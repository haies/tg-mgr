"""转发模块 CLI 参数解析"""
import re

from pyrogram import Client


def parse_source_arg(arg: str) -> tuple[int | None, int | None, str | None]:
    """解析参数为 (channel_id, message_id, username)

    Args:
        arg: 频道ID或链接

    Returns:
        (channel_id, message_id, username) - message_id 为 None 表示整个频道
        username 用于延迟解析（如 jn2678 -> -1001234567890）
    """
    # 标准化：补全缺失的 https:// 前缀
    if arg.startswith("t.me/") and not arg.startswith("https://"):
        arg = "https://" + arg

    # 解析链接：https://t.me/c/1234567890/12345 格式（超级群/私有频道）
    c_pattern = r"https?://t\.me/c/(\d+)/(\d+)(?:\?.*)?$"
    c_match = re.match(c_pattern, arg)
    if c_match:
        raw_id = int(c_match.group(1))
        message_id = int(c_match.group(2))
        # t.me/c/xxxx/ 中的 xxxx 是超级群 ID，需要加 -100 前缀
        channel_id = -(raw_id + 1000000000000) if raw_id < 100000000000 else -raw_id
        return (channel_id, message_id, None)

    # 解析链接：https://t.me/username/123 格式
    link_pattern = r"https?://t\.me/([\w_]+)/(\d+)(?:\?.*)?$"
    match = re.match(link_pattern, arg)
    if match:
        identifier = match.group(1)
        message_id = int(match.group(2))
        # 用户名格式，需要延迟解析（调用 API）
        return (None, message_id, identifier)

    # 纯数字频道ID
    try:
        return (int(arg), None, None)
    except ValueError:
        print(f"[ERROR] 无法解析参数: {arg}")
        return (None, None, None)


def resolve_username_to_channel_id(client: Client, username: str) -> int | None:
    """将用户名解析为频道ID

    Args:
        client: Telegram 客户端
        username: 用户名（可以是 'jn2678' 或 '@jn2678'）

    Returns:
        频道ID，解析失败返回 None
    """
    try:
        # 确保 username 有 @ 前缀
        if not username.startswith('@'):
            username = f"@{username}"
        chat = client.get_chat(username)  # type: ignore[attr-defined]
        return chat.id  # type: ignore[attr-defined]
    except Exception as e:
        print(f"[ERROR] 无法解析用户名 {username}: {e}")
        return None