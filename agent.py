import asyncio
from typing import NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
#导入读取冷区模块
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
)
from models import model, qwen_sw_f
from prompts import SYSTEM_PROMPT
from tools import get_amap_tools, web_search

#定义自定义状态
class CustomState(AgentState):
    user_id: NotRequired[str]               # 用户ID唯一标识
    current_plan_key: NotRequired[str]       # 当前计划key

#异步运行 agent工厂函数
async def create_agent_instance(store) -> None:
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
    # 创建工具选择中间件
    tool_selector = LLMToolSelectorMiddleware(
        max_tools=5,
        model=qwen_sw_f,
        system_prompt="请根据用户问题，选择最相关的旅行规划工具。",
    )
    # 创建 agent（原文件管理器创建agent）
    agent = create_agent(
        model=model,
        tools=tools,
        store=store,
        checkpointer=InMemorySaver(),
        middleware=[
            fix_orphaned_tool_calls_middleware,
            tool_selector,
        ],
        state_schema=CustomState,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent

#聊天入口+异常重试
async def chat_loop(agent,user_message,user_id):
    config = {"configurable": {"thread_id": user_id}}
    input_data = {"messages": [HumanMessage(content=user_message)],
                          "user_id": user_id,
                          "current_plan_key": None, # 当前计划key,暂未闭环且不清楚当初为何设计为何烂尾
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
                # 可重试的错误
                wait = 2 ** (attempt + 1)
                print(f"[{module}] API 限流或错误，{wait}秒后自动重试...")
                await asyncio.sleep(wait)
            else:
                # 不可重试的错误
                exce = f"[{module}] 不可重试的错误：{str(e).upper()}"
                yield exce