"""
Telegram链接生成工具

功能：
- 生成可点击的Telegram消息链接
- 自动处理-100前缀（如 -1001234567890 → 1234567890）
- 生成频道地址链接
"""


def generate_tg_link(chat_id: str | int, msg_id: int) -> str:
    """
    生成Telegram消息链接

    参数:
    chat_id: 频道ID（需包含-100前缀）
    msg_id: 消息ID

    返回:
    格式化后的Telegram链接
    """
    stripped_id = str(chat_id).replace("-100", "")
    return f"https://t.me/c/{stripped_id}/{msg_id}"


def get_channel_address(channel_id: int) -> str:
    """
    生成可点击的频道地址，正确处理-100前缀

    Args:
        channel_id: 频道ID（整数）

    Returns:
        格式如 "t.me/c/1234567890" 的频道地址
    """
    abs_id = abs(channel_id)
    str_id = str(abs_id)
    if str_id.startswith('100') and len(str_id) >= 13:
        return f"t.me/c/{str_id[3:]}"
    return f"t.me/c/{str_id}"
