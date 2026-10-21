from sqlalchemy import delete, func,select, text
from memory.ORM_SQLAlchemy import SessionLocal, Cold_Zone_Cities,Cold_zone_attractions,Travel_Plan_Workspace,async_engine,User_sessions

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

#日志文件固定在项目目录下的logs文件夹中
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs" 
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "cleanup.log"

#创建独立日志记录器
cleanup_logger = logging.getLogger("cleanup_logger")
cleanup_logger.setLevel(logging.INFO)
cleanup_logger.propagate = False

if not cleanup_logger.handlers:
    handler = RotatingFileHandler(
        LOG_FILE, 
        maxBytes=1024 * 1024 * 5, # 单个日志最大 5 MB
        backupCount=5,            # 最多保留 5 个日志文件
        encoding="utf-8"          # 使用 UTF-8 编码
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler.setFormatter(formatter)
    cleanup_logger.addHandler(handler)
async def clean_expired_data():
    try:
        #查询景点表
        stmt = select(func.count()).select_from(Cold_zone_attractions).where(Cold_zone_attractions.end_time < func.now())
        #清理所有用户工作台过期数据
        data_workspace = delete(Travel_Plan_Workspace).where(Travel_Plan_Workspace.end_time < func.now())
        #清理所有用户城市冷区过期数据
        data_Cold_Zone_Cities = delete(Cold_Zone_Cities).where(Cold_Zone_Cities.end_time < func.now())
        #清理所有用户城市冷区景点过期数据
        data_cold_zone_attraction = delete(Cold_zone_attractions).where(Cold_zone_attractions.end_time < func.now())
        #清理过期令牌
        data_user_sessions = delete(User_sessions).where(User_sessions.end_time < func.now(),User_sessions.last_active < func.now() - text("INTERVAL '8 days'"))
        #创建数据库会话并执行删除操作
        async with SessionLocal.begin() as session:
            #先清理工作台数据
            data_workspace = await session.execute(data_workspace)
            data_workspace = data_workspace.rowcount or 0
            #先查询景点冷区数据
            result  = await session.execute(stmt)
            counts = result .scalar()
            #再清理城市冷区数据相同user_id 和 city的景点会级联删除
            data_Cold_Zone_Cities = await session.execute(data_Cold_Zone_Cities)
            data_Cold_Zone_Cities = data_Cold_Zone_Cities.rowcount or 0
            #再查一下城市冷区数据
            result = await session.execute(stmt)
            counte = result .scalar() or 0
            #最后清理城市冷区景点数据
            data_cold_zone_attraction = await session.execute(data_cold_zone_attraction)
            data_cold_zone_attraction = data_cold_zone_attraction.rowcount or 0
            #计算级联删除景点数据
            count = counts - counte

            #删除过期的令牌
            data_user_sessions = await session.execute(data_user_sessions)
            data_user_sessions = data_user_sessions.rowcount or 0
            
        success = {
            "status": "success",
            "data_workspace":data_workspace,
            "data_Cold_Zone_Cities":data_Cold_Zone_Cities,
            "data_cold_zone_attraction":data_cold_zone_attraction,
            "count":count,
            "counts":counts,
            "data_user_sessions":data_user_sessions
        }
        cleanup_logger.info(f"清理成功，已删除 {data_workspace} 条工作台数据，{data_Cold_Zone_Cities} 条城市冷区数据 {count} 条级联删除景点数据,{data_cold_zone_attraction} 条城市冷区景点数据,一共删除{counts}条冷区景点数据,{data_user_sessions}条过期令牌数据")
        return success
    except Exception as e:
        cleanup_logger.exception(f"清理失败，事务已回滚或提交失败 Error: {e}")
        raise 


# 主程序
async def main() -> None:
    try:
        result = await clean_expired_data()
        #记入主程序拿到的返回结果
        cleanup_logger.info("清除过期数据的结果:%s",result)
    finally:
        await async_engine.dispose()

#启动清理任务
if __name__ == "__main__":
    import asyncio
    asyncio.run(main())