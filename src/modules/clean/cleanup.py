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

# 文件大小阈值：小于2MB视为小文件（垃圾消息判定用）
JUNK_MEDIA_SIZE_THRESHOLD = 2 * 1024 * 1024  # 2MB


def count_chinese_chars(text: str) -> int:
    """统计字符串中中文字符的数量"""
    return sum(1 for char in text if '一' <= char <= '鿿')


def is_junk_message(text: str, file_size: int | None = None, media_type: str | None = None) -> bool:
    """判断消息是否为垃圾消息

    判定规则（同时满足）：
    - 媒体类型为 photo 或 video
    - 30字以上中文 或 100字符以上英文
    - 文件大小小于 2MB
    """
    if not text:
        return False

    # 必须是有具体文字内容
    stripped = text.strip()
    links_only = TG_MSG_LINK_PATTERN.sub('', stripped).strip()
    if links_only == '' and TG_MSG_LINK_PATTERN.search(stripped):
        return False

    # 规则：长文字
    chinese_count = count_chinese_chars(text)
    if not (chinese_count >= 30 or len(text) >= 100):
        return False

    # 规则：媒体类型必须为 photo 或 video
    if media_type not in ('photo', 'video'):
        return False

    # 规则：文件大小必须小于 2MB
    if file_size is not None and file_size >= JUNK_MEDIA_SIZE_THRESHOLD:
        return False

    return True


def is_spam_text(text: str) -> bool:
    """检测纯文字消息是否为垃圾

    判定规则：
    - 所有纯文字消息都算垃圾（不论长短）
    - 排除仅包含 Telegram 消息链接的文字
    """
    if not text:
        return False

    # 排除仅包含链接的文字
    stripped = text.strip()
    links_only = TG_MSG_LINK_PATTERN.sub('', stripped).strip()
    if links_only == '' and TG_MSG_LINK_PATTERN.search(stripped):
        return False

    # 所有非空的纯文字消息都算垃圾
    return True


def find_junk_messages(conn: sqlite3.Connection) -> list:
    """查找所有垃圾消息

    检测类型：
    1. 媒体消息（photo/video）+ 长文字 + 文件小于2MB + 非媒体组成员
    2. 纯文字消息 + 推广/引流关键词
    """
    cursor = conn.cursor()

    # 类型1：photo/video 媒体消息 + 长文字（需要同时满足文字长度、文件大小条件，且不在媒体组中）
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, caption, timestamp
        FROM messages
        WHERE media_type IN ('photo', 'video')
        AND caption IS NOT NULL AND caption != ''
        AND (media_group_id IS NULL OR media_group_id = '')
    """)
    media_junk = [msg for msg in cursor.fetchall() if is_junk_message(msg[4], msg[2], msg[3])]

    # 类型2：纯文字消息 + 推广/引流关键词
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, caption, timestamp
        FROM messages
        WHERE (media_type IS NULL OR media_type = 'text' OR media_type = '')
        AND caption IS NOT NULL AND caption != ''
    """)
    text_junk = [msg for msg in cursor.fetchall() if is_spam_text(msg[4])]

    return media_junk + text_junk


def find_invalid_messages(conn: sqlite3.Connection) -> list:
    """查找所有无效消息（is_valid = 0）"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, timestamp
        FROM messages
        WHERE is_valid = 0
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
        invalid_messages = find_invalid_messages(conn)

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
        junk_messages = find_junk_messages(conn)

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
                msg_id, file_unique_id, file_size, media_type, caption, timestamp = msg
                tg_link = generate_tg_link(_channel_id, msg_id)
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