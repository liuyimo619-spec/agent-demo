#添加冷区
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolRuntime
from datetime import datetime, timedelta
# memory/cold_zone.py
from pydantic import BaseModel,Field
import sys
import os

# 把项目根目录加入 Python 的模块搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger import logger

def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"冷区系统出错，不影响主进程，请明确告知用户（异常：{type(error).__name__} - {str(error)}）"

class ColdZoneTool(BaseModel):
    cold_zone: str = Field(description="已去过的城市名称，如'成都'、'杭州'")
    attraction_name: list[str] = Field(description="已去过的城市中的景点名称列表，如[武侯祠,西湖]")


#写入冷区
def _add_cold_zone(runtime:ToolRuntime,cold_zone:str,attraction_name:list) -> str:
    '''
    将城市写入冷区，记录用户已去过该城市。
    当用户确认旅行计划后调用此工具，将目的地城市名记入冷区，后续一定时间内不会再推荐该城市。

    Args:
        runtime:工具的运行时
        cold_zone:已去过的城市名称，如"成都"、"杭州"
        attraction_name:已去过的城市中的景点名称列表，如[武侯祠、西湖]
    Return:返回操作是否成功
    '''
    user_id = runtime.state.get('user_id')
    if not user_id:
        logger.error("缺少用户 ID，冷区写入失败")
        return "错误：缺少用户 ID,冷区写入失败"
    store = runtime.store
    namespace = ('users',user_id,"cold_zone")
    key = "cold_zone"
    #判断是否存在冷区
    existing = store.get(namespace,key)
    if existing:
        existing = existing.value
    else:
        existing = {}
    if not isinstance(attraction_name, list):
        logger.info(" attraction_name 不是列表类型")
        return "错误： attraction_name 不是列表类型"
    if cold_zone not in existing:
        attraction_name = attraction_name
    else:
        attraction = existing.get(cold_zone, {}).get('attraction_name')
        if not attraction:
            attraction = []
        attraction.extend(attraction_name)
        attraction_name = list(dict.fromkeys(attraction))
    num = {
        'date':datetime.now().strftime('%Y-%m-%d'),
        'attraction_name':attraction_name
    }
    existing[cold_zone] = num
    store.put(namespace, key, existing)
    return f'已更新城市冷区：{cold_zone}'

add_cold_zone = StructuredTool.from_function(
    func=_add_cold_zone,
    name="add_cold_zone",
    description="将城市写入冷区，记录用户已去过该城市。当用户确认旅行计划后调用，传入目的地城市名（如'北京'）和景点（如‘武侯祠’），后续冷却期内不再推荐该城市/景点。",
    args_schema=ColdZoneTool,
    handle_tool_errors=tool_error_handler
)

#读取冷区
def _get_cold_zone_raw(store, user_id: str) -> str:
    """
    查询用户冷区（内部函数，接受 store 和 user_id）
    """
    if not user_id:
        logger.error("缺少用户 ID，冷区加载失败")
        return '错误缺少用户user_id'
    namespace = ('users',user_id, "cold_zone")
    key = "cold_zone"
    existing = store.get(namespace, key)
    if existing:
        y = '冷区：\n'
        for k, v in existing.value.items():
            attraction_name = v.get('attraction_name')
            if attraction_name :
                y += f'{k}:{"、".join(attraction_name)}\n'
            else:
                y += f'{k}\n'
        return '用户' +y+'\n冷区内景点除客户要求外则不准添加进计划表'
    else:
        return '用户未添加冷区'

#清除过期冷区
def clean_expired_cold_zone(store, user_id: str):
    """
    清理冷区数据，删除超过lock_years年没有访问过的冷区数据
    store:传入调用数据库的工具
    user_id:传入用户user_id
    """
    namespace = ('users', user_id, "cold_zone")
    key = "cold_zone"
    existing = store.get(namespace, key)
    if not existing:
        return "用户未添加冷区"
    cold_zone = existing.value
    current_time = datetime.now()
    lock_years = 3
    lis = []
    for city, time in cold_zone.items():
        date_str = time.get('date')
        if not date_str:
            lis.append(city)
            continue
        time = datetime.strptime(date_str, '%Y-%m-%d')+timedelta(days=lock_years * 365)
        if time <= current_time:
            lis.append(city)
    if not lis:
        return "无过期城市"
    for city in lis:
        cold_zone.pop(city)
    store.put(namespace, key, cold_zone)
    msg = f"已删除过期城市：{','.join(lis)}"
    logger.info(msg)
    return msg


