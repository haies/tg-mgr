"""转发模块 CLI 入口"""
import argparse

from modules.forward.cli import parse_source_arg, resolve_username_to_channel_id
from modules.forward.core import run_forward
from modules.forward.recursive import sync_channel_for_forward, is_channel_forwarding_allowed, forward_with_recursion
from modules.forward.recursive import find_messages_to_forward
from modules.forward.preview import summarize_messages_for_forward, confirm_forward
from modules.forward.send import forward_messages_batch, get_channel_address
from modules.forward.force import _force_send_single_message, _force_send_media_group
from utils.telegram_client import DEFAULT_CONFIG, get_client, get_config
from modules.sync import sync_channel
from database import get_db


def _get_reaction_total(message):
    """从消息中提取反应总数"""
    from utils.media import extract_reaction_data
    return extract_reaction_data(message).total


def _build_stats_str(total: int, views: int, is_media_group: bool = False) -> str:
    """构建统计信息字符串"""
    parts = []
    if total > 0:
        parts.append(f"反应:{total}")
    if views > 0:
        parts.append(f"浏览:{views}")
    if is_media_group:
        parts.append("媒体组")
    if parts:
        return f" ({', '.join(parts)})"
    return ""


def _join_channel(client, channel_id: int) -> bool:
    """尝试加入频道"""
    from pyrogram import errors
    try:
        client.join_chat(channel_id)
        return True
    except Exception:
        pass
    try:
        chat = client.get_chat(channel_id)
        if hasattr(chat, "username") and chat.username:
            client.join_chat(f"https://t.me/{chat.username}")
            return True
    except Exception:
        pass
    return False


def main():
    parser = argparse.ArgumentParser(description="高反应消息转发模块")
    parser.add_argument("channels", nargs="+", help="源频道ID或消息链接")
    parser.add_argument("-o", "--target", type=int, help="目标频道ID")
    parser.add_argument("-c", "--check", action="store_true", help="转发前检查目标频道是否已存在")
    parser.add_argument("-r", "--depth", type=int, nargs="?", const=5, default=None,
        help="递归深度（-r3 或 -r 3，默认5，0表示不递归）")
    parser.add_argument("-f", "--force", action="store_true",
        help="强制转发禁止转发的消息（通过复制内容而非转发）")
    parser.add_argument("-l", "--limit", type=int, default=None,
        help="高反应消息数量限制（默认从配置文件读取）")
    parser.add_argument("-v", "--views-limit", type=int, dest="views_limit", default=None,
        help="高浏览量消息数量限制（默认从配置文件读取）")

    args = parser.parse_args()
    run_forward(args)


if __name__ == "__main__":
    main()