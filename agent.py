import asyncio
from typing import NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain_core.messages import HumanMessage
#导入读取冷区模块
from logger import logger
from memory.cold_zone import get_cold_zone_raw

from errors import identify_module, should_retry
#导入长期记忆（用户喜好）模块
from memory.Memory import (
    get_user_preferences,
    remove_user_preference,
    save_user_preferences,
)
#导入短期记忆模块
from memory.plan_manager import (
    confirm_plan_version,
    get_all_plan_id,
    get_city_trip_count,
    get_latest_plan,
    get_plan_summaries,
    get_plan_id_by_city,
    get_plan_key_by_offset,
    list_plan_ids_by_city,
    get_latest_plan_summary,
    list_plan_versions,
    get_plan_by_plan_id,
    get_latest_workspace_plan,
    list_workspace_plans,
    save_plan,
    delete_plan,
)
#导入中间件
from middleware import (
    fix_orphaned_tool_calls_middleware,
    trim_history_middleware,
    auto_load_tool_middleware,
    tool_call_middleware,
    expose_selected_tools
)
from models import model
from prompts import SYSTEM_PROMPT
from tools import  get_amap_tools, web_search,build_tool_selector

#定义自定义状态
class CustomState(AgentState):
    user_id: NotRequired[str]               # 用户ID唯一标识
    current_plan_key: NotRequired[str]       # 当前计划key


#异步运行 agent工厂函数
async def create_agent_instance(checkpointer):
    amap_tools = await get_amap_tools()
    tools = [
        *amap_tools,
        web_search,
        save_user_preferences,
        remove_user_preference,
        get_user_preferences,
        get_all_plan_id,
        list_plan_ids_by_city,
        get_plan_id_by_city,
        get_plan_key_by_offset,
        get_city_trip_count,
        get_latest_plan,
        save_plan,
        get_plan_summaries,
        get_latest_plan_summary,
        get_plan_by_plan_id,
        get_latest_workspace_plan,
        list_workspace_plans,
        delete_plan,
        confirm_plan_version,
        list_plan_versions,
        get_cold_zone_raw
    ]
    selection_tool = build_tool_selector(tools)
    tools.append(selection_tool)
    # 创建工具选择中间件
    tool_selector = expose_selected_tools
    # 创建 agent（原文件管理器创建agent）
    agent = create_agent(
        model=model,
        tools=tools,
        checkpointer=checkpointer,
        middleware=[
            fix_orphaned_tool_calls_middleware,
            trim_history_middleware,
            tool_call_middleware,
            auto_load_tool_middleware,
            tool_selector,
        ],
        state_schema=CustomState,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent

#聊天入口+异常重试
async def chat_loop(agent,user_message,user_id):
    # 配置项
    #user_id为用户ID，限制最高50步超步，约 11 轮工具往返
    config = {"configurable": {"thread_id": user_id},"recursion_limit": 50}
    input_data = {"messages": [HumanMessage(content=user_message)],
                          "user_id": user_id,
                        }   
    #异常重试机制
    for attempt in range(3):
        #捕获异常
        try:                   
            async for message_chunk, metadata in agent.astream(input_data, config=config,stream_mode='messages'):
                #只返回.content信息
                if metadata.get("ls_model_name") != "qwen3.7-plus-2026-05-26":
                    continue
                text = message_chunk.text
                if text:
                    yield text
            break
        #异常处理
        except Exception as e:
            module = identify_module(e)
            # 判断是否可重试
            if should_retry(e):
                if attempt == 2:
                    logger.error(f"[{module}] API 连续3次重试失败，请稍后再试...")
                    yield f'部分功能请求超时，请稍后再试...'
                    break
                # 可重试的错误
                wait = 2 ** (attempt + 1)
                logger.warning(f"[{module}] API 限流或错误，{wait}秒后自动重试...")
                await asyncio.sleep(wait)
                
            else:
                # 不可重试的错误
                exce = f"[{module}] 不可重试的错误：{str(e).upper()}"
                logger.error(exce)
                yield f'错误：{e}'
                break