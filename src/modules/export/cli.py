"""命令行参数解析"""
import argparse
import re

from utils.telegram_client import get_config


def parse_export_args(args: list) -> argparse.Namespace:
    """解析导出命令参数

    支持：
    - 频道ID（如 -1001234567890 或 1234567890）
    - 消息地址（如 https://t.me/c/1234567890/100）
    - 混合输入

    Args:
        args: 命令行参数列表

    Returns:
        Namespace包含 channel_ids 和 message_ids
    """
    parser = argparse.ArgumentParser(
        description="Telegram 频道导出工具", usage="./tg export [channel_id|message_url]..."
    )
    parser.add_argument("-p", "--preview", action="store_true", help="下载前预览并确认")
    parser.add_argument("-l", "--limit", type=int, default=None, help="文件大小限制(MB)")
    parser.add_argument("channels", nargs="*", help="频道ID或消息地址，多个输入用空格分隔")

    parsed = parser.parse_args(args)

    channel_ids = []
    message_ids = []

    for input_str in parsed.channels:
        # 判断是消息地址还是频道ID
        if input_str.startswith("https://t.me/c/") or input_str.startswith("http://t.me/c/"):
            # 解析消息地址: https://t.me/c/{channel_id}/{message_id}
            # 注意：t.me/c/{chat_id} 中的 chat_id 需要加上 -100 前缀才是完整的频道 ID
            match = re.match(r"https?://t\.me/c/(\d+)/(\d+)", input_str)
            if match:
                channel_ids.append(f"-100{match.group(1)}")
                message_ids.append(int(match.group(2)))
            else:
                # 地址格式不正确，作为频道ID处理
                channel_ids.append(input_str)
        elif input_str.startswith("-100"):
            # 带-100前缀的频道ID
            channel_ids.append(input_str)
        elif input_str.lstrip("-").isdigit():
            # 纯数字频道ID（可能带负号）
            channel_ids.append(input_str)
        else:
            # 其他格式，作为频道ID处理
            channel_ids.append(input_str)

    # 如果没有传入参数，使用配置文件中的默认频道
    if not channel_ids:
        config = get_config()
        default_channel = config.get("channel_id")
        if default_channel:
            channel_ids = [str(default_channel)]

    return argparse.Namespace(channel_ids=channel_ids, message_ids=message_ids, preview=parsed.preview, limit=parsed.limit)