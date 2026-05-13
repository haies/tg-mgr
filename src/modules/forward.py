"""
高反应消息复制模块

功能：
- 找出频道中高反应消息（次数 > 0 或 浏览量 top 10）
- 自动将符合条件的消息复制到目标频道
- 支持递归深度转发（-r 参数）

使用：
- tg forward <源频道ID或链接> [-o <目标频道ID>] [-c] [-r <深度>]

递归深度规则：
- 参数中的频道 = 第 1 层
- 第 1 层频道中高反应消息的来源频道 = 第 2 层
- 依此类推...
"""

import logging
import re
import sqlite3
import sys
import time
from typing import Any

from pyrogram import Client, errors
from pyrogram.types import InputMedia, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio, InputMediaAnimation, Message

from database import get_database_path, get_db_connection
from database.query import (
    find_high_reaction_messages as query_high_reaction,
)
from database.query import (
    find_reaction_messages_over_threshold,
)
from utils.media import row_to_reaction_dict
from utils.telegram_client import get_client, get_config, get_log_path
from utils.telegram_link import get_channel_address

logger = logging.getLogger(__name__)

# 默认递归深度
DEFAULT_RECURSION_DEPTH = 5


def parse_source_arg(arg: str) -> tuple[int | None, int | None, str | None]:
    """解析参数为 (channel_id, message_id, username)

    Args:
        arg: 频道ID或链接

    Returns:
        (channel_id, message_id, username) - message_id 为 None 表示整个频道
        username 用于延迟解析（如 jn2678 -> -1001234567890）
    """
    # 解析链接：https://t.me/c/1234567890/12345 或 https://t.me/username/123?single
    link_pattern = r"https?://t\.me/([\w_]+)/(\d+)(?:\?.*)?$"
    match = re.match(link_pattern, arg)
    if match:
        identifier = match.group(1)
        message_id = int(match.group(2))

        # 检查是否是数字 ID（私有频道格式 t.me/c/数字/数字）
        if identifier.isdigit():
            raw_id = int(identifier)
            # t.me/c/xxxx/ 中的 xxxx 是超级群 ID，需要加 -100 前缀
            channel_id = -(raw_id + 1000000000000) if raw_id < 100000000000 else -raw_id
            return (channel_id, message_id, None)
        else:
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
        chat = client.get_chat(username)
        return chat.id
    except Exception as e:
        print(f"[ERROR] 无法解析用户名 {username}: {e}")
        return None


