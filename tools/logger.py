""" 运行日志记录 """

from loguru import logger
from pathlib import Path
from datetime import datetime
import sys

logger.remove()

# 配置日志文件路径
log_dir = Path("runtime_logs")
if not log_dir.exists():
    log_dir.mkdir()

# 定义日志文件名
log_file_name = f"runtime_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
log_file_path = log_dir / log_file_name

# 配置 loguru 日志
logger.add(
    log_file_path,
    format="<level>[{time:HH:mm:ss}] [{thread.name} | {level:<8}] [{module}] [{file.name}:{line}]: {message}</level>",
    level="TRACE",
    rotation="1 week",
    compression="zip",
    backtrace=True,
    diagnose=True,
    enqueue=True
)

# 添加控制台日志输出
logger.add(
    sys.stderr,
    format="<level>[{time:HH:mm:ss}] [{thread.name} | {level:<8}] [{module}] [{file.name}:{line}]: {message}</level>",
    level="DEBUG",
    colorize=True
)

# 测试日志
if __name__ == "__main__":
    logger.trace("这是一个 trace 级别的日志")
    logger.debug("这是一个 debug 级别的日志")
    logger.info("这是一个 info 级别的日志")
    logger.success("这是一个 success 级别的日志")
    logger.warning("这是一个 warning 级别的日志")
    logger.error("这是一个 error 级别的日志")
    logger.critical("这是一个 critical 级别的日志")