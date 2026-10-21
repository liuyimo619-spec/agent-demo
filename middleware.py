# 中间件
from langchain_core.messages import AIMessage, ToolMessage
#导入冷区模块
from langchain.agents.middleware import before_model
from logger import logger
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