def find_messages_to_forward(conn: sqlite3.Connection, channel_id: int) -> list[dict[str, Any]]:
    """查找要转发的消息（高反应优先，否则用浏览量）

    Args:
        conn: 数据库连接
        channel_id: 频道ID

    Returns:
        消息列表，每条消息包含 message_id, source_id 等
    """
    # 优先：反应 > 0 的消息
    over0_results = find_reaction_messages_over_threshold(conn, threshold=0, limit=50)
    over0 = {row[0]: row for row in over0_results}

    if len(over0) > 0:
        # 如果 >0 的消息超过 10 条，全部转发
        if len(over0) > 10:
            results = []
            for msg_id, row in sorted(over0.items(), key=lambda x: x[1][3], reverse=True):
                results.append(row_to_reaction_dict((msg_id, row[1], row[2], row[3])))
            return results
        # 否则转发 top 10
        results = []
        for row in query_high_reaction(conn, min_total=0, limit=10):
            results.append(row_to_reaction_dict(row))
        return results

    # Fallback：浏览量 > 0 的 top 10
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT message_id, views, source_id, media_type
        FROM messages
        WHERE is_valid = 1 AND views > 0
        ORDER BY views DESC
        LIMIT 10
    """,
    )
    results = []
    for row in cursor.fetchall():
        results.append({
            "message_id": row[0],
            "views": row[1],
            "source_id": row[2],
            "media_type": row[3],
        })
    return results


def extract_source_channels(messages: list[dict[str, Any]]) -> list[int]:
    """从消息列表中提取来源频道ID

    Args:
        messages: 消息列表

    Returns:
        来源频道ID列表（去重）
    """
    source_channels = set()
    for msg in messages:
        source_id = msg.get("source_id")
        if source_id and source_id < 0:  # 频道ID通常是负数
            source_channels.add(source_id)
    return list(source_channels)


def sync_channel_for_forward(channel_id: int) -> None:
    """为转发同步频道数据（使用临时数据库）"""
    from modules.sync import sync_channel

    temp_db_path = get_database_path().with_suffix(".tmp.db")
    original_db_path = get_database_path()

    try:
        sync_channel(channel_id=str(channel_id), db_path=str(temp_db_path))
    finally:
        pass  # sync_channel handles env cleanup internally

    # 同步成功后，替换旧数据库
    if temp_db_path.exists():
        if original_db_path.exists():
            original_db_path.unlink()
        temp_db_path.rename(original_db_path)


def forward_single_message(
    client: Client,
    source_channel_id: int,
    target_channel_id: int,
    message_id: int,
) -> bool:
    """转发单条消息

    Args:
        client: Telegram 客户端
        source_channel_id: 源频道ID
        target_channel_id: 目标频道ID
        message_id: 消息ID

    Returns:
        True if successful, False otherwise
    """
    try:
        # 检查是否是媒体组的一部分
        group_msg = _get_original_media_group_message(client, source_channel_id, message_id)
        if group_msg and group_msg.media_group_id:
            # 媒体组：使用 send_media_group 转发
            media_group_messages = _get_media_group_messages(
                client, source_channel_id, group_msg.media_group_id, message_id
            )
            return _forward_media_group(client, source_channel_id, target_channel_id, media_group_messages)
        else:
            # 普通消息：直接复制
            client.copy_message(
                chat_id=target_channel_id,
                from_chat_id=source_channel_id,
                message_id=message_id,
            )
        return True
    except errors.FloodWait as e:
        wait = max(e.value, 5)
        time.sleep(wait)
        return False
    except (errors.Forbidden, errors.BadRequest):
        return False
    except Exception:
        return False


def _get_original_media_group_message(
    client: Client, channel_id: int, message_id: int
) -> Message | None:
    """获取消息所在的媒体组原消息"""
    try:
        # 先调用 get_chat 建立会话，解决 CHAT_ID_INVALID 问题
        client.get_chat(channel_id)
        msgs = client.get_messages(channel_id, message_id)
        return msgs
    except Exception:
        return None


def _get_media_group_messages(
    client: Client, channel_id: int, media_group_id: str, center_msg_id: int | None = None
) -> list[Message]:
    """获取媒体组的所有消息

    Args:
        client: Telegram 客户端
        channel_id: 频道ID
        media_group_id: 媒体组ID
        center_msg_id: 中心消息ID，用于双向搜索

    Returns:
        媒体组消息列表（按 message_id 排序）
    """
    try:
        # 先调用 get_chat 建立会话
        client.get_chat(channel_id)

        messages = []
        seen_ids = set()

        # 如果有 center_msg_id，先获取它作为起点
        if center_msg_id:
            try:
                center_msg = client.get_messages(channel_id, center_msg_id)
                if center_msg and hasattr(center_msg, "media_group_id") and center_msg.media_group_id == media_group_id:
                    messages.append(center_msg)
                    seen_ids.add(center_msg.id)
            except Exception:
                pass

        # 向后搜索：从 center_msg_id 开始向后遍历（消息ID增大的方向）
        if center_msg_id:
            for offset in range(1, 20):  # 最多向后搜索 20 条
                msg_id = center_msg_id + offset
                if msg_id in seen_ids:
                    continue
                try:
                    msg = client.get_messages(channel_id, msg_id)
                    if msg and hasattr(msg, "media_group_id") and msg.media_group_id == media_group_id:
                        messages.append(msg)
                        seen_ids.add(msg_id)
                    elif msg and (not hasattr(msg, "media_group_id") or msg.media_group_id is None):
                        # 遇到非媒体组消息，停止向后搜索
                        break
                except Exception:
                    break

        # 向前搜索：从 center_msg_id 开始向前搜索（消息ID减小的方向）
        if center_msg_id:
            for offset in range(1, 20):  # 最多向前搜索 20 条
                msg_id = center_msg_id - offset
                if msg_id in seen_ids or msg_id <= 0:
                    continue
                try:
                    msg = client.get_messages(channel_id, msg_id)
                    if msg and hasattr(msg, "media_group_id") and msg.media_group_id == media_group_id:
                        messages.append(msg)
                        seen_ids.add(msg_id)
                    elif msg and (not hasattr(msg, "media_group_id") or msg.media_group_id is None):
                        # 遇到非媒体组消息，停止向前搜索
                        break
                except Exception:
                    break
        else:
            # 没有 center_msg_id，回退到旧的遍历方式（取最新消息）
            for msg in client.get_chat_history(channel_id, limit=100):
                if hasattr(msg, "media_group_id") and msg.media_group_id == media_group_id:
                    if msg.id not in seen_ids:
                        messages.append(msg)
                        seen_ids.add(msg.id)
                        if len(messages) >= 10:
                            break

        # 按 message_id 排序
        messages.sort(key=lambda m: m.id)
        return messages
    except Exception:
        return []


def _forward_media_group(
    client: Client,
    source_channel_id: int,
    target_channel_id: int,
    messages: list[Message],
) -> bool:
    """转发媒体组

    Args:
        client: Telegram 客户端
        source_channel_id: 源频道ID
        target_channel_id: 目标频道ID
        messages: 媒体组消息列表

    Returns:
        True if successful, False otherwise
    """
    if not messages:
        return False

    # 按 message_id 排序确保顺序正确
    messages = sorted(messages, key=lambda m: m.id)

    # 准备媒体组输入
    media_list = []
    for msg in messages:
        media_input = _prepare_media_for_send(msg)
        if media_input:
            media_list.append(media_input)

    if not media_list:
        return False

    try:
        client.send_media_group(target_channel_id, media_list)
        return True
    except errors.FloodWait as e:
        wait = max(e.value, 5)
        time.sleep(wait)
        return False
    except Exception:
        return False


def _prepare_media_for_send(message: Message) -> InputMedia | None:
    """准备消息用于 send_media_group，返回正确的 InputMedia 类型"""
    try:
        caption = message.caption or ""
        if message.photo:
            return InputMediaPhoto(message.photo.file_id, caption=caption)
        elif message.video:
            return InputMediaVideo(message.video.file_id, caption=caption)
        elif message.document:
            return InputMediaDocument(message.document.file_id, caption=caption)
        elif message.audio:
            return InputMediaAudio(message.audio.file_id, caption=caption)
        elif message.animation:
            return InputMediaAnimation(message.animation.file_id, caption=caption)
        elif message.voice:
            # voice 和 video_note 不支持媒体组，使用 InputMedia
            return InputMedia(message.voice.file_id, caption=caption)
        elif message.video_note:
            return InputMedia(message.video_note.file_id, caption=caption)
        elif message.text:
            # 纯文本消息不是媒体组的一部分
            return None
        return None
    except Exception:
        return None


def forward_messages_batch(
    source_channel_id: int,
    target_channel_ids: list[int],
    messages: list[dict[str, Any]],
    check_exists: bool = False,
) -> tuple[int, int, int]:
    """批量转发消息

    Args:
        source_channel_id: 源频道ID
        target_channel_ids: 目标频道ID列表
        messages: 要转发的消息列表
        check_exists: 是否检查消息是否存在

    Returns:
        (forwarded, skipped, failed)
    """
    from datetime import datetime

    log_file = get_log_path("forward.log")
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_date = not log_file.exists()

    forwarded = 0
    skipped = 0
    failed = 0

    with get_client("tg-mgr") as client:
        # 确保已加入目标频道
        for target_id in target_channel_ids:
            try:
                client.get_chat(target_id)
            except Exception:
                if join_channel(client, target_id):
                    print(f"[FORWARD] 已加入目标频道 {target_id}")

        for msg in messages:
            msg_id = msg["message_id"]
            link = f"{get_channel_address(source_channel_id)}/{msg_id}"

            for target_id in target_channel_ids:
                if check_exists:
                    if message_exists_in_channel(client, target_id, msg_id):
                        print(f"[FORWARD] 跳过（已存在）: {link} -> {target_id}")
                        skipped += 1
                        continue

                try:
                    # 检查是否是媒体组消息
                    original_msg = _get_original_media_group_message(client, source_channel_id, msg_id)
                    if original_msg and original_msg.media_group_id:
                        # 媒体组消息，使用 send_media_group 转发
                        media_group_msgs = _get_media_group_messages(client, source_channel_id, original_msg.media_group_id, msg_id)
                        if media_group_msgs:
                            # 有完整媒体组，转发整个组
                            if _forward_media_group(client, source_channel_id, target_id, media_group_msgs):
                                forwarded += 1
                                print(f"[FORWARD] 转发成功(媒体组): {link} -> {target_id}")
                            else:
                                failed += 1
                                continue
                        else:
                            # 媒体组消息但找不到其他同组消息，降级为 copy_message
                            client.copy_message(
                                chat_id=target_id,
                                from_chat_id=source_channel_id,
                                message_id=msg_id,
                            )
                            forwarded += 1
                            print(f"[FORWARD] 转发成功(媒体组降级): {link} -> {target_id}")
                    else:
                        # 普通消息，使用 copy_message
                        client.copy_message(
                            chat_id=target_id,
                            from_chat_id=source_channel_id,
                            message_id=msg_id,
                        )
                        forwarded += 1
                        print(f"[FORWARD] 转发成功: {link} -> {target_id}")

                    # 写入日志
                    if write_date:
                        with open(log_file, "w") as f:
                            f.write(f"{today}\n")
                        write_date = False
                    with open(log_file, "a") as f:
                        f.write(f"{link}\n")

                except errors.Forbidden:
                    print(f"[FORWARD] 频道 {target_id} 禁止复制")
                    break
                except errors.BadRequest as e:
                    if "CHAT_ADMIN_REQUIRED" in str(e):
                        print(f"[FORWARD] 频道 {target_id} 需要管理员权限")
                        break
                    if "CHAT_FORWARDS_RESTRICTED" in str(e):
                        print(f"[FORWARD] 频道 {target_id} 禁止转发内容")
                        break
                    print(f"[FORWARD] 转发失败: {link} - {e}")
                    failed += 1
                except errors.FloodWait as e:
                    wait = max(e.value, 5)
                    print(f"[FORWARD] FloodWait: 等待 {wait} 秒...")
                    time.sleep(wait)
                    # 重试一次
                    if forward_single_message(client, source_channel_id, target_id, msg_id):
                        forwarded += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"[FORWARD] 转发失败: {link} - {e}")
                    failed += 1

                time.sleep(0.5)

    return forwarded, skipped, failed


def forward_with_recursion(
    source_channels: list[int],
    target_channel: int,
    current_depth: int = 1,
    max_depth: int = DEFAULT_RECURSION_DEPTH,
    processed_channels: set[int] | None = None,
    check_exists: bool = False,
) -> tuple[int, int, int]:
    """递归转发高反应消息

    Args:
        source_channels: 当前层级的源频道列表
        target_channel: 目标频道ID
        current_depth: 当前深度
        max_depth: 最大深度
        processed_channels: 已处理的频道集合
        check_exists: 是否检查消息是否存在

    Returns:
        (total_forwarded, total_skipped, total_failed)
    """
    if processed_channels is None:
        processed_channels = set()

    # 检查深度限制
    if current_depth > max_depth:
        return 0, 0, 0

    total_forwarded = 0
    total_skipped = 0
    total_failed = 0

    for channel_id in source_channels:
        if channel_id in processed_channels:
            print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 已处理过，跳过")
            continue

        # 检查频道转发权限
        print(f"[FORWARD] 深度 {current_depth}: 检查频道 {channel_id}...")
        with get_client("tg-mgr") as client:
            if not is_channel_forwarding_allowed(client, channel_id):
                print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 禁止转发，跳过")
                continue

        # 同步频道数据
        print(f"[FORWARD] 深度 {current_depth}: 同步频道 {channel_id}...")
        try:
            sync_channel_for_forward(channel_id)
        except Exception as e:
            print(f"[FORWARD] 深度 {current_depth}: 同步频道 {channel_id} 失败: {e}")
            continue

        conn = get_db_connection()

        # 查找要转发的消息
        messages = find_messages_to_forward(conn, channel_id)
        if messages:
            total = sum(m.get("positive", 0) + m.get("heart", 0) for m in messages)
            total_views = sum(m.get("views", 0) for m in messages)
            print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 找到 {len(messages)} 条消息 (反应数: {total}, 浏览量: {total_views})")
        else:
            print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 找到 0 条消息")

        if messages:
            # 转发到目标
            f, s, fa = forward_messages_batch(channel_id, [target_channel], messages, check_exists)
            total_forwarded += f
            total_skipped += s
            total_failed += fa

            # 递归处理下一层
            if current_depth < max_depth:
                next_channels = extract_source_channels(messages)
                if next_channels:
                    print(f"[FORWARD] 深度 {current_depth}: 发现来源频道 {len(next_channels)} 个")
                    nf, ns, nfa = forward_with_recursion(
                        next_channels,
                        target_channel,
                        current_depth + 1,
                        max_depth,
                        processed_channels,
                        check_exists,
                    )
                    total_forwarded += nf
                    total_skipped += ns
                    total_failed += nfa
                else:
                    print(f"[FORWARD] 深度 {current_depth}: 未找到来源频道，停止递归")

        conn.close()
        processed_channels.add(channel_id)

    return total_forwarded, total_skipped, total_failed


def message_exists_in_channel(client: Client, target_channel_id: int, source_msg_id: int) -> bool:
    """检查消息是否已存在于目标频道"""
    try:
        for msg in client.get_chat_history(target_channel_id, limit=100):
            if msg.id == source_msg_id:
                return True
        return False
    except Exception:
        return False


def join_channel(client: Client, channel_id: int) -> bool:
    """尝试加入频道"""
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


def is_channel_forwarding_allowed(client: Client, channel_id: int) -> bool:
    """检查频道是否允许转发"""
    try:
        chat = client.get_chat(channel_id)
        if hasattr(chat, "has_protected_content") and chat.has_protected_content:
            return False
        return True
    except errors.BadRequest as e:
        if "CHAT_FORWARDS_RESTRICTED" in str(e):
            return False
        raise
    except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
        return False


def main():
    """主执行流程"""
    import argparse

    parser = argparse.ArgumentParser(description="高反应消息转发模块")
    parser.add_argument("sources", nargs="+", help="源频道ID或消息链接")
    parser.add_argument("-o", "--target", type=int, help="目标频道ID")
    parser.add_argument("-c", "--check", action="store_true", help="转发前检查目标频道是否已存在")
    parser.add_argument(
        "-r", "--depth", type=int, nargs="?", const=DEFAULT_RECURSION_DEPTH, default=None,
        help=f"递归深度（-r3 或 -r 3，默认{DEFAULT_RECURSION_DEPTH}，0表示不递归）"
    )

    args = parser.parse_args()

    config = get_config()
    target_channel_id = args.target if args.target else config.get("channel_id")

    if not target_channel_id:
        print("[ERROR] 未指定目标频道，且环境变量 TG_CHANNEL_ID 未配置")
        sys.exit(1)

    # 解析递归深度
    recursion_depth = args.depth if args.depth is not None else config.get("recursion_depth", DEFAULT_RECURSION_DEPTH)

    # 分离链接和频道ID
    channel_ids = []
    link_messages = []  # [(channel_id, message_id, username), ...]

    for source in args.sources:
        parsed = parse_source_arg(source)
        if parsed[0] is None and parsed[2] is None:
            # 无法解析，跳过
            continue
        if parsed[1] is not None:
            # 链接：直接转发，不递归
            link_messages.append(parsed)  # (channel_id or None, message_id, username or None)
        else:
            channel_ids.append(parsed[0])

    # 处理链接参数（直接转发，不递归）
    if link_messages:
        print(f"[FORWARD] 处理 {len(link_messages)} 条直接转发...")
        with get_client("tg-mgr") as client:
            # 确保已加入目标频道
            try:
                client.get_chat(target_channel_id)
            except Exception:
                if join_channel(client, target_channel_id):
                    print(f"[FORWARD] 已加入目标频道 {target_channel_id}")

            for channel_id, msg_id, username in link_messages:
                # 解析用户名（如果需要）
                if channel_id is None and username:
                    resolved_id = resolve_username_to_channel_id(client, username)
                    if resolved_id is None:
                        print(f"[ERROR] 无法解析用户名 {username}，跳过")
                        continue
                    channel_id = resolved_id

                link = f"{get_channel_address(channel_id)}/{msg_id}"
                try:
                    # 检查是否是媒体组消息
                    original_msg = _get_original_media_group_message(client, channel_id, msg_id)
                    if original_msg and original_msg.media_group_id:
                        # 媒体组消息，使用 send_media_group 转发
                        media_group_msgs = _get_media_group_messages(client, channel_id, original_msg.media_group_id, msg_id)
                        if media_group_msgs:
                            # 有完整媒体组，转发整个组
                            if _forward_media_group(client, channel_id, target_channel_id, media_group_msgs):
                                print(f"[FORWARD] 直接转发成功(媒体组): {link}")
                            else:
                                print(f"[FORWARD] 直接转发失败(媒体组): {link}")
                        else:
                            # 媒体组消息但找不到其他同组消息，降级为 copy_message
                            client.copy_message(
                                chat_id=target_channel_id,
                                from_chat_id=channel_id,
                                message_id=msg_id,
                            )
                            print(f"[FORWARD] 直接转发成功(媒体组降级): {link}")
                    else:
                        # 普通消息，使用 copy_message
                        client.copy_message(
                            chat_id=target_channel_id,
                            from_chat_id=channel_id,
                            message_id=msg_id,
                        )
                        print(f"[FORWARD] 直接转发成功: {link}")
                except Exception as e:
                    print(f"[FORWARD] 直接转发失败: {link} - {e}")

    # 处理频道参数（支持递归）
    if channel_ids:
        if recursion_depth <= 0:
            # 不递归，只处理当前频道
            print(f"[FORWARD] 处理 {len(channel_ids)} 个频道（无递归）...")
            for channel_id in channel_ids:
                print(f"[FORWARD] ========== 处理频道: {channel_id} ==========")

                # 检查权限
                with get_client("tg-mgr") as client:
                    if not is_channel_forwarding_allowed(client, channel_id):
                        print(f"[FORWARD] 频道 {channel_id} 禁止转发，跳过")
                        continue

                # 同步
                print(f"[FORWARD] 同步频道 {channel_id}...")
                try:
                    sync_channel_for_forward(channel_id)
                except Exception as e:
                    print(f"[FORWARD] 同步失败: {e}")
                    continue

                conn = get_db_connection()
                messages = find_messages_to_forward(conn, channel_id)
                if messages:
                    total = sum(m.get("positive", 0) + m.get("heart", 0) for m in messages)
                    total_views = sum(m.get("views", 0) for m in messages)
                    print(f"[FORWARD] 频道 {channel_id} 找到 {len(messages)} 条消息 (反应数: {total}, 浏览量: {total_views})")
                else:
                    print(f"[FORWARD] 频道 {channel_id} 找到 0 条消息")
                if messages:
                    f, s, fa = forward_messages_batch(channel_id, [target_channel_id], messages, args.check)
                    print(f"[FORWARD] 完成: 转发 {f}, 跳过 {s}, 失败 {fa}")
                conn.close()
        else:
            # 递归转发
            print(f"[FORWARD] 处理 {len(channel_ids)} 个频道（递归深度 {recursion_depth}）...")
            total_f, total_s, total_fa = forward_with_recursion(
                channel_ids,
                target_channel_id,
                current_depth=1,
                max_depth=recursion_depth,
                check_exists=args.check,
            )
            print("\n[FORWARD] ========== 全部完成 ==========")
            print(f"[FORWARD] 总计: 转发 {total_f}, 跳过 {total_s}, 失败 {total_fa}")


# 向后兼容别名
def find_high_reaction_messages(channel_id: int, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """向后兼容别名 - 使用 find_messages_to_forward"""
    return find_messages_to_forward(conn, channel_id)


if __name__ == "__main__":
    main()
