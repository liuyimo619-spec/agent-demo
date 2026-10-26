from langchain.tools import ToolRuntime,tool
from langgraph.types import Command
from logger import logger
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import StructuredTool
from langchain_core.messages import ToolMessage
import os
import asyncio
from typing import Annotated
from pydantic import Field
from dotenv import load_dotenv
from langchain_linkup import LinkupSearchRetriever
import time
from langchain_mcp_adapters.interceptors import ToolCallInterceptor
load_dotenv(override=True)
#工具错误处理
def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"该工具出错，暂时无法执行，（异常：{type(error).__name__} - {str(error)}）"

# tools.py
# 限流拦截器
class SharedRateLimitInterceptor(ToolCallInterceptor):
    _last_call_time = 0  # 类变量，所有实例共享
    _next_allowed_time = 0
    _lock = asyncio.Lock()   # 锁
    # 限流拦截器入口
    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
    # 拦截器方法
    async def __call__(self, request, handler):
        async with SharedRateLimitInterceptor._lock: # 自动加锁释放锁
            #获取当前时间刻度尺
            now = time.time()
            # 计算这次允许执行的时间点：要么是现在（如果很久没调用了），要么是排期时间
            exec_time = max(now, SharedRateLimitInterceptor._next_allowed_time)
            
            # 更新下一次允许调用的时间点（给后面排队的人发号码牌）
            SharedRateLimitInterceptor._next_allowed_time = exec_time + self.min_interval
        wait_time = exec_time - time.time()
        if wait_time > 0:
            logger.info(f"限流等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)
        return await handler(request)
#高德mcp
async def get_amap_tools():
    amap_key = os.getenv("AMAP_KEY")
    if not amap_key:
        logger.error("请传入高德mcp的api_key")
        return []
    rate_limiter = SharedRateLimitInterceptor(calls_per_second=1)
    client = MultiServerMCPClient({
        "amap_maps":{
            'transport':'sse',
            "url": f"https://mcp.amap.com/sse?key={amap_key}",
        }
    }, tool_interceptors=[rate_limiter])
    tools = await client.get_tools()
    for tool in tools:
        tool.handle_tool_error = tool_error_handler
    filtered_tools = [
        tool for tool in tools
        if tool.name != "maps_ip_location"  # 把不想要的排除掉
    ]
    return filtered_tools

#联网查询工具
async def web_search(query: str) -> str:
    '''
    联网查询

    Arg:
        query: 传入需要搜索的字符串
    return:返回搜索结果
    '''
    linkup_api_key = os.getenv("LINKUP_API_KEY")
    if not linkup_api_key:
        logger.error("请传入linkup的api_key")
        return '缺少api无法联网'
    if not query:
        logger.error("没有输入查询内容")
        return "请输入查询内容"
    retriever = LinkupSearchRetriever(
        depth="deep",  # 搜索深度："standard" 或 "deep"[reference:16]
        linkup_api_key=linkup_api_key ,# 如果环境变量已设置，可省略[reference:17]
    )
    # 执行搜索，返回文档列表
    try: 
        docs = await retriever.ainvoke(query, top_k = 5)
    except TypeError:
        docs = (await retriever.ainvoke(query))[:5]
    except Exception as e:
        return tool_error_handler(e)
    if not docs:
        return "没有找到结果"
    doce = []
    for doc in docs:
        doce.append(f"来源: {doc.metadata['url']}\n内容: {doc.page_content}")
    return  "\n\n".join( doce)
web_search = StructuredTool.from_function(
    coroutine=web_search,
    name="web_search",
    description="使用互联网进行搜索，返回搜索结果。",
)

#工具选择工具
def build_tool_selector(business_tools):
    available_names = {item.name for item in business_tools}
    catalog = '\n'.join(
        f'{item.name}: {item.description}'
        for item in business_tools
    )
    # 工具选择
    async def select_tools(name:Annotated[list[str],Field(min_length=1)],runtime:ToolRuntime) -> Command|ToolMessage:
        try:
            # 检查工具是否存在
            invalid = [name for name in name if name not in available_names]
            if invalid:
                return ToolMessage(content=f'无效的工具名称：{invalid},请从目录中重新选择',tool_call_id=runtime.tool_call_id,status='error')
            return Command(update={
                'loaded_tools': name,
                'messages': [ToolMessage(content='已加载:'+'、'.join(name)+'。请继续调用工具完成任务',tool_call_id=runtime.tool_call_id)],
            })
        except Exception as e:
            return tool_error_handler(e)
    select_tools = StructuredTool.from_function(
        coroutine=select_tools,
        name="select_tools",
        description=f"选择工具，请从以下工具中选择，请输入工具名称，多个工具请传入工具名称的字符串列表。\n{catalog}",
    )
    return select_tools


