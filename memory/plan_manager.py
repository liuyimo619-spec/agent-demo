#查早key
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import StructuredTool
from typing import Optional
from langgraph.prebuilt import ToolRuntime
# memory/cold_zone.py
import sys
import os
import re
_URL_RE = re.compile(r'https?://\s+')
# 把项目根目录加入 Python 的模块搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger import logger
def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"记忆系统出错，不影响主进程，请明确告知用户（异常：{type(error).__name__} - {str(error)}）"

def _get_summary_model(runtime):
    if runtime.context is not None:
        model = runtime.context.get('model')
        if model:
            return model
    from models import qwen_sw_f
    return qwen_sw_f
class SavePlanInput(BaseModel):
    city: str = Field(
        description="旅行目的地城市名，如 '北京'、'上海'"
    )
    plan_data: Dict[str, Any] = Field(
        description="完整的旅行计划数据，按日期组织。"
                    "示例格式："
                    "{'day1': {'上午': '故宫(费用)', '下午': '天坛（费用）','晚上':'长城（费用）'}, "
                    "'day2': {'上午': '故宫(费用)', '下午': '天坛（费用）','晚上':'长城（费用）'}, "
                    "'总费用': '500元', "
                    "'备注': '无'}"
    )
    summary: Optional[str] = Field(
        default=None,
        description="可选摘要，如果不提供，系统会自动生成"
    )
class plan_data(BaseModel):
    plan_key: str = Field(
        description="要追加版本的旅行计划 key，请先通过 key 工具查找"
    )
    plan_data: Dict[str, Any] = Field(
        description="完整的旅行计划数据，按日期组织。"
                    "示例格式："
                    "{'day1': {'上午': '故宫(费用)', '下午': '天坛（费用）','晚上':'长城（费用）'}, "
                    "'day2': {'上午': '故宫(费用)', '下午': '天坛（费用）','晚上':'长城（费用）'}, "
                    "'总费用': '500元', "
                    "'备注': '无'}"
    )
