import asyncio
from agent import checkpointer
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from memory.ORM_SQLAlchemy import Users,User_sessions,SessionLocal
from security import generate_session_token,hash_session_token,verify_password,hash_password
from fastapi import HTTPException
from logger import logger
#注册流程
async def register_user(user_id:str,password:str):
    try:
        hashed_password = hash_password(password)
        #生成token 和 token_hash
        token,token_hash = generate_session_token()
        #账密入库
        user = insert(Users).values(user_id=user_id,password_hash=hashed_password)
        #token_hash 入库
        token_hash = insert(User_sessions).values(user_id=user_id,token=token_hash)       
        async with SessionLocal.begin() as session:
            await session.execute(user)
            await session.execute(token_hash)
        return user_id ,token
    except IntegrityError:
        raise HTTPException(status_code=409, detail="用户已存在")
    except Exception as e:
        logger.error(f"注册失败 user_id={user_id}: {e}")
        raise HTTPException(status_code=500, detail='服务器错误')
#登入流程
async def login_user(user_id:str,password:str):
    try:
        #查询账户的password_hash
        stmt = select(Users.password_hash).where(Users.user_id == user_id)
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            hashed_password = result.fetchone()
            if hashed_password is None:
                raise HTTPException(status_code=401, detail="密码/账户错误")
        if verify_password(password,hashed_password[0]):
            #生成token 和 token_hash
            token,token_hash = generate_session_token()
            #token_hash 入库
            token_hash = insert(User_sessions).values(user_id=user_id,token=token_hash)
            async with SessionLocal.begin() as session:
                await session.execute(token_hash)
            return user_id ,token
        raise HTTPException(status_code=401, detail="密码/账户错误")
    except HTTPException as e:
        raise
    except Exception as e:
        logger.error(f"登入失败 user_id={user_id}: {e}")
        raise HTTPException(status_code=500, detail='服务器错误')

#登出流程
async def logout_user(user_id:str,token:str):
    try:
        #生成token_hash
        token_hash = hash_session_token(token)
        #删除token_hash
        stmt = delete(User_sessions).where(User_sessions.user_id == user_id, User_sessions.token == token_hash)
        async with SessionLocal.begin() as session:
            row = await session.execute(stmt)
            if row.rowcount == 1:
                return True
        raise HTTPException(status_code=401, detail="令牌无效")
    except HTTPException as e:
        raise
    except Exception as e:
        logger.error(f"登出失败 user_id={user_id}: {e}")
        raise HTTPException(status_code=500, detail='服务器错误')

#令牌验证+last_active刷新
async def verify_token(user_id:str,token:str):
    try:
        #验证令牌,刷新last_active并返回token 
        token_hash = hash_session_token(token)
        update_stmt = update(User_sessions).where(User_sessions.user_id == user_id,User_sessions.token == token_hash,User_sessions.end_time > func.now()).values(last_active=func.now()).returning(User_sessions.user_id)
        async with SessionLocal.begin() as session:
            row = await session.execute(update_stmt)
            row = row.scalar_one_or_none()
            if row is not None:
                return row,token
            raise HTTPException(status_code=401, detail="令牌无效/已过期")
    except HTTPException as e:
        raise 
    except Exception as e:
        logger.error(f"令牌验证失败 user_id={user_id}: {e}")
        raise HTTPException(status_code=500, detail='服务器错误')

#计算过期内存记忆
async def count_memory_within_time():
    try:
        #查询所有用户
        stmt = select(User_sessions.user_id).group_by(User_sessions.user_id).having(func.max(User_sessions.last_active) < func.now()-text("INTERVAL '7 days'"))
        async with SessionLocal() as session:
            result = await session.execute(stmt)
            user_ids = result.scalars().all()
            return user_ids
    except Exception as e:
        logger.error(f"计算过期内存记忆失败: {e}")
        return []

#清理内存记忆函数
async def cleanup_expired(user_ids:list[str]):
    try:
        #删除过时记忆
        for user_id in user_ids:
            await checkpointer.adelete_thread(user_id)
        logger.info(f"清理过期内存数量: {len(user_ids)}")
    except Exception as e:
        logger.error(f"清理过期内存记忆失败: {e}")


#清理过期内存记忆
async def cleanup_loop():
    while True:
        try:
            app = await count_memory_within_time()
            await cleanup_expired(app)  # 查询并清理
        except Exception as e:
            logger.error(f"清理过期内存记忆失败: {e}")        
        await asyncio.sleep(6 * 60 * 60)  # 睡眠 6 小时


