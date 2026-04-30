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
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


# 获取配置目录
# 优先级：TG_MGR_CONFIG_DIR > TG_MGR_DEV=1 > ~/.tg-mgr
def get_config_dir() -> Path:
    """获取配置目录路径

    - TG_MGR_CONFIG_DIR: 自定义配置目录
    - TG_MGR_DEV=1: 开发模式，使用项目根目录
    - 默认: ~/.tg-mgr (生产安装)
    """
    if os.environ.get('TG_MGR_CONFIG_DIR'):
        return Path(os.environ['TG_MGR_CONFIG_DIR'])

    if os.environ.get('TG_MGR_DEV'):
        # 开发模式：使用项目根目录
        return Path(__file__).parent.parent.parent

    return Path.home() / '.tg-mgr'


# 加载 .env 环境变量（优先从配置目录加载）
env_path = get_config_dir() / '.env'
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()  # 回退到 cwd

from pyrogram import Client, errors  # noqa: E402
import time
from functools import wraps


def get_project_tmp_dir() -> Path:
    """获取项目 tmp 目录路径（默认在 ~/.tg-mgr/tmp）"""
    return get_config_dir() / 'tmp'


def get_sessions_dir() -> Path:
    """获取 sessions 目录路径"""
    sessions_dir = get_project_tmp_dir() / 'sessions'
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def get_config_path() -> Path:
    """获取配置文件路径（默认从 ~/.tg-mgr/config.json）"""
    return get_config_dir() / 'config.json'


def get_log_path(name: str = 'app.log') -> Path:
    """获取日志文件路径"""
    log_dir = get_project_tmp_dir() / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / name


def is_interactive():
    """检测是否在交互式终端中运行"""
    return sys.stdin.isatty() and sys.stdout.isatty()


def get_config():
    """获取配置

    敏感信息(api_id, api_hash, bot_token, channel_id)从环境变量读取（.env 文件），
    其他配置从 config.json 读取。
    """
    config_path = get_config_path()

    # 从环境变量读取敏感信息
    api_id = os.environ.get('TG_API_ID')
    api_hash = os.environ.get('TG_API_HASH')

    # 验证必需字段
    if not api_id:
        raise ValueError("Missing required environment variable: TG_API_ID")
    if not api_hash:
        raise ValueError("Missing required environment variable: TG_API_HASH")

    try:
        api_id = int(api_id)
    except ValueError:
        raise ValueError("TG_API_ID must be a valid integer")

    config = {
        'api_id': api_id,
        'api_hash': api_hash,
        'bot_token': os.environ.get('TG_BOT_TOKEN'),
        'channel_id': os.environ.get('TG_CHANNEL_ID'),
    }

    # 从 config.json 读取非敏感配置
    if config_path.exists():
        with open(config_path, encoding='utf-8') as f:
            file_config = json.load(f)
            # 合并非敏感配置
            for key in ['forward_limit', 'reaction_limit',
                       'download_dir', 'max_retries', 'retry_delay_base', 'media_types']:
                if key in file_config:
                    config[key] = file_config[key]

    return config


def create_client(
    config: dict,
    session_name: str = "tg-mgr"
) -> tuple[Client, bool]:
    """
    创建 Telegram 客户端

    Args:
        config: 配置字典，包含 api_id, api_hash
        session_name: session 名称

    Returns:
        (client, is_started): 客户端实例、客户端是否已启动
    """
    api_id = config['api_id']
    api_hash = config['api_hash']

    client_kwargs = {
        "name": str(get_sessions_dir() / session_name),
        "api_id": api_id,
        "api_hash": api_hash
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
        str(get_sessions_dir() / session_name),
        api_id=config['api_id'],
        api_hash=config['api_hash']
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
