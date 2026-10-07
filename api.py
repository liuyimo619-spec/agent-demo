from fastapi import FastAPI, Request
from  agent import chat_loop,create_agent_instance
from contextlib import asynccontextmanager
from langgraph.store.sqlite import SqliteStore
from pydantic import BaseModel
from fastapi.responses import StreamingResponse,Response

# 生命周期
@asynccontextmanager
async def lifespan(app: FastAPI):
    with SqliteStore.from_conn_string("./memory/测试.db") as store:
        app.state.agent = await create_agent_instance(store)

        yield
# 创建app
app = FastAPI(lifespan=lifespan)
# 请求参数
class ChatRequest(BaseModel):
    message:str   # 用户输入
    user_id:str   # 用户id 通过用户id来区分用户并保持会话

# 添加跨域中间件
@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST,get,options,delete",
            "Access-Control-Allow-Headers": "content-type,Authorization",
        }
        return Response(status_code=200, headers=headers)
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"# 允许所有源访问
    return response
#流式响应接口
@app.post("/旅行agent/")
async def travel_agent(request: ChatRequest):
    agent = app.state.agent
    return StreamingResponse(chat_loop(agent,request.message,request.user_id),media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app="api:app", host="127.0.0.1", port=8000, reload=True)
