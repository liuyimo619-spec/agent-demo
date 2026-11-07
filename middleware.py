# 中间件
from dataclasses import replace
from datetime import datetime

from langchain_core.messages import AIMessage, ToolMessage,RemoveMessage,HumanMessage
from langchain.agents.middleware import before_model,after_agent,wrap_model_call, wrap_tool_call,dynamic_prompt
from logger import logger
from memory.checkpointer import checkpointer
from typing import Annotated
from pydantic import Field
from langchain.agents import AgentState
from langgraph.types import Command
from models import model_no_think
from langgraph.graph import END
from prompts import SYSTEM_PROMPT
SOFT_WARNING_STEP = 45    # 软警告线：开始提示限制工具
CRITICAL_THRESHOLD_STEP = 50 # 临界阈值线：系统警告已超阈值
HARD_LIMIT_STEP = 52      # 硬熔断线：强制结束进程
TOOL_LIMIT = {"maps_geo": 6,"maps_text_search": 8,"maps_around_search": 4,
              "maps_direction_walking": 4,"maps_distance": 3,"maps_search_detail": 4,"maps_weather": 2,
}
_SENTINELS = {"none", "null", "无", "未指定"}
_CLEAN_PARAMS = {"city", "category"} 
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


# 历史消息截断 上限20条用户历史消息 
# 模型调用前处理
@before_model
def trim_history_middleware(state, runtime):
    try:
        messages = state.get("messages", [])
        human_mgs= 0
        for message in messages:
            if isinstance(message, HumanMessage):
                human_mgs += 1
        #获取保存下的消息id
        if human_mgs <=20:
            return None
        hu_mgs = 0
        kept_ids = set()
        for message in reversed(messages):
            kept_ids.add(message.id)
            if isinstance(message, HumanMessage):
                hu_mgs += 1
            if hu_mgs == 20:
                break
        removed = [RemoveMessage(id=msg.id) for msg in messages if msg.id not in kept_ids] #指定删除条数
        return {"messages": removed} if removed else None   #返回删除后的消息
    except Exception as e:
        logger.error(f"历史消息截断中间件异常，不影响主流程: {e}")

#内存记忆清除中间件
@after_agent
async def clean_memory_after_confirm_middleware(state, runtime):
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

#计划表中间件,构建aimessage 计划表由前端处理次aimessage信息拿到计划表
@after_agent
def append_plan_card_middleware(state, runtime):
    try:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                break
            if isinstance(msg, ToolMessage) and msg.name == 'save_plan':
                artifact = getattr(msg, "artifact", {}) or {}
                markdown = artifact.get('markdown')
                if not markdown:
                    logger.warning("计划表中间件: save_plan 缺少计划表内容，跳过计划卡")
                    return None
                return Command(update={'messages': [AIMessage(content='请看计划表',additional_kwargs={"render_type": "plan_card","markdown": markdown})]})
        return None
    except Exception as e:
        logger.error(f"计划表中间件异常，此次前端无法获取计划表: {e}")
        return None

#工具选择+超步上限+关闭思考中间件
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
        visible_tools = []
        #获取已加载的工具名称列表
        allowed = set(request.state.get('loaded_tools', []))
        allowed.add('select_tools'); allowed.add('save_plan')  #保证select_tools和save_plan的详细存在
        #获取已加载的工具名称列表
        visible_tools = [item for item in request.tools if item.name in allowed] #这里的 tools 存放的是已经挂载的tool 和 下面 tool 不是一个东西
        #save_plan后关闭思考模式。
        try:
            messages = request.state.get('messages',[])
            if isinstance(messages[-1],ToolMessage) and messages[-1].name == 'save_plan':
                logger.info("save_plan 已完成，本次模型调用关闭思考")
                return await handler(request.override(model=model_no_think,tools=visible_tools))
        except Exception as e:
            logger.error(f"关闭思考模式中间件异常: {e}")
        #超步上限设计
        try:
            step_count = request.state.get('step_count',0)
            if step_count > SOFT_WARNING_STEP:
                visible_tools = [item for item in visible_tools if item.name == 'save_plan']
            return await handler(request.override(tools=visible_tools))
        except Exception as e:
            logger.error(f"超步上限中间件异常: {e}")
        
        return await handler(request.override(tools=visible_tools))
    except Exception as e:
        logger.error(f"工具选择中间件异常，仅剩'未注册工具拦截加载中间件'兜底: {e}")
        return await handler(request.override(tools=visible_tools))
#未注册工具拦截加载 + 超步限制 + 工具调用上限中间件
def merge_tool_counts(current,incoming):
    current = dict(current or {})
    for k,v in incoming.items():
        current[k] = current.get(k,0) + v
    return current
@wrap_tool_call
async def auto_load_tool_middleware(request, handler):
    try:
        #--------None默认值替换---------
        try:
            args = request.tool_call.get('args',{})
            if isinstance(args,dict):
                cleaned,dropped = {},[]
                for k,v in args.items():
                    if k in _CLEAN_PARAMS and isinstance(v,str) and v.strip().lower() in _SENTINELS:
                        dropped.append(k)
                        continue
                    cleaned[k] = v
                if dropped:
                    logger.warning(
                        f"工具入参清洗: {request.tool_call.get('name')} 删除哨兵参数 {dropped}"
                    )
                    request = request.override(tool_call={**request.tool_call, "args": cleaned})
        except Exception as e:
            logger.error(f"None默认值替换中间件异常: {e}")
        #--------超步限制中间件---------
        step_count = request.state.get("step_count") or 0
        if step_count >= HARD_LIMIT_STEP:
            logger.warning(f"Agent 触发超步硬上限：{step_count} 步，强制中断！")
            return Command(update={"messages": [ToolMessage(content='超出步数上限，调用失败',status="error",tool_call_id=request.tool_call['id'])]})
        #查看request.tool内部是否存在工具名称，不存在丢给框架处理
        if request.tool is None:    #框架会把这次请求的工具名称进行判断是否挂载，如果挂载就会存放到request.tool中
            return await handler(request)
        #--------未注册工具拦截加载中间件---------
        #工具存在时的处理
        allowed = set(request.state.get('loaded_tools', []))
        allowed.add('select_tools');allowed.add('save_plan')    #保证select_tools的详细存在
        #捕获第一次工具调用，构建toolmessage并将对应工具名称保存在loaded_tools中
        just_loaded = []
        if request.tool_call['name'] not in allowed:
            just_loaded = [request.tool_call['name']] #添加对应的工具信息
            logger.info(f"未加载直接执行: {request.tool_call['name']}") #记入日志
        
        #--------工具调用上限检测---------
        name = request.tool_call['name']
        counts = request.state.get('tool_counts',{})
        cap = TOOL_LIMIT.get(name,3)
        if counts.get(name,0) >= cap:
            return Command(update={"messages":[ToolMessage(content=f"{name} 本轮已调用 {cap} 次，已达上限，本次未执行。"
                                                            f"不要换参数重试本工具；需要其他信息请改用别的工具，"
                                                            f"信息足够后调用 save_plan，无法确认的内容在 remarks 注明“以现场为准”。",
                                                            tool_call_id=request.tool_call['id'],status="error")]})
        result = await handler(request)
        delta = {"tool_counts":{name:1}}  # 工具调用次数+1 tool_counts字段挂载了合并器，会自动+1 name代表工具名称 +1 代表调用次数+1
        if just_loaded: #如果just_loaded不为空，则添加工具名称
            delta["loaded_tools"] = just_loaded
        if isinstance(result,Command): # 判断类型
            return replace(result, update={**(result.update or {}), **delta}) #因为Command是替换不是修改，所以要先拿出进行修改再进行update
        
        return Command(update={"messages": [result], **delta}) 
    except Exception as e:
        logger.error(f"自动加载工具中间件异常，仅剩'工具选择中间件+工具'兜底: {e}")
        return Command(update={"messages": [ToolMessage(content='工具调用失败此工具禁止再调用',status="error",tool_call_id=request.tool_call['id'])]})
#超步上限计数器中间件
@before_model(can_jump_to=["end"])
async def track_step_count_middleware(state, runtime):
    try:
        step_count = state.get("step_count")
        if step_count is None:
            logger.error("超步上限中间件: 缺少 step_count 状态(已补全)")
            return {'step_count': 0}
        if step_count >= HARD_LIMIT_STEP:
            logger.warning(f"Agent 触发超步硬上限：{step_count} 步，强制中断！")
            return Command(update={"jump_to": "end","messages": [AIMessage(name="step_limit_notice",content="抱歉，系统思考超时，请重新询问。")],"step_count": step_count},goto=END)
        step_count += 1
        return {'step_count': step_count}
    except Exception as e:
            logger.error(f"超步上限计数器异常，超步上限可能失效: {e}")
            return None
#超步限制中间件


@dynamic_prompt
async def inject_step_warning_middleware(request):
    try:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        now = datetime.now()
        time_str = now.strftime("%Y-%m-%d") + " " + weekdays[now.weekday()]
        step_count = request.state.get("step_count") or 0
        if step_count < CRITICAL_THRESHOLD_STEP and step_count > SOFT_WARNING_STEP:
            step = CRITICAL_THRESHOLD_STEP - step_count
            return f"[唯一可信系统时间：{time_str}]{SYSTEM_PROMPT}。警告！剩余超步仅剩{step}步，禁止再调用除 save_plan 以外的工具。如超出系统阈值则出现系统故障。"
        if step_count <= SOFT_WARNING_STEP:
            return f'[唯一可信系统时间：{time_str}]{SYSTEM_PROMPT}'
        if step_count >= CRITICAL_THRESHOLD_STEP and step_count <= HARD_LIMIT_STEP:
            return f"[唯一可信系统时间：{time_str}]{SYSTEM_PROMPT}。警告！已超过系统阈值，请勿再调用除 save_plan 以外的工具。如超出系统阈值则出现系统故障。"
    except Exception as e:
            logger.error(f"超步限制中间件异常，超步上限可能失效: {e}")
            return None
