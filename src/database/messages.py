"""
Messages 表操作模块

提供 messages 表的数据库操作
"""

import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrogram import types


def init_database(conn: sqlite3.Connection) -> None:
    """初始化数据库表结构"""

    cursor = conn.cursor()

    # 确保channels表存在
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)

    # 创建索引以优化查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_unique_id ON messages(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON messages(media_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_valid ON messages(is_valid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_duplicate ON messages(is_duplicate)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")

    conn.commit()


def get_last_processed_id(conn: sqlite3.Connection) -> int:
    """获取最后处理的消息ID，用于断点续同步"""
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(message_id) FROM messages")
    result = cursor.fetchone()
    return result[0] or 0


def insert_messages(
    cursor: sqlite3.Cursor,
    messages: list,
    seen_files: set,
) -> tuple[list, list, int]:
    """批量插入消息，返回 (new_files, duplicates, skipped)

    Args:
        cursor: 数据库游标
        messages: 消息列表
        seen_files: 已见过的文件唯一ID集合

    Returns:
        (new_files, duplicates, skipped_count)
    """
    from utils.media import extract_media_info, extract_reaction_data, extract_source_id

    new_files = []
    duplicates = []
    skipped = 0

    for message in messages:
        # 使用共享函数提取媒体信息
        media_info = extract_media_info(message)

        # Skip if no valid file_unique_id
        if not media_info.file_unique_id or media_info.file_unique_id == "":
            skipped += 1
            continue

        # Check for duplicates
        if media_info.file_unique_id in seen_files:
            duplicates.append(
                (message.id, media_info.file_unique_id, media_info.file_size, media_info.media_type)
            )
        else:
            # 使用共享函数提取反应数据
            reaction = extract_reaction_data(message)

            # Check message validity
            is_valid = 0 if _check_restricted(message) else 1

            # 使用共享函数提取源频道 ID
            source_id = extract_source_id(message)

            # 提取消息文本
            caption = message.caption or message.text or ""

            new_files.append(
                (
                    message.id,
                    media_info.file_unique_id,
                    media_info.file_size,
                    media_info.media_type,
                    caption,
                    0,
                    is_valid,
                    json.dumps({"positive": reaction.positive, "heart": reaction.heart, "total": reaction.total}),
                    source_id,
                    media_info.views,
                )
            )
            seen_files.add(media_info.file_unique_id)

    # Process duplicates in bulk
    if duplicates:
        for duplicate in duplicates:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, is_duplicate, views) "
                    "VALUES (?, ?, ?, ?, 1, 0)",
                    duplicate,
                )
            except sqlite3.IntegrityError:
                continue

    # Process new messages with bulk insert
    if new_files:
        try:
            cursor.executemany(
                "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id, views) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                new_files,
            )
        except sqlite3.IntegrityError:
            for new_file in new_files:
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id, views) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        new_file,
                    )
                except sqlite3.IntegrityError:
                    pass

    return new_files, duplicates, skipped


def _check_restricted(message: "types.Message") -> bool:
    """检查消息是否受限"""

    # 1. 基础类型检查
    if not message or hasattr(message, "empty") and message.empty:
        return True

    # 2. 检查消息本身的限制原因 (Message Level)
    restrictions = getattr(message, "restrictions", None)
    if restrictions:
        for r in restrictions:
            reason = getattr(r, "reason", "").lower()
            hard_restrictions = ["copyright", "violence", "scam", "terms", "user_opt_out"]
            if any(hard_reason in reason for hard_reason in hard_restrictions):
                return True

    # 3. 检查转发源频道 (Chat Level)
    source_chat = getattr(message, "forward_from_chat", None)
    if source_chat:
        restrictions = getattr(source_chat, "restrictions", None)
        if restrictions:
            for r in restrictions:
                reason = getattr(r, "reason", "").lower()
                hard_restrictions = ["copyright", "violence", "scam", "terms", "user_opt_out"]
                if any(hard_reason in reason for hard_reason in hard_restrictions):
                    return True

    # 4. 媒体有效性深度检查
    if message.media:
        media_obj = message.video or message.photo or message.document or message.animation
        if media_obj:
            if not getattr(media_obj, "file_id", None):
                return True

    return False


def update_message_duplicate(conn: sqlite3.Connection, message_id: int) -> None:
    """更新消息为重复状态"""
    cursor = conn.cursor()
    cursor.execute("UPDATE messages SET is_duplicate = 1 WHERE message_id = ?", (message_id,))
    conn.commit()


def find_duplicates(conn: sqlite3.Connection) -> list:
    """查找所有重复媒体组（基于文件唯一ID）

    使用窗口函数在单次查询中获取所有待删除消息ID，避免 N+1 问题
    """
    cursor = conn.cursor()

    # 使用窗口函数一次性获取所有重复消息
    cursor.execute("""
        WITH RankedMessages AS (
            SELECT
                message_id,
                file_unique_id,
                file_size,
                media_type,
                timestamp,
                ROW_NUMBER() OVER (
                    PARTITION BY file_unique_id
                    ORDER BY timestamp ASC
                ) as rn
            FROM messages
            WHERE file_unique_id IS NOT NULL AND file_unique_id != ''
        )
        SELECT
            file_unique_id,
            MAX(file_size) as file_size,
            MIN(media_type) as media_type,
            MAX(CASE WHEN rn = 1 THEN message_id END) as keep_id,
            GROUP_CONCAT(CASE WHEN rn > 1 THEN message_id END) as delete_ids
        FROM RankedMessages
        WHERE rn > 1 OR (
            (SELECT COUNT(*) FROM messages m2 WHERE m2.file_unique_id = RankedMessages.file_unique_id) > 1
            AND rn = 1
        )
        GROUP BY file_unique_id
        HAVING COUNT(*) > 0 AND delete_ids IS NOT NULL
    """)

    duplicates = []
    for row in cursor.fetchall():
        file_unique_id, file_size, media_type, keep_id, delete_ids_str = row
        if delete_ids_str:
            delete_ids = [int(x) for x in delete_ids_str.split(',')]
            duplicates.append((file_size, media_type, keep_id, delete_ids))

    return duplicates


def find_invalid_messages(conn: sqlite3.Connection) -> list:
    """查找所有无效消息（is_valid = 0）"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, timestamp
        FROM messages
        WHERE is_valid = 0
    """)

    return cursor.fetchall()


def get_message_stats(conn: sqlite3.Connection) -> list:
    """获取消息统计"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            media_type,
            COUNT(*) as total,
            SUM(is_valid=0) as invalid_count,
            SUM(is_duplicate=1) as duplicate_count
        FROM messages
        GROUP BY media_type
    """)
    return cursor.fetchall()


def get_existing_files(conn: sqlite3.Connection) -> set:
    """获取所有已存在的文件唯一ID"""
    cursor = conn.cursor()
    cursor.execute("SELECT file_unique_id, message_id FROM messages WHERE is_duplicate = 0")
    seen_files = set()
    for file_unique_id, _ in cursor.fetchall():
        seen_files.add(file_unique_id)
    return seen_files
