"""清理无效和垃圾消息逻辑"""
import re
import sqlite3

from database import get_db
from utils.telegram_client import get_client, get_config
from utils.telegram_link import generate_tg_link

# Telegram 消息链接正则 (t.me/c/123/456 或 t.me/username/123)
TG_MSG_LINK_PATTERN = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me|telegramdog\.com)/'
    r'(?:c/\d+/\d+|[\w_]+/\d+)',
    re.IGNORECASE
)

# 媒体组总大小阈值：小于100KB视为超小媒体组（垃圾消息判定用）
JUNK_TINY_MEDIA_GROUP_THRESHOLD = 100 * 1024  # 100KB


def is_junk_message(text: str, file_size: int | None = None, media_type: str | None = None) -> bool:
    """判断单条媒体消息是否为垃圾

    判定规则：
    - 媒体类型为 photo 或 video
    - 文件大小小于 100KB
    """
    if media_type not in ('photo', 'video'):
        return False
    if file_size is not None and file_size >= JUNK_TINY_MEDIA_GROUP_THRESHOLD:
        return False
    return True


def find_junk_messages(conn: sqlite3.Connection, channel_id: int | str | None = None) -> list:
    """查找所有垃圾消息（可按频道过滤）

    检测类型：
    1. 单媒体（photo/video）+ 文件小于100KB（无文字或长文字均可）
    2. 纯文字消息（去掉所有链接还有纯文字）
    3. 媒体组总大小小于100KB

    Args:
        conn: 数据库连接
        channel_id: 频道ID过滤（可选）
    """
    cursor = conn.cursor()

    # 类型1：photo/video 媒体消息 + 长文字 + 文件小于100KB（不再限制媒体组）
    if channel_id is not None:
        cursor.execute("""
            SELECT message_id, file_unique_id, file_size, media_type, caption, timestamp
            FROM messages
            WHERE media_type IN ('photo', 'video')
            AND file_size < ?
            AND channel_id = ?
        """, (JUNK_TINY_MEDIA_GROUP_THRESHOLD, channel_id))
    else:
        cursor.execute("""
            SELECT message_id, file_unique_id, file_size, media_type, caption, timestamp
            FROM messages
            WHERE media_type IN ('photo', 'video')
            AND file_size < ?
        """, (JUNK_TINY_MEDIA_GROUP_THRESHOLD,))
    media_junk = [msg for msg in cursor.fetchall() if is_junk_message(msg[4], msg[2], msg[3])]

    # 类型2：媒体组总大小小于100KB
    tiny_media_groups = _find_tiny_media_groups(conn, channel_id)

    return media_junk + tiny_media_groups


def _find_tiny_media_groups(conn: sqlite3.Connection, channel_id: int | str | None = None) -> list:
    """查找媒体组总大小小于100KB的消息组

    Args:
        conn: 数据库连接
        channel_id: 频道ID过滤（可选）

    Returns:
        每条消息元组列表 (message_id, file_unique_id, file_size, media_type, caption, timestamp)
    """
    cursor = conn.cursor()

    # 查询媒体组总大小小于100KB的组（排除单成员媒体组，因为单图/单视频走类型1判断）
    if channel_id is not None:
        cursor.execute("""
            SELECT
                m.message_id,
                m.file_unique_id,
                m.file_size,
                m.media_type,
                m.caption,
                m.timestamp,
                m.media_group_id,
                SUM(m.file_size) OVER (PARTITION BY m.media_group_id) AS group_total_size,
                COUNT(*) OVER (PARTITION BY m.media_group_id) AS group_member_count
            FROM messages m
            WHERE m.media_group_id IS NOT NULL
            AND m.media_group_id != ''
            AND m.channel_id = ?
        """, (channel_id,))
    else:
        cursor.execute("""
            SELECT
                m.message_id,
                m.file_unique_id,
                m.file_size,
                m.media_type,
                m.caption,
                m.timestamp,
                m.media_group_id,
                SUM(m.file_size) OVER (PARTITION BY m.media_group_id) AS group_total_size,
                COUNT(*) OVER (PARTITION BY m.media_group_id) AS group_member_count
            FROM messages m
            WHERE m.media_group_id IS NOT NULL
            AND m.media_group_id != ''
        """)

    results = []
    seen_groups = set()

    for row in cursor.fetchall():
        msg_id, file_unique_id, file_size, media_type, caption, timestamp, media_group_id, group_total_size, group_member_count = row

        # 只处理媒体组总大小小于100KB的消息
        if group_total_size < JUNK_TINY_MEDIA_GROUP_THRESHOLD:
            # 每个媒体组只取第一条消息，避免重复标记
            if media_group_id not in seen_groups:
                seen_groups.add(media_group_id)
                results.append((msg_id, file_unique_id, file_size, media_type, caption, timestamp))

    return results


