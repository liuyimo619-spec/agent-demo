import asyncio

from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Request,Depends,HTTPException
from  agent import chat_loop,create_agent_instance
from memory.checkpointer import checkpointer
from contextlib import asynccontextmanager
from pydantic import BaseModel,Field
from fastapi.responses import StreamingResponse
from auth import register_user,login_user,logout_user,verify_token,cleanup_loop
from memory.domain_guard import check_domain,check_domain_guard
from logger import logger
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
# 生命周期
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 创建agent 并挂载agent
    app.state.agent = await create_agent_instance(checkpointer)
    # 创建domain_guard 并挂载domain_guard
    app.state.agent_instance = await check_domain_guard()
    # 清理循环
    app.state.cleanup_task = asyncio.create_task(
        cleanup_loop()
    )
    try:
        yield
    finally:
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass



#依赖
async def verifytoken(request: Request):
    request_token = request.headers.get("Authorization")
    if not request_token:
        raise HTTPException(status_code=401, detail="缺少token")
    request_user_id = request.headers.get("user_id")
    if not request_user_id:
        raise HTTPException(status_code=401, detail="缺少user_id")
    #以第一个空格分割字符串
    bearer, separator, token = request_token.partition(" ")
    if bearer.lower() != "bearer" or separator != " " or not token.strip():
        raise HTTPException(status_code=401, detail="无效的token")
    return await verify_token(request_user_id,token)


# 创建app
app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],   # 只写你真实用的源
    allow_methods=["POST", "GET", "OPTIONS", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "user_id"],)

# 请求参数
class ChatRequest(BaseModel):
    message:str = Field(...,min_length=1,max_length=500,description="用户提示词，必填项，最短为1 最长为500字符")   # 用户输入
#流式响应接口
@app.post("/agent/旅行agent/")
async def travel_agent(request: ChatRequest,user_id:tuple[str,str] = Depends(verifytoken)):
    # 检测用户输入是否属于旅行规划领域
    check_domain_guard = app.state.agent_instance

    try:
        async with asyncio.timeout(15):
            check_result = await check_domain(
                check_domain_guard, request.message
            )
    except TimeoutError:
        logger.warning("领域守卫检查超时")
        raise HTTPException(
            status_code=504,
            detail="请求超时，请重试",
        ) 
    if check_result == "三角形":
        raise HTTPException(status_code=400, detail='输入内容不属于旅行规划领域')
    if check_result == "系统错误":
        raise HTTPException(status_code=500, detail="服务器错误")
    # 获取agent
    agent = app.state.agent
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d") + " " + weekdays[now.weekday()]
    message = f"[当前时间：{time_str}]" + " " + request.message
    return StreamingResponse(chat_loop(agent,message,user_id[0]),media_type="text/plain")











#注册请求参数
class RegisterRequest(BaseModel):
    user_id:str
    password:str
#注册
@app.post("/Register/")
async def register(request:RegisterRequest):
    return await register_user(request.user_id,request.password)

#登入请求参数
@app.post("/Login/")
async def login(request:RegisterRequest):
    return await login_user(request.user_id,request.password)
#登出
@app.post("/agent/Logout/")
async def logout(id_token:tuple[str,str] = Depends(verifytoken)):
    return await logout_user(id_token[0],id_token[1])

#挂载
#app.include_router(prefix="/agent")

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app="api:app", host="127.0.0.1", port=8000, reload=True)
