from logger import logger
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import StructuredTool
import os
import asyncio
from dotenv import load_dotenv
from langchain_linkup import LinkupSearchRetriever
load_dotenv(override=True)
import time
from langchain_mcp_adapters.interceptors import ToolCallInterceptor


# tools.py
class SharedRateLimitInterceptor(ToolCallInterceptor):
    _last_call_time = 0  # 类变量，所有实例共享

    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second

    async def __call__(self, request, handler):
        now = time.time()
        wait_time = self.min_interval - (now - SharedRateLimitInterceptor._last_call_time)
        if wait_time > 0:
            logger.info(f"限流等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)
        SharedRateLimitInterceptor._last_call_time = time.time()
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
#本地html创建工具mcp
async def get_html_tools():
    '''链接到本地的 mcp-html-sync-server,返回其提供的所有工具'''
    try:
        client = MultiServerMCPClient({
            'html-sync':{
                'transport':'stdio',
                'command':'npx',
                'args':['-y','mcp-html-sync-server'],
                'env':{
                    'NODE_ENV':"production"
                }
            }
        })
        tools = await client.get_tools()
    except Exception as e:
        logger.error(f'html-sync无法注入，不影响主流程。或联系管理员处理：{e}')
        return  []
    return tools

#工具错误处理
def tool_error_handler(error: Exception) -> str:
    # 1. 记录日志（可选）
    logger.error(f"[ERROR] 工具执行异常：{type(error).__name__}: {error}")

    # 2. 返回给 LLM 的友好信息（含异常类型，便于 LLM 理解）
    return f"该工具出错，暂时无法执行，（异常：{type(error).__name__} - {str(error)}）"
#联网查询工具
def web_search(query: str) -> str:
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
        docs = retriever.invoke(query, top_k = 5)
    except TypeError:
        docs = retriever.invoke(query)[:5]
    except Exception as e:
        logger.error(f'linkup-search-retriever无法注入，不影响主流程。{e}')
        return "搜索请求失败"
    if not docs:
        logger.error("没有找到结果")
        return "没有找到结果"
    doce = []
    for doc in docs:
        doce.append(f"来源: {doc.metadata['url']}\n内容: {doc.page_content}")
    return  "\n\n".join( doce)
web_search = StructuredTool.from_function(
    func=web_search,
    name="web_search",
    description="使用互联网进行搜索，返回搜索结果。",
    handle_tool_errors=tool_error_handler
)