import asyncio
from typing import NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.sqlite import SqliteStore

from errors import identify_module, should_retry
#导入长期记忆（用户喜好）模块
from memory.Memory import (
    get_user_preferences,
    remove_user_preference,
    save_user_preferences,
)
from memory.cold_zone import add_cold_zone
#导入短期记忆模块
from memory.plan_manager import (
    append_plan_version,
    confirm_plan_version,
    get_all_plan_keys,
    get_city_trip_count,
    get_latest_plan,
    get_plan_by_key,
    get_plan_key_by_city,
    get_plan_key_by_offset,
    get_plan_keys_by_city,
    list_plan_versions,
    save_plan,
    update_plan_summary,
)
#导入中间件
from middleware import (
    clean_expired_cold_zone_middleware,
    fix_orphaned_tool_calls_middleware,
    inject_cold_zone_middleware,
)
from models import model, qwen_sw_f
from prompts import SYSTEM_PROMPT
from tools import get_amap_tools, get_html_tools, web_search

#定义自定义状态
class CustomState(AgentState):
    user_id: NotRequired[str]               # 用户ID唯一标识
    current_plan_key: NotRequired[str]       # 当前计划key
    _cold_zone_injected: NotRequired[bool]   # 是否已注入冷区

#异步运行 主函数
async def main() -> None:
    amap_tools = await get_amap_tools()
    html_tools = await get_html_tools()
    tools = [
        *html_tools,
        *amap_tools,
        web_search,
        save_user_preferences,
        remove_user_preference,
        get_user_preferences,
        get_all_plan_keys,
        get_plan_keys_by_city,
        get_plan_key_by_city,
        get_plan_key_by_offset,
        get_city_trip_count,
        get_latest_plan,
        save_plan,
        get_plan_by_key,
        update_plan_summary,
        append_plan_version,
        confirm_plan_version,
        list_plan_versions,
        add_cold_zone,
    ]
    # 创建工具选择中间件
    tool_selector = LLMToolSelectorMiddleware(
        max_tools=5,
        model=qwen_sw_f,
        system_prompt="请根据用户问题，选择最相关的旅行规划工具。",
    )
    #在文件管理器中创建agent
    with SqliteStore.from_conn_string("./memory/测试.db") as store:
        agent = create_agent(
            model=model,
            store=store,
            tools=tools,
            checkpointer=InMemorySaver(),
            middleware=[
                inject_cold_zone_middleware,
                clean_expired_cold_zone_middleware,
                fix_orphaned_tool_calls_middleware,
                tool_selector,
            ],
            state_schema=CustomState,
            system_prompt=SYSTEM_PROMPT,
        )
        await chat_loop(agent)

#聊天循环
async def chat_loop(agent) -> None:
    config = {"configurable": {"thread_id": "user_1"}}
    while True:
        user_message = input("用户（输入 esc 退出）：")
        if user_message == "esc":
            break
        #异常重试机制
        for attempt in range(3):
            #捕获异常
            try:
                if attempt == 0:    
                    input_data = {"messages": [HumanMessage(content=user_message)],
                                  "user_id": "user_1",
                                  "current_plan_key": None, # 当前计划key
                                  "_cold_zone_injected": False, # 是否已注入冷区
                                }                
               
                else:
                    input_data =  {"user_id": "user_1",}
                run = await agent.ainvoke(input_data, config=config)
                print(run["messages"][-1].content)
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
                    print(f"[{module}] 不可重试的错误：{str(e).upper()}")
                    raise


if __name__ == "__main__":
    asyncio.run(main())
