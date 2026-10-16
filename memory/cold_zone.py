#添加冷区
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolRuntime
from datetime import datetime, timedelta
from pydantic import BaseModel,Field
from sqlalchemy import func, select
#导入数据库
from memory.ORM_SQLAlchemy import Cold_zone_attractions,Cold_Zone_Cities,SessionLocal
from logger import logger

def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"冷区系统出错，不影响主进程，请明确告知用户（异常：{type(error).__name__} - {str(error)}）"

#读取冷区
async def _get_cold_zone_raw(runtime:ToolRuntime, city:str|None = None, include_attractions: bool = True,) -> list[str]|str:
    """
    查询用户冷区

    Args:
        runtime: 工具的运行时
        city: 可选，指定城市名。不传则返回全部城市的景点
        include_attractions: 是否包含景点冷区。True（默认）返回城市+景点，
                            False 只返回城市冷区
    """
    try:
        user_id = runtime.state.get('user_id')
        if not user_id:
            logger.error("缺少用户 ID")
            return "错误：缺少用户 ID"
        if include_attractions:
            #构建默认查询语句
            stmt = select(Cold_zone_attractions.city, Cold_zone_attractions.attraction).where(Cold_zone_attractions.user_id == user_id,Cold_zone_attractions.end_time > func.now())
            #添加城市条件
            if city:
                stmt = stmt.where(Cold_zone_attractions.city == city)
            #执行查询语句
            async with SessionLocal() as session:
                result = await session.execute(stmt)
                rows  = result.all()
            if not rows :
                return "没有冷区数据"
            prefs = {}
            for c,a in rows :
                prefs.setdefault(c, []).append(a)
            list_str = [f'{cityf}:{", ".join(valuef)}' for cityf, valuef in prefs.items()]
            return '用户冷区：\n'+'\n'.join(list_str)
        else:
            #构建不读取景点查询语句
            stmt = select(Cold_Zone_Cities.city).where(Cold_Zone_Cities.user_id == user_id,Cold_Zone_Cities.end_time > func.now())
            if city:
                stmt = stmt.where(Cold_Zone_Cities.city == city)
            #执行查询语句
            async with SessionLocal() as session:
                result = await session.execute(stmt)
                cold_zones  = result.scalars().all()
            if not cold_zones :
                return '没有冷区数据'
            return f"用户城市冷区：{'、'.join(cold_zones)}"
    except Exception as e:
        return tool_error_handler(e)
get_cold_zone_raw = StructuredTool.from_function(
    coroutine=_get_cold_zone_raw,
    name="get_cold_zone_raw",
    description="查询用户冷区默认返回全部的城市：[景点],可选择返回指定城市的景点或者只返回城市",
)




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


