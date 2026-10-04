# 中间件


from langchain_core.messages import AIMessage, ToolMessage
#导入冷区模块
from memory.cold_zone import _get_cold_zone_raw,clean_expired_cold_zone
from langchain.agents.middleware import before_model,after_agent
import asyncio
from logger import logger
#添加冷区到提示词
@before_model
def inject_cold_zone_middleware(state, runtime):
    try:
        if state.get("_cold_zone_injected"):
            return None
        user_id = state.get("user_id")
        if not user_id:
            logger.error("中间件加载冷区：错误：缺少用户 ID")
            return None
        cold_zone = _get_cold_zone_raw(runtime.store, user_id)
        if cold_zone:
            return {
                "system_prompt": state.get("system_prompt", "") + "\n" + cold_zone,
                "_cold_zone_injected": True
            }
    except Exception as e:
        logger.error(f'冷区无法注入，不影响主流程。{e}')
    return None

#清除过期冷区
@after_agent
async def clean_expired_cold_zone_middleware(state, runtime):
    try:
        user_id = state.get("user_id")
        if not user_id:
            logger.error("中间件清理冷区：错误：缺少用户 ID")
            return None
        await asyncio.to_thread(
            clean_expired_cold_zone,
            runtime.store,
            user_id
        )
    except Exception as e:
         logger.error(f'冷区无法清除，不影响主流程。')
    return None
#消息补全
@before_model
def fix_orphaned_tool_calls_middleware(state, runtime):
    try:
        messages = state.get("messages", [])
        if not messages:
            return None
        responded_tool_call_ids = set()
        for msg in messages:
            if isinstance(msg, ToolMessage):
                responded_tool_call_ids.add(msg.tool_call_id)

        placeholders = []
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc["id"] not in responded_tool_call_ids:
                        logger.warning(f"发现孤立的 tool_call: {tc['id']} ({tc.get('name', '?')})，自动补全 ToolMessage")
                        placeholders.append(ToolMessage(
                            content="该工具调用未能完成，请忽略此次调用并继续。",
                            tool_call_id=tc["id"]
                        ))
                        responded_tool_call_ids.add(tc["id"])

        if placeholders:
            return {"messages": placeholders}
    except Exception as e:
        logger.error(f"消息修复中间件异常，不影响主流程: {e}")
    return None