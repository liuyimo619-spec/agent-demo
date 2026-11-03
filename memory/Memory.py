from langgraph.prebuilt import ToolRuntime
from langchain_core.tools import StructuredTool
from typing import Literal
from pydantic import BaseModel,Field
from sqlalchemy import delete, select
#导入数据类
from memory.ORM_SQLAlchemy import User_Preferences,SessionLocal
from sqlalchemy.dialects.postgresql import insert
import sys
import os

# 把项目根目录加入 Python 的模块搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger import logger


def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"喜好系统当前操作出错，不影响主进程，请明确告知用户（异常：{type(error).__name__} - {str(error)})"



#规范llm的输入输出
PreferenceKey = Literal[
    "风格", "饮食", "住宿", "活动", "避免", "必须体验", "氛围", "特殊需求"
]

class PreferencesInput(BaseModel):
    preferences_key:PreferenceKey = Field(description="【必须提供】用户喜好类型，从以下选择：风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求")
    preferences:str = Field(description="【必须提供】偏好的具体描述，用自然语言表达，例如‘喜欢辣’或’怕高‘")


#写入用户喜好
async def save_user_preferences(runtime:ToolRuntime,preferences_key:str,preferences:str) -> str:
    """
    保存用户喜好
    """
    try: 
        # 1.从 Agent 状态中获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        # 2.构建 PostgreSQL 插入语句
        stmt = (
            insert(User_Preferences)
            .values(
                user_id=user_id,
                preference_key=preferences_key,
                preference_value=preferences,
            )
            .on_conflict_do_nothing(
                index_elements=['user_id', 'preference_key','preference_value'],
            )
        )
        # 3.创建短生命周期 Session，并执行事务
        async with SessionLocal.begin() as session:
            await session.execute(stmt)
        return "喜好已保存/存在"
    except Exception as e:
        return tool_error_handler(e)
save_user_preferences = StructuredTool.from_function(
    coroutine=save_user_preferences,
    name="save_user_preferences",
    description="强制保存用户偏好。调用时，必须同时提供 'preferences_key'（类别）和 'preferences'（具体内容），两者缺一不可。",
    args_schema=PreferencesInput,
)

#删除用户某一个喜好
class RemovePreferenceInput(BaseModel):
    preferences_key:PreferenceKey = Field(description="【必须提供】用户喜好类型，从以下选择：风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求")
    value: str = Field(description="【必须提供】要删除的偏好值，如'吃辣'")

async def remove_user_preference(runtime:ToolRuntime,preferences_key:str,value:str) -> str:
    """
    删除用户某项喜好
    """
    try:
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        stmt = (
            delete(User_Preferences)
            .where(
                User_Preferences.user_id == user_id,
                User_Preferences.preference_key == preferences_key,
                User_Preferences.preference_value == value,
            )
        )
        async with SessionLocal.begin() as session:
            await session.execute(stmt)
        return "删除成功"
    except Exception as e:
        return tool_error_handler(e)
remove_user_preference = StructuredTool.from_function(
    coroutine=remove_user_preference,
    name="remove_user_preference",
    description="强制删除用户某项偏好。调用时，必须同时提供 'preferences_key'（类别）和 'value'（具体内容），两者缺一不可。",
    args_schema=RemovePreferenceInput
)


#查询用户喜好
class GetPreferencesInput(BaseModel):
    category: str = Field(
        default=None,
        description="可选：指定要查询的偏好类别（风格、饮食、住宿、活动、避免、必须体验、氛围、特殊需求）。不传则返回全部。"
    )
async def get_user_preferences(runtime:ToolRuntime,category:str=None)-> str:
    """
    查询用户喜好
    """
    
    try:
        # 1.从 Agent 状态中获取用户 ID
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        # 2.构建 PostgreSQL 查询语句
        stmt = select(
        User_Preferences.preference_key,
        User_Preferences.preference_value,
        ).where(
            User_Preferences.user_id == user_id
        )
        if category is not None:
            stmt = stmt.where(
                User_Preferences.preference_key == category
            )
        # 3.按类别和喜好内容排序
        stmt = stmt.order_by(User_Preferences.preference_key,User_Preferences.preference_value)
        # 4.创建短生命周期 Session，并执行查询
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            rows = result.all()
        # 5.指定类别
        if category is not None:
            if not rows:
                return "没有用户偏好"
            value_list = '、'.join(value for key, value in rows)
            return f'用户偏好：{value_list}'
        # 6.查询全部
        if not rows:
            return "没有用户偏好"
        # 7.将查询结果按照类别整理
        prefs = {}
        for key, value in rows:
            prefs.setdefault(key, []).append(value)
        lines = [
            f'{key}: {"、 ".join(value)}' for key, value in prefs.items()
        ]
        return "用户全部喜好：\n" + "\n".join(lines)
    except Exception as e:
        return tool_error_handler(e)    
get_user_preferences = StructuredTool.from_function(
    coroutine=get_user_preferences,
    name="get_user_preferences",
    description="强制查询用户偏好。调用时，可选提供 'category'（类别），不传则返回全部。",
    args_schema=GetPreferencesInput
)