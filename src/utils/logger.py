"""
日志工具模块

提供统一的日志配置，支持：
1. 控制台输出（INFO 级别）
2. 文件输出（DEBUG 级别）
3. 环境变量控制日志级别（TG_MGR_LOG_LEVEL）

日志文件位置：~/.tg-mgr/tmp/logs/tg-mgr.log（生产模式）
"""
import logging
import os
import sys
from pathlib import Path
from typing import Optional


def get_log_dir() -> Path:
    """获取日志目录路径"""
    from .telegram_client import get_project_tmp_dir
    log_dir = get_project_tmp_dir() / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logger(
    name: str,
    level: Optional[str] = None,
    use_file: bool = True
) -> logging.Logger:
    """配置日志记录器

    Args:
        name: 日志记录器名称（通常用 __name__）
        level: 日志级别，默认从 TG_MGR_LOG_LEVEL 环境变量读取
        use_file: 是否输出到文件

    Returns:
        配置好的日志记录器
    """
    if level is None:
        level = os.environ.get('TG_MGR_LOG_LEVEL', 'INFO')

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger(name)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    logger.setLevel(numeric_level)

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        '[%(name)s] %(levelname)s: %(message)s'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # 文件 handler
    if use_file:
        log_dir = get_log_dir()
        file_handler = logging.FileHandler(log_dir / 'tg-mgr.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """获取已配置的日志记录器（单例模式）"""
    return setup_logger(name)
