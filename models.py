import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(override=True)
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL")
#初始化主模型
model = init_chat_model(
    model="openai:qwen-max",
    api_key=DASHSCOPE_API_KEY,
    base_url=DASHSCOPE_BASE_URL,
)
#初始化小模型
qwen_sw_f = init_chat_model(
    model="openai:qwen-flash",
    api_key=DASHSCOPE_API_KEY,
    base_url=DASHSCOPE_BASE_URL,
)
