from sqlalchemy import delete, func, insert, select, update
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
        raise HTTPException (status_code=500, detail='服务器错误')
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
            token = generate_session_token()
            #token_hash 入库
            token_hash = insert(User_sessions).values(user_id=user_id,token=token[1])
            async with SessionLocal.begin() as session:
                await session.execute(token_hash)
            return user_id ,token[0]
        raise HTTPException(status_code=401, detail="密码/账户错误")
    except HTTPException as e:
        raise
    except Exception as e:
        logger.error(f"登入失败 user_id={user_id}: {e}")
        raise HTTPException (status_code=500, detail='服务器错误')

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
        raise HTTPException (status_code=500, detail='服务器错误')

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
        raise HTTPException (status_code=500, detail='服务器错误')
