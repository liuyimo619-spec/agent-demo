# logger.py

# 1. 导入 Python 标准库
import logging
from datetime import datetime

# 2. 创建日志器（Logger）
#    这是你以后在代码里调用的那个东西：logger.info("xxx")
logger = logging.getLogger("travel_agent")

# 3. 设置日志级别
#    DEBUG < INFO < WARNING < ERROR < CRITICAL
#    设置为 DEBUG，意味着 DEBUG 及以上级别的所有日志都会被记录
logger.setLevel(logging.DEBUG)

# 4. 生成日志文件名，按天区分
#    比如：agent_20260723.log
log_filename = f'agent_{datetime.now().strftime("%Y%m%d")}.log'

# 5. 创建文件处理器（FileHandler）
#    这是决定“日志往哪里写”的东西
#    你不想打印到控制台，所以只用 FileHandler，不用 StreamHandler
file_handler = logging.FileHandler(
    log_filename,          # 文件名
    encoding='utf-8'       # 用 UTF-8 编码，防止中文乱码
)

# 6. 设置文件处理器的日志级别
#    文件里记录所有级别的日志（包括 DEBUG）
file_handler.setLevel(logging.DEBUG)

# 7. 创建格式器（Formatter）
#    决定“每一条日志长什么样”
#    %(asctime)s  → 时间
#    %(levelname)s → 级别（INFO、WARNING、ERROR 等）
#    %(name)s     → 日志器名称（"travel_agent"）
#    %(message)s  → 你的日志消息
file_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 8. 把格式器绑定到文件处理器
file_handler.setFormatter(file_formatter)

# 9. 把文件处理器添加到日志器
logger.addHandler(file_handler)