import traceback

from openai import BadRequestError, RateLimitError


def identify_module(e):
    """精确识别异常来源，返回详细的模块信息"""
    err_str = str(e).lower()

    # 1. 高德 MCP 限流（通过关键词）
    if "cuqps" in err_str:
        return "高德 MCP（QPS 超限）"

    # 2. 工具调用孤儿错误（需要单独处理，这不是限流）
    if "tool_calls" in err_str and "must be followed by tool messages" in err_str:
        return "工具调用孤儿（缺少对应的 ToolMessage）"

    # 3. 通过异常类型判断
    if isinstance(e, RateLimitError):
        return "通义千问（主模型限流）"

    # 4. OpenAI 兼容接口的 BadRequestError
    if isinstance(e, BadRequestError):
        # 进一步判断是哪个模块
        if "model_not_found" in err_str:
            return "模型不存在（请检查模型名）"
        if "invalid_parameter" in err_str:
            return "API 参数错误（可能是模型名、工具格式或消息结构问题）"
        if "rate_limit" in err_str:
            return "通义千问（主模型限流）"
        return f"OpenAI 兼容接口错误（{e.code if hasattr(e, 'code') else '未知'}）"

    # 5. 通过调用栈判断
    tb = traceback.extract_tb(e.__traceback__)
    for frame in reversed(tb):
        if "tools.py" in frame.filename:
            return f"工具执行错误（{frame.name}）"
        if "amap" in frame.filename.lower() or "mcp" in frame.filename.lower():
            return "高德 MCP 错误"
        if "tongyi" in frame.filename.lower():
            return "通义千问 API 错误"

    return f"未知模块（{type(e).__name__}）"


def should_retry(e):
    """判断异常是否值得重试"""
    err_str = str(e).lower()

    # 可重试的错误类型
    retryable = [
        "cuqps",
        "rate_limit",
        "timeout",
        "connection",
        "engine_response_data_error",
        "timed out"
    ]
    if any(k in err_str for k in retryable):
        return True

    # 不可重试的错误类型（直接抛出）
    non_retryable = [
        "model_not_found",
        "tool_calls",
        "must be followed by tool messages",
    ]
    if any(k in err_str for k in non_retryable):
        return False

    # 默认不重试（避免无限循环）
    return False
