"""转发模块入口

功能：
- 找出频道中高反应消息（次数 > 0 或 浏览量 top 10）
- 自动将符合条件的消息复制到目标频道
- 支持递归深度转发（-r 参数）

使用：
- tg forward <源频道ID或链接> [-o <目标频道ID>] [-c] [-r <深度>]
"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中
src_path = Path(__file__).parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# 从各子模块导入主要接口（保持向后兼容）
from modules.forward.main import main
from modules.forward.core import run_forward

from modules.forward.send import (
    forward_messages_batch,
    forward_single_message,
    get_channel_address,
)

from modules.forward.preview import (
    summarize_messages_for_forward,
    confirm_forward,
)

from modules.forward.recursive import (
    forward_with_recursion,
    find_messages_to_forward,
    sync_channel_for_forward,
    is_channel_forwarding_allowed,
    extract_source_channels,
)

from modules.forward.cli import (
    parse_source_arg,
    resolve_username_to_channel_id,
)

# 重新导出一些常用函数和类，使测试可以 patch modules.forward.get_client 等
from utils.telegram_client import get_client, get_config
from modules.sync import sync_channel
from database import get_db

__all__ = [
    'main',
    'run_forward',
    'forward_with_recursion',
    'find_messages_to_forward',
    'summarize_messages_for_forward',
    'confirm_forward',
    'forward_messages_batch',
    'forward_single_message',
    'is_channel_forwarding_allowed',
    'get_channel_address',
    'parse_source_arg',
    'resolve_username_to_channel_id',
    'sync_channel_for_forward',
    'extract_source_channels',
    'get_client',
    'get_config',
    'sync_channel',
]
