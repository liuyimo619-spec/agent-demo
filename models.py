import os
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(override=True)
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL")
DASHSCOPE_MODELx_ID = os.getenv("DASHSCOPE_MODELx_ID")
DASHSCOPE_MODEL_ID = os.getenv("DASHSCOPE_MODEL_ID")
#初始化主模型
model = init_chat_model(
    model=f"openai:{DASHSCOPE_MODEL_ID}",
    api_key=DASHSCOPE_API_KEY,
    base_url=DASHSCOPE_BASE_URL,
)
#初始化小模型
qwen_sw_f = init_chat_model(
    model=f"openai:{DASHSCOPE_MODELx_ID}",
    api_key=DASHSCOPE_API_KEY,
    base_url=DASHSCOPE_BASE_URL,
    temperature=0,
    extra_body={"enable_thinking": False},
)
#初始化无思考模型
model_no_think = init_chat_model(
    model=f"openai:{DASHSCOPE_MODEL_ID}",
    api_key=DASHSCOPE_API_KEY,
    base_url=DASHSCOPE_BASE_URL,
    extra_body={"enable_thinking": False},
)
