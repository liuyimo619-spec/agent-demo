from langgraph.prebuilt import ToolRuntime
from langchain_core.tools import StructuredTool
from typing import Literal
from pydantic import BaseModel,Field
# memory/cold_zone.py
import sys
import os

# 把项目根目录加入 Python 的模块搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger import logger


def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"喜好系统出错，不影响主进程，请明确告知用户（异常：{type(error).__name__} - {str(error)}）"



#规范llm的输入输出
PreferenceKey = Literal[
    "风格", "饮食", "住宿", "活动", "避免", "必须体验", "氛围", "特殊需求"
]

class PreferencesInput(BaseModel):
    preferences_key:PreferenceKey = Field(description="【必须提供】用户喜好类型，从以下选择：风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求")
    preferences:str = Field(description="【必须提供】偏好的具体描述，用自然语言表达，例如‘喜欢辣’或’怕高‘")


#写入用户喜好
def save_user_preferences(runtime:ToolRuntime,preferences_key:str,preferences:str) -> str:
    """
    保存用户喜好

    Args:
        runtime:工具的运行时
        preferences_key:喜好的类型
        preferences:喜好
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        return "错误：缺少用户 ID"
    store = runtime.store
    namespace = ('users',user_id,"preferences")
    key = "preferences_dict"
    #判断是否存在用户喜好
    existing = store.get(namespace,key)
    if existing:
        #存在则更新
        prefs = existing.value
        if preferences_key in prefs:
            #如果之前是列表，则追加
            if isinstance(prefs[preferences_key], list):
                prefs[preferences_key].append(preferences)
            else:
                # 如果之前是单个字符串，转成列表再追加
                prefs[preferences_key] = [prefs[preferences_key], preferences]
        else:
            # 不存在则添加
            prefs[preferences_key] = [preferences]
    else:
        #不存在则创建
        prefs = {preferences_key:[preferences]}
    #保存
    store.put(namespace,key,prefs)
    return f'已保存用户喜好：{preferences_key} - {preferences}'

save_user_preferences = StructuredTool.from_function(
    func=save_user_preferences,
    name="save_user_preferences",
    description="强制保存用户偏好。调用时，必须同时提供 'preferences_key'（类别）和 'preferences'（具体内容），两者缺一不可。",
    args_schema=PreferencesInput,
    handle_tool_errors=tool_error_handler
)

#删除用户某一个喜好
class RemovePreferenceInput(BaseModel):
    preferences_key:PreferenceKey = Field(description="【必须提供】用户喜好类型，从以下选择：风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求")
    value: str = Field(description="【必须提供】要删除的偏好值，如'吃辣'")

def remove_user_preference(runtime:ToolRuntime,preferences_key:str,value:str) -> str:
    """
    删除用户某项喜好

    Args:
        runtime:工具的运行时
        preferences_key:用户喜好类型
        preferences:要删除的偏好值，如'吃辣'
    """
    user_id = runtime.state.get('user_id')
    if not user_id:
        return "错误：缺少用户 ID"
    store = runtime.store
    namespace = ('users',user_id,"preferences")
    key = "preferences_dict"
    #判断是否存在用户喜好
    existing = store.get(namespace,key)
    if not existing:
        return '暂未存入该用户的喜好'
    prefs = existing.value
    if preferences_key not in prefs:
        return '该用户未添加该类喜好'
    if value in prefs[preferences_key]:
        prefs[preferences_key].remove(value)
        store.put(namespace,key,prefs)
        return f'已删除用户喜好：{preferences_key} - {value}'
    else:
        return '该用户未添加该喜好'
remove_user_preference = StructuredTool.from_function(
    func=remove_user_preference,
    name="remove_user_preference",
    description="强制删除用户某项偏好。调用时，必须同时提供 'preferences_key'（类别）和 'value'（具体内容），两者缺一不可。",
    args_schema=RemovePreferenceInput,
    handle_tool_errors=tool_error_handler
)


#查询用户喜好
class GetPreferencesInput(BaseModel):
    category: str = Field(
        default=None,
        description="可选：指定要查询的偏好类别（风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求）。不传则返回全部。"
    )
def get_user_preferences(runtime:ToolRuntime,category:str=None)-> str:
    """
    查询用户喜好

    Args:
        runtime:工具的运行时
        category:可选：指定要查询的偏好类别（风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求）。不传则返回全部。
    """
    store = runtime.store
    user_id = runtime.state.get('user_id')
    if not user_id:
        return "错误：缺少用户 ID"
    namespace = ('users',user_id,"preferences")
    keys = "preferences_dict"
    existing = store.get(namespace,keys)
    if not existing:
        return '暂未存入该用户的喜好'
    prefs = existing.value
    if category:
        if category in prefs:
            value_list = '、'.join(prefs[category])
            return  f'用户的喜好：{category}：{value_list}'
        else:
            return '该用户未添加该类喜好'
    else:
        if prefs:
            lines = []
            for key, value in prefs.items():
                lines.append(f'{key}:{'、'.join(value)}')
            return '用户全部喜好：\n'+'\n'.join( lines)
        else:
            return '该用户未添加任何喜好'
get_user_preferences = StructuredTool.from_function(
    func=get_user_preferences,
    name="get_user_preferences",
    description="强制查询用户偏好。调用时，可选提供 'category'（类别），不传则返回全部。",
    args_schema=GetPreferencesInput,
    handle_tool_errors=tool_error_handler
)