def find_invalid_messages(conn: sqlite3.Connection, channel_id: int | str | None = None) -> list:
    """查找所有无效消息（is_invalid = 1，可按频道过滤）

    Args:
        conn: 数据库连接
        channel_id: 频道ID过滤（可选）
    """
    cursor = conn.cursor()
    if channel_id is not None:
        cursor.execute("""
            SELECT message_id, file_unique_id, file_size, media_type, timestamp
            FROM messages
            WHERE is_invalid = 1 AND channel_id = ?
        """, (channel_id,))
    else:
        cursor.execute("""
            SELECT message_id, file_unique_id, file_size, media_type, timestamp
            FROM messages
            WHERE is_invalid = 1
        """)

    return cursor.fetchall()


def run_deinvalid(delete: bool = False, channel_id: str | None = None) -> dict[str, int]:
    """无效消息检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取

    Returns:
        按media_type统计的待清理消息数量 dict
    """
    from modules.clean.deduplicate import delete_message_safely

    config = get_config()
    _channel_id = channel_id if channel_id else config["channel_id"]

    stats: dict[str, int] = {}

    with get_db() as conn:
        invalid_messages = find_invalid_messages(conn, _channel_id)

        if not invalid_messages:
            print("[CLEAN] 未检测到无效消息")
            return stats

        # 按media_type统计
        for msg in invalid_messages:
            media_type = msg[3] or "unknown"
            stats[media_type] = stats.get(media_type, 0) + 1

        total_deleted = 0
        total_failed = 0

        print(f"[CLEAN] 检测到 {len(invalid_messages)} 条无效消息:")
        client = None
        if delete:
            client = get_client("tg-mgr")
            client.start()  # type: ignore[unused-coroutine]

        try:
            for idx, msg in enumerate(invalid_messages, 1):
                msg_id, file_unique_id, file_size, media_type, timestamp = msg
                tg_link = generate_tg_link(_channel_id, msg_id)

                print(f"\n消息 #{idx} (ID: {msg_id}, {media_type}, {file_size} bytes):")
                print(f"  - 链接: {tg_link}")
                print(f"  - 时间: {timestamp}")

                if delete:
                    assert client is not None
                    success = delete_message_safely(client, conn, msg_id, _channel_id)
                    if success:
                        total_deleted += 1
                    else:
                        total_failed += 1

            if delete:
                conn.commit()
                print(
                    f"\n[CLEAN] 清理完成 - 共处理 {len(invalid_messages)} 条无效消息, 成功删除 {total_deleted} 条, 失败 {total_failed} 条"
                )
            else:
                print("\n[CLEAN] 检测完成")

        finally:
            if client and client.is_connected:
                client.stop()  # type: ignore[unused-coroutine]

        return stats


def run_dejunk(delete: bool = False, channel_id: str | None = None) -> dict[str, int]:
    """垃圾消息检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取

    Returns:
        按media_type统计的待清理消息数量 dict
    """
    from modules.clean.deduplicate import delete_message_safely

    config = get_config()
    _channel_id = channel_id if channel_id else config["channel_id"]

    stats: dict[str, int] = {}

    with get_db() as conn:
        junk_messages = find_junk_messages(conn, _channel_id)

        if not junk_messages:
            print("[CLEAN] 未检测到垃圾消息")
            return stats

        # 按media_type统计
        for msg in junk_messages:
            media_type = msg[3] or "text"
            stats[media_type] = stats.get(media_type, 0) + 1

        total_deleted = 0
        total_failed = 0

        print(f"[CLEAN] 检测到 {len(junk_messages)} 条垃圾消息:")
        client = None
        if delete:
            client = get_client("tg-mgr")
            client.start()  # type: ignore[unused-coroutine]

        try:
            for idx, msg in enumerate(junk_messages, 1):
                tg_link = generate_tg_link(_channel_id, msg[0])
                # 类型3媒体组消息有8个元素，多了 media_group_id 和 group_member_count
                if len(msg) == 8:
                    msg_id, file_unique_id, file_size, media_type, caption, timestamp, _, group_count = msg
                    print(f"  [{idx}] {tg_link} | {media_type} 媒体组({group_count}个) | 总大小 {file_size // 1024}KB | 文字 {len(caption) if caption else 0} 字")
                else:
                    msg_id, file_unique_id, file_size, media_type, caption, timestamp = msg
                    size_kb = file_size // 1024 if file_size else 0
                    text_len = len(caption) if caption else 0
                    print(f"  [{idx}] {tg_link} | {media_type} {size_kb}KB | 文字 {text_len} 字")

                if delete:
                    assert client is not None
                    success = delete_message_safely(client, conn, msg_id, _channel_id)
                    if success:
                        total_deleted += 1
                    else:
                        total_failed += 1

            if delete:
                conn.commit()
                print(f"[CLEAN] 清理完成，已删除 {total_deleted} 条")
            else:
                pass  # 检测完成，不显示多余信息

        finally:
            if client and client.is_connected:
                client.stop()  # type: ignore[unused-coroutine]

        return stats