#查找用户所有的 key
def _get_all_plan_keys(runtime:ToolRuntime)->list:
    """
    查询用户所有旅行地的key
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("查找用户key: 缺少用户 ID")
        return []
    store = runtime.store
    namespace = ('users', user_id)
    key = "plan_key_list"
    existing = store.get(namespace, key)
    if existing:
        return existing.value
    else:
        return []


#根据城市查找该城市的所有key
def _get_plan_keys_by_city(runtime:ToolRuntime,city:str)->list:
    '''
    根据城市名称查询该城市的所有key

    Args:
        retime:工具的运行时
        city:查询的城市名称
    Return:返回列表
    '''
    lis = _get_all_plan_keys(runtime)
    # 设置当前计划key
    return [key for key in lis if key.startswith(city + '_')]


#固定城市模糊次数
def _get_plan_key_by_city(runtime:ToolRuntime,city:str,offset:int=-1)->str:
    '''
    根据城市名称查询该城市对应的key

    Args:
        runtime:工具的运行时
        city:查询的城市名称
        offset:偏移量，默认为-1，表示返回该城市最 Cold_zone_time 最新的key
    Return:返回key/报错信息
    '''
    lis = _get_plan_keys_by_city(runtime,city)
    if not lis:
        return f'未找到{city}的旅行计划'
    try:
        return lis[offset]
    except IndexError:
        logger.error(f"固定城市模糊次数: 偏移量超出范围")
        return f'偏移量 {offset} 超出范围，总计划数为 {len(lis)}，可用偏移量范围为 -{len(lis)} 到 -1'

get_plan_key_by_city = StructuredTool.from_function(
    func=_get_plan_key_by_city,
    name="get_plan_key_by_city",
    description="根据城市名称查询该城市对应的key",
    handle_tool_errors=tool_error_handler
)

#模糊旅行次数
def _get_plan_key_by_offset(runtime:ToolRuntime,offset:int=-1) ->str:
    '''
    根据偏移量查询旅行计划

    Args:
        runtime:工具的运行时
        offset:偏移量，默认为-1，表示返回最 Cold_zone_time 最新的key
    Return:返回key/报错信息
    '''
    lis = _get_all_plan_keys(runtime)
    if not lis:
        return '未找到旅行计划'
    try:
        return lis[offset]
    except IndexError:
        logger.error(f"模糊旅行次数: 偏移量超出范围")
        return f'偏移量 {offset} 超出范围，总计划数为 {len(lis)}，可用偏移量范围为 -{len(lis)} 到 -1'

get_plan_key_by_offset = StructuredTool.from_function(
    func=_get_plan_key_by_offset,
    name="get_plan_key_by_offset",
    description="根据偏移量查询旅行计划",
    handle_tool_errors=tool_error_handler
)

#查询去过某城市多少次
def _get_city_trip_count(runtime:ToolRuntime,city:str) -> int:
    '''
    查询去过某城市多少次

    Args:
        runtime:工具的运行时
        city:查询的城市名称
    Return:返回次数
    '''
    lis = _get_plan_keys_by_city(runtime,city)
    return len(lis)

get_city_trip_count = StructuredTool.from_function(
    func=_get_city_trip_count,
    name="get_city_trip_count",
    description="查询去过某城市多少次",
    handle_tool_errors=tool_error_handler
)


#查询最新的旅行计划
def _get_latest_plan(runtime:ToolRuntime)->str:
    '''
    查询最新的旅行计划
    '''
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("查询最新旅行计划: 缺少用户 ID")
        return '缺少用户 ID'
    lis = _get_all_plan_keys(runtime)
    if not lis:
        return '未找到旅行计划'
     # 读取该计划的摘要
    store = runtime.store
    latest_key = lis[-1]
    namespace_data = ('users', user_id, 'plan')
    plan_data = store.get(namespace_data, latest_key)

    if plan_data:
        summary = plan_data.value.get('summary')
        if summary:
            # 如果有摘要（进行中的计划），设为当前计划
            runtime.state["current_plan_key"] = latest_key
    return latest_key

get_latest_plan = StructuredTool.from_function(
    func=_get_latest_plan,
    name="get_latest_plan",
    description="查询最新的旅行计划",
    handle_tool_errors=tool_error_handler
)




#短期记忆
#内部函数
#向列表中追加新key
def _append_plan_key(store, user_id, new_key)->str:
    namespace = ('users', user_id)
    key = "plan_key_list"
    existing = store.get(namespace, key)
    if existing:
        lis = existing.value
        lis.append(new_key)
        store.put(namespace, key, lis)
        return f'已添加旅行计划：{new_key}'
    else:
        lis = [new_key]
        store.put(namespace, key, lis)
        return f'已添加旅行计划：{new_key}'
#计算下一个可用的key
def _generate_plan_key(store, user_id, city)->str:
    namespace = ('users', user_id)
    key = "plan_key_list"
    existing = store.get(namespace, key)
    if existing:
        lis = existing.value
        if city + '_1' in lis:
            for i in range(2, 1000):
                if city + f'_{i}' not in lis:
                    return city + f'_{i}'
            logger.error(f"计算下一个可可用key: 列表已满,注意恶意攻击")
            return f'用户去过{city}城市1000次了超越系统记录'
    else:
        return f'{city}_1'
#摘要函数
def generate_summary(messages:list, model)->str:
    """
    生成摘要

    Args:
        messages:消息列表
        model:传入模型
    Return:返回摘要
    """
    raw_text= "\n".join([msg.content for msg in messages if isinstance(msg,(HumanMessage,AIMessage)) and isinstance(msg.content, str)])
    conversation_text = _URL_RE.sub('', raw_text)
    prp = f"请用中文总结以下对话的核心内容，提取关键信息（如目的地、预算、天数、住宿条件、用户表明哪里不需要/不去/禁止等，不需要记入计划表）：\n{conversation_text}"
    messages = model.invoke(prp)
    return messages.content

#创建短期记忆
def _save_plan(runtime:ToolRuntime ,city:str,plan_data: dict,summary:Optional[str] = None):
    """
    创建短期记忆

    Args:
        runtime:工具的运行时
        city:城市名称
        plan_data:计划数据json格式
        summary:全部的历史消息,可不写，内部有摘要函数调用
    Return:返回操作结果
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("写入短期记忆： 缺少用户 ID")
        return "错误：缺少用户 ID"
    plan_key = _generate_plan_key(runtime.store, user_id, city)
    if not summary:
        summary_model = _get_summary_model(runtime)
        messages = runtime.state.get('messages')
        if not messages:
            return "错误：缺少messages实例"
        summary = generate_summary(messages, summary_model)
    value = {
        "plan_data": plan_data,
        "summary": summary,
    }
    runtime.store.put(('users', user_id, 'plan'), plan_key, value)
    _append_plan_key(runtime.store, user_id, plan_key)
    #设置当前计划key
    runtime.state["current_plan_key"] = plan_key
    return f'已保存旅行计划：{plan_key}'
save_plan = StructuredTool.from_function(
    func=_save_plan,
    name="save_plan",
    description="""创建旅行计划到短期记忆。
                接收目的地城市、完整的计划数据（JSON格式），自动生成摘要并存储。
                计划数据应包含每日行程（按时间段分组）、预算估算和住宿建议。
                保存后会自动设为当前进行中的计划。""",
    args_schema=SavePlanInput,
    handle_tool_errors=tool_error_handler
)


#读取短期记忆
def _get_plan_by_key(runtime:ToolRuntime,plan_key:str)-> str:
    """
    读取短期记忆

    Args:
        runtime:工具的运行时
        plan_key:旅行计划key,请先通过key工具查找key
    Return:返回操作结果
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("读取短期记忆： 缺少用户 ID")
        return "错误：缺少用户 ID"
    namespace = ('users',user_id,"plan")
    existing = runtime.store.get(namespace, plan_key)
    if existing:
        value = existing.value
        return f'旅行计划：{plan_key}\n旅行计划数据：{value["plan_data"]}\n摘要：{value["summary"]}\n'
    return f'未找到旅行计划：{plan_key}'
get_plan_by_key = StructuredTool.from_function(
    func=_get_plan_by_key,
    name="get_plan_by_key",
    description="读取短期记忆，先通过合适的key工具获取key",
    handle_tool_errors=tool_error_handler
)
#覆写摘要
def _update_plan_summary(runtime:ToolRuntime,plan_key:str,summary:Optional[str] = None)-> str:
    """
    覆写摘要

    Args:
        runtime:工具的运行时
        plan_key:旅行计划key,请先通过key工具查找key
        summary:摘要
    Return:返回操作结果
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("覆写摘要： 缺少用户 ID")
        return "错误：缺少用户 ID"
    namespace = ('users',user_id,"plan")
    value = runtime.store.get(namespace, plan_key)
    if not value:
        return f'未找到旅行计划：{plan_key},请先通过key工具查找key或创建旅行计划'
    value = value.value
    if not summary:
        summary_model = _get_summary_model(runtime)
        messages = runtime.state.get('messages')
        if not messages:
            logger.error("覆写摘要: 缺少messages实例")
            return "错误：缺少messages实例"
        summary = generate_summary(messages, summary_model)
    value["summary"] = summary
    runtime.store.put(namespace, plan_key, value)
    return f'已覆写旅行计划：{plan_key}\n摘要：{summary}'
update_plan_summary = StructuredTool.from_function(
    func=_update_plan_summary,
    name="update_plan_summary",
    description="覆写摘要,先通过合适的key工具获取key,然后传入摘要(可填)",
    handle_tool_errors=tool_error_handler
)
#追加计划表
def _append_plan_version(runtime:ToolRuntime,plan_key:str,plan_data: dict)-> str:
    """
    追加计划表

    Args:
        runtime:工具的运行时
        plan_key:旅行计划key,请先通过key工具查找key
        plan_data:计划表数据
    Return:返回操作结果
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        return "错误：缺少用户 ID"
    namespace = ('users',user_id,"plan")
    value = runtime.store.get(namespace, plan_key)
    if not value:
        return f'未找到旅行计划：{plan_key},请先通过key工具查找key或创建旅行计划'
    data = value.value
    if "plan_data" not in data or data["plan_data"] is None:
        data["plan_data"] = []  # 初始化为空列表
    data["plan_data"].append(plan_data)
    runtime.store.put(namespace, plan_key, data)
    return f'已追加旅行计划：{plan_key}\n追加计划表数据：{plan_data}'
append_plan_version = StructuredTool.from_function(
    func=_append_plan_version,
    name="append_plan_version",
    description="追加新的计划表版本到已有的旅行计划中,先通过合适的key工具获取key",
    args_schema=plan_data,
    handle_tool_errors=tool_error_handler
)
#保存最终版
def _confirm_plan_version(runtime:ToolRuntime,plan_key:str,target_index: int = -1) -> str:
    """
    保存最终版

    Args:
        runtime:工具的运行时
        plan_key:旅行计划key,请先通过key工具查找key
        target_index:目标索引，默认为-1，表示最后一个版本
    Return:返回操作结果
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        return "错误：缺少用户 ID"
    namespace = ('users',user_id,"plan")
    value = runtime.store.get(namespace, plan_key)
    if not value:
        return f'未找到旅行计划：{plan_key},请先通过key工具查找key或创建旅行计划'
    data = value.value
    plan_list = data.get("plan_data")
    if not plan_list or not isinstance(plan_list, list):
        return f'该计划 {plan_key} 尚无计划表版本，请先添加计划表'
    try:
        plan_data = data["plan_data"][target_index]
        data["plan_data"] = [plan_data]
    except IndexError:
        return f'未找到旅行计划版本：{plan_key}索引{target_index},列表长度{len(data["plan_data"])}'
    data["summary"] = None
    runtime.store.put(namespace, plan_key, data)
    #重置当前计划key
    runtime.state["current_plan_key"] = None
    return f'已保存旅行计划：{plan_key}\n最终版计划表数据：{plan_data}'
confirm_plan_version = StructuredTool.from_function(
    func=_confirm_plan_version,
    name="confirm_plan_version",
    description="保存最终版,先通过合适的key工具获取key,target_index:目标索引，默认为-1，表示最后一个版本",
    handle_tool_errors=tool_error_handler
)
#读取计划表列表
def _list_plan_versions(runtime: ToolRuntime, plan_key: str) -> str:
    """
    读取计划表列表

    Args:
        runtime:工具的运行时
        plan_key:旅行计划key,请先通过key工具查找key
    Return:返回操作结果
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("读取计划表列表： 缺少用户 ID")
        return "错误：缺少用户 ID"
    namespace = ('users',user_id,"plan")
    value = runtime.store.get(namespace, plan_key)
    if not value:
        return f'未找到旅行计划：{plan_key},请先通过key工具查找key或创建旅行计划'
    data = value.value
    plan_list = data.get("plan_data")
    if not plan_list:
        return f'该计划 {plan_key} 尚无计划表版本，请先添加计划表'
    a = []
    for i in range(len(plan_list)):
        plan_data = plan_list[i]
        a.append( f'计划表版本 {i+1}: {plan_data}\n')
    return f'旅行计划：{plan_key}\n旅行计划表列表：\n'+''.join(a)
list_plan_versions = StructuredTool.from_function(
    func=_list_plan_versions,
    name="list_plan_versions",
    description="读取计划表列表",
    handle_tool_errors=tool_error_handler
)

get_plan_keys_by_city = StructuredTool.from_function(
    func=_get_plan_keys_by_city,
    name="get_plan_keys_by_city",
    description="根据城市名称查询该城市的所有key",
    handle_tool_errors=tool_error_handler
)

get_all_plan_keys = StructuredTool.from_function(
    func=_get_all_plan_keys,
    name="get_all_plan_keys",
    description="查询用户所有旅行地的key",
    handle_tool_errors=tool_error_handler
)
