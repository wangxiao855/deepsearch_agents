import logging
import os
import colorlog

# 初始化全局日志对象
logger = logging.getLogger()


_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, _level_name, logging.INFO))


handler = colorlog.StreamHandler()

handler.setFormatter(colorlog.ColoredFormatter(
    '%(log_color)s%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'bold_red',
    }
))

logger.addHandler(handler)



