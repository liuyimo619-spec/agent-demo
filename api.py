import asyncio

from fastapi import APIRouter, FastAPI, Request,Depends,HTTPException
from  agent import chat_loop,create_agent_instance
from memory.checkpointer import checkpointer
from contextlib import asynccontextmanager
from pydantic import BaseModel,Field
from fastapi.responses import StreamingResponse,Response
from auth import register_user,login_user,logout_user,verify_token,cleanup_loop

# 生命周期
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 创建agent 并挂载agent
    app.state.agent = await create_agent_instance(checkpointer)
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



#路由依赖
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
# 创建依赖
get_current_user =APIRouter(dependencies=[Depends(verifytoken)])
# 添加跨域中间件
@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    if request.method == "OPTIONS":
        headers = {
            # 允许所有源访问
            "Access-Control-Allow-Origin": "*",
            # 允许所有方法
            "Access-Control-Allow-Methods": "POST,get,options,delete",
            # 允许所有请求头
            "Access-Control-Allow-Headers": "content-type,Authorization,user_id",

        }
        return Response(status_code=200, headers=headers)
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"# 允许所有源访问
    return response



# 请求参数
class ChatRequest(BaseModel):
    message:str = Field(...,min_length=1,max_length=500,description="用户提示词，必填项，最短为1 最长为500字符")   # 用户输入
#流式响应接口
@get_current_user.post("/旅行agent/")
async def travel_agent(request: ChatRequest,user_id:tuple[str,str] = Depends(verifytoken)):
    # 获取agent
    agent = app.state.agent
    return StreamingResponse(chat_loop(agent,request.message,user_id[0]),media_type="text/plain")











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
@get_current_user.post("/Logout/")
async def logout(id_token:tuple[str,str] = Depends(verifytoken)):
    return await logout_user(id_token[0],id_token[1])

#挂载
app.include_router(get_current_user,prefix="/agent")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app="api:app", host="127.0.0.1", port=8000, reload=True)
