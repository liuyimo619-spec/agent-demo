import asyncio
import json
from typing import Annotated, NotRequired
from langgraph.types import Overwrite
from langchain.agents import AgentState, create_agent
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
    merge_tool_counts, #合并工具调用计数,不是中间件是函数
    trim_history_middleware,
    auto_load_tool_middleware,
    clean_memory_after_confirm_middleware,
    expose_selected_tools,
    track_step_count_middleware,
    inject_step_warning_middleware,
    append_plan_card_middleware
)
from models import model
from prompts import SYSTEM_PROMPT
from tools import  get_amap_tools, web_search,build_tool_selector
import os
from dotenv import load_dotenv

load_dotenv(override=True)
DASHSCOPE_MODEL_ID = os.getenv("DASHSCOPE_MODEL_ID")
CHAT_TOTAL_TIMEOUT = float(os.getenv("CHAT_TOTAL_TIMEOUT", 300))
CHAT_IDLE_TIMEOUT = float(os.getenv("CHAT_IDLE_TIMEOUT", 60))

#定义自定义状态
class CustomState(AgentState):
    user_id: NotRequired[str]               # 用户ID唯一标识
    current_plan_key: NotRequired[str]       # 当前计划key
    step_count: NotRequired[int]               # 当前步骤数
    tool_counts: Annotated[dict, merge_tool_counts]

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
            clean_memory_after_confirm_middleware,
            auto_load_tool_middleware,
            tool_selector,
            track_step_count_middleware,
            inject_step_warning_middleware,
            append_plan_card_middleware
        ],
        state_schema=CustomState,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent

#计划表卡片在流式文本末尾的标记，前端据此切分并渲染
PLAN_CARD_MARKER = "\n\n[[PLAN_CARD]]\n"

#聊天入口+异常重试
async def chat_loop(agent,user_message,user_id):
    # 配置项
    #user_id为用户ID，限制最高50步超步，约 11 轮工具往返
    config = {"configurable": {"thread_id": user_id}}
    input_data = {"messages": [HumanMessage(content=user_message)],
                          "user_id": user_id,
                          "step_count":0,  # 当前超步数 默认为0 上限52
                          "tool_counts": Overwrite({}),   # 工具调用计数，Overwrite 允许覆盖 默认值，每次运行都会被重置为0也就是空{}
                        }   
    #异常重试机制
    for attempt in range(3):
        stream = None
        #捕获异常
        try:                   
            async with asyncio.timeout(CHAT_TOTAL_TIMEOUT):
                # 开始对话
                stream = agent.astream(input_data, config=config,stream_mode = 'messages')
                while True:
                    try:
                        # 看门狗
                        item = await asyncio.wait_for(anext(stream), CHAT_IDLE_TIMEOUT)
                    except StopAsyncIteration:  # 迭代完成
                        logger.error(f'看门狗超时')
                        break
                    #使用开门狗后拿到的数据（源数据已被开门狗消耗所以从开门狗那里调用）
                    message_chunk,metadata = item
                    #计划表由中间件生成的 AIMessage 携带，不能混进普通文本
                    additional = getattr(message_chunk, "additional_kwargs", None) or {}
                    if additional.get("render_type") == "plan_card":
                        #渲染计划表
                        markdown = additional.get("markdown")
                        if markdown:
                            yield PLAN_CARD_MARKER + json.dumps(
                                {"render_type": "plan_card", "markdown": markdown},
                                ensure_ascii=False,
                            )
                        continue
                    #只返回.content信息
                    if metadata.get("ls_model_name") != DASHSCOPE_MODEL_ID and message_chunk.name != "step_limit_notice":
                        continue
                    text = message_chunk.text
                    if text:
                        yield text
                break
        except TimeoutError:
            # 对话超时
            logger.error(f"对话超时中止 user_id: {user_id}"f"(总时长≤{CHAT_TOTAL_TIMEOUT}s, 空闲≤{CHAT_IDLE_TIMEOUT}s)")
            yield '本次处理超时，已中止，请重试或把需求拆小一点。'
            break
        except asyncio.CancelledError:
            # 客户端断开
            logger.warning(f"user_id:{user_id}对话被取消")
            raise
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
        #强制关闭agent流，避免客户端关闭而后端工作继续运行
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except BaseException:
                    pass
