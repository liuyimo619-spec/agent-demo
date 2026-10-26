# 中间件
from langchain_core.messages import AIMessage, ToolMessage,RemoveMessage,trim_messages,HumanMessage
from langchain.agents.middleware import before_model,after_agent,wrap_model_call, wrap_tool_call
from logger import logger
from memory.checkpointer import checkpointer
from typing import Annotated
from pydantic import Field
from langchain.agents import AgentState
from langgraph.types import Command

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


# 历史消息截断 上限40条用户历史消息 
# 模型调用前处理
@before_model
def trim_history_middleware(state, runtime):
    try:
        messages = state.get("messages", [])
        human_mgs= 0
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                human_mgs += 1
        #获取保存下的消息id
        if human_mgs <=40:
            return None
        hu_mgs = 0
        kept_ids = set()
        for message in reversed(messages):
            kept_ids.add(message.id)
            if isinstance(message, HumanMessage):
                hu_mgs += 1
            if hu_mgs == 40:
                break
        removed = [RemoveMessage(id=msg.id) for msg in messages if msg.id not in kept_ids] #指定删除条数
        return {"messages": removed} if removed else None   #返回删除后的消息
    except Exception as e:
        logger.error(f"历史消息截断中间件异常，不影响主流程: {e}")

#内存记忆清除中间件
@after_agent
async def tool_call_middleware(state, runtime):
    try:
       messages = state.get("messages", [])
       if not messages:
           logger.error("清除内存记忆: 缺少 messages 实例")
           return None
       user_id = state.get("user_id")
       if not user_id:
           logger.error("清除内存记忆: 缺少用户 ID")
           return None
       #判断工具名称
       for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
               if msg.name == 'confirm_plan_version' and msg.content == '添加成功':
                   await checkpointer.adelete_thread(user_id)
                   break
            if isinstance(msg, HumanMessage):
               break
       return None
    except Exception as e:
       logger.error(f"清除内存记忆中间件异常，不影响主流程: {e}")

#工具选择中间件
#累计工具名称并去重
def merge_tool_names(current:list[str],incoming:list[str])->list[str]:
    #current:当前工具名称列表
    #incoming:传入的工具名称列表
    return list(dict.fromkeys(current+incoming))

class ToolSelectionState(AgentState):
    loaded_tools:Annotated[list[str], merge_tool_names]
@wrap_model_call(state_schema=ToolSelectionState)
async def expose_selected_tools(request,handler):
    try:
        #获取已加载的工具名称列表
        allowed = set(request.state.get('loaded_tools', []))
        allowed.add('select_tools')  #保证select_tools的详细存在
        #获取已加载的工具名称列表
        visible_tools = [item for item in request.tools if item.name in allowed] #这里的 tools 存放的是已经挂载的tool 和 下面 tool 不是一个东西
        return await handler(request.override(tools=visible_tools))
    except Exception as e:
        logger.error(f"工具选择中间件异常，仅剩'未注册工具拦截加载中间件'兜底: {e}")

@wrap_tool_call
async def auto_load_tool_middleware(request, handler):
    try:
        #查看request.tool内部是否存在工具名称，不存在丢给框架处理
        if request.tool is None:    #框架会把这次请求的工具名称进行判断是否挂载，如果挂载就会存放到request.tool中
            return await handler(request)
        #工具存在时的处理
        allowed = set(request.state.get('loaded_tools', []))
        allowed.add('select_tools')    #保证select_tools的详细存在
        #捕获第一次工具调用，构建toolmessage并将对应工具名称保存在loaded_tools中
        if request.tool_call['name'] not in allowed:
            allowed.add(request.tool_call['name']) #添加对应的工具信息
            #构建返回结果
            return Command(update={
                'loaded_tools': [request.tool_call['name']],
                'messages': [ToolMessage(content='工具调用失败，已修复:'+request.tool_call['name']+'。必须重新调用该工具来完成对应的操作',tool_call_id=request.tool_call['id'],status="error")],
            }
            )
        return await handler(request)
    except Exception as e:
        logger.error(f"自动加载工具中间件异常，仅剩'未注册工具拦截加载中间件'兜底: {e}")