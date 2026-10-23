# 中间件
from langchain_core.messages import AIMessage, ToolMessage,RemoveMessage,trim_messages
from langchain.agents.middleware import before_model,after_agent
from logger import logger
#from agent import checkpointer

#消息补全
#模型调用前处理
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


# 历史消息截断 上限40条 
# 模型调用前处理
@before_model
def trim_history_middleware(state, runtime):
    try:
        messages = state.get("messages", [])
        if len(messages) <= 40:
            return None
        kept = trim_messages(
            messages, 
            max_tokens=40,                          #借 token 参数装条数
            token_counter=lambda msgs: len(msgs),   #计数器偷换为条数
            strategy='last',                         #保尾部
            start_on='human'                        #从用户开始
        )
        kept_ids = [msg.id for msg in kept]        #获取保存下的消息id
        removed = [RemoveMessage(id=msg.id) for msg in messages if msg.id not in kept_ids] #指定删除条数
        return {"messages": removed} if removed else None   #返回删除后的消息
    except Exception as e:
        logger.error(f"历史消息截断中间件异常，不影响主流程: {e}")

#内存记忆清除中间件
#@after_agent
#async def tool_call_middleware(state, runtime):
#    try:
#       messages = state.get("messages", [])
#       if not messages:
#           return None
#       user_id = state.get("user_id")
#       if not user_id:
#           logger.error("清除内存记忆: 缺少用户 ID")
#       #判断工具名称
#       for msg in reversed(messages):
#           if isinstance(msg, ToolMessage):
#               if msg.name == 'confirm_plan_version' and msg.content == '添加成功':
#                   await checkpointer.adelete_thread(user_id)
#                   break
#       return None
#   except Exception as e:
#       logger.error(f"清除内存记忆中间件异常，不影响主流程: {e}")