"""
Telegram 客户端工具模块

提供统一的客户端创建和管理功能，支持：
1. 用户 Session 登录（非交互式）
2. 交互式登录（手机号+验证码）

使用优先级：
1. 有 session 文件 → 使用用户 Session（非交互式）
2. 没有 session → 交互式登录
"""

import json
import logging
import os
import sys
import time
from functools import wraps
from pathlib import Path

# 配置根 logger 的 FloodWait filter，在 dotenv 加载前完成
# 这样 Pyrogram 产生的日志也会被过滤
_logger = logging.getLogger(__name__)


class _FloodWaitFilter(logging.Filter):
    FLOOD_WAIT_PATTERN = __import__("re").compile(r"Waiting for \d+ seconds before continuing")

    def filter(self, record):
        if record.levelno == logging.WARNING:
            msg = record.getMessage()
            if self.FLOOD_WAIT_PATTERN.search(msg):
                return False
        return True


def _setup_floodwait_filter():
    root = logging.getLogger()
    # 检查是否已有 filter
    for handler in root.handlers:
        for f in handler.filters:
            if isinstance(f, _FloodWaitFilter):
                return
    # 添加 filter 到现有 handlers
    for handler in root.handlers:
        handler.addFilter(_FloodWaitFilter())
    # 如果还没有 handlers（此时 pyrogram 还未导入），创建一个带 filter 的 handler
    # 防止 Python 自动添加默认的无过滤 StreamHandler
    if not root.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(logging.WARNING)
        h.addFilter(_FloodWaitFilter())
        root.addHandler(h)


_setup_floodwait_filter()

from dotenv import load_dotenv


# 获取配置目录
# 优先级：TG_MGR_CONFIG_DIR > TG_MGR_DEV=1 > ~/.tg-mgr
def get_config_dir() -> Path:
    """获取配置目录路径

    - TG_MGR_CONFIG_DIR: 自定义配置目录
    - TG_MGR_DEV=1: 开发模式，使用项目根目录
    - 默认: ~/.tg-mgr (生产安装)
    """
    if os.environ.get("TG_MGR_CONFIG_DIR"):
        return Path(os.environ["TG_MGR_CONFIG_DIR"])

    if os.environ.get("TG_MGR_DEV"):
        # 开发模式：使用项目根目录
        return Path(__file__).parent.parent.parent

    return Path.home() / ".tg-mgr"


# 加载 .env 环境变量（优先从配置目录加载）
env_path = get_config_dir() / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()  # 回退到 cwd

from pyrogram import Client, errors  # noqa: E402


# 配置默认值（唯一的硬编码默认值来源）
DEFAULT_CONFIG: dict = {
    # 核心配置
    "channel_id": None,
    "download_dir": "~/Downloads/Telegram",
    # 转发相关
    "reaction_limit": 10,
    "views_limit": 50,
    "max_source_channels": 10,
    "recursion_depth": 5,
    # 清理相关
    "filter_min_size": 1048576,      # 1MB
    "filter_max_size": 1073741824,   # 1GB
    # 重试相关
    "max_retries": 5,
    "retry_delay_base": 1,
    # 媒体类型
    "media_types": ["photo", "video", "document", "audio", "animation", "text", "video_note"],
}


def get_project_tmp_dir() -> Path:
    """获取项目 tmp 目录路径（默认在 ~/.tg-mgr/tmp）"""
    return get_config_dir() / "tmp"


def get_sessions_dir() -> Path:
    """获取 sessions 目录路径"""
    sessions_dir = get_project_tmp_dir() / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


# 配置缓存（避免每次调用都读文件）
_config_cache: dict | None = None
_config_cache_mtime: float | None = None


def get_config_path() -> Path:
    """获取配置文件路径（默认从 ~/.tg-mgr/config.json）"""
    return get_config_dir() / "config.json"


def get_log_path(name: str = "app.log") -> Path:
    """获取日志文件路径"""
    log_dir = get_project_tmp_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / name


def is_interactive():
    """检测是否在交互式终端中运行"""
    return sys.stdin.isatty() and sys.stdout.isatty()


def get_config() -> dict:
    """获取配置（带缓存）

    敏感信息(api_id, api_hash, bot_token, channel_id)从环境变量读取（.env 文件），
    其他配置从 config.json 读取。配置会被缓存，文件修改时会自动失效。

    Returns:
        配置字典，包含 api_id, api_hash, bot_token, channel_id 及 config.json 中的配置
    """
    global _config_cache, _config_cache_mtime

    config_path = get_config_path()

    # 检查缓存是否有效（文件未修改）
    current_mtime = config_path.stat().st_mtime if config_path.exists() else None
    if _config_cache is not None and _config_cache_mtime == current_mtime:
        return _config_cache.copy()

    # 从环境变量读取敏感信息
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")

    # 验证必需字段
    if not api_id:
        raise ValueError("Missing required environment variable: TG_API_ID")
    if not api_hash:
        raise ValueError("Missing required environment variable: TG_API_HASH")

    try:
        api_id_int = int(api_id)
    except ValueError:
        raise ValueError("TG_API_ID must be a valid integer")

    config = {
        "api_id": api_id_int,
        "api_hash": api_hash,
        "bot_token": os.environ.get("TG_BOT_TOKEN"),
        "channel_id": os.environ.get("TG_CHANNEL_ID"),
    }

    # 从 config.json 读取非敏感配置
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            file_config = json.load(f)
            # 合并非敏感配置
            for key in DEFAULT_CONFIG:
                if key in file_config:
                    config[key] = file_config[key]

    # 更新缓存
    _config_cache = config
    _config_cache_mtime = current_mtime

    return config.copy()


def create_client(config: dict, session_name: str = "tg-mgr") -> tuple[Client, bool]:
    """
    创建 Telegram 客户端

    Args:
        config: 配置字典，包含 api_id, api_hash
        session_name: session 名称

    Returns:
        (client, is_started): 客户端实例、客户端是否已启动
    """
    api_id = config["api_id"]
    api_hash = config["api_hash"]

    client_kwargs = {
        "name": str(get_sessions_dir() / session_name),
        "api_id": api_id,
        "api_hash": api_hash,
    }

    session_file = get_sessions_dir() / f"{session_name}.session"
    is_started = False

    if session_file.exists():
        print(f"[{session_name}] 使用已有的用户 session 登录")
    else:
        print(f"[{session_name}] 首次运行，需要输入手机号和验证码进行登录")

    client = Client(**client_kwargs)

    return client, is_started


def get_client(session_name: str = "tg-mgr") -> Client:
    """
    获取 Telegram 客户端，自动处理登录

    Args:
        session_name: session 名称

    Returns:
        已启动的客户端
    """
    config = get_config()
    session_file = get_sessions_dir() / f"{session_name}.session"
    if not session_file.exists():
        print(f"[{session_name}] 首次运行，需要输入手机号和验证码进行登录")
    return Client(
        str(get_sessions_dir() / session_name), api_id=config["api_id"], api_hash=config["api_hash"]
    )


def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    """FloodWait 重试装饰器

    使用指数退避策略处理 FloodWait 异常。

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except errors.FloodWait as e:
                    if retry == max_retries - 1:
                        raise
                    wait_time = max(e.value, 5)
                    time.sleep(wait_time)

        return wrapper

    return decorator
