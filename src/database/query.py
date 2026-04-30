"""
数据库查询模块

提供共享的数据库查询功能：
1. 高反应消息查询
2. 大文件媒体查询

供 info、forward、clean 等模块使用
"""
import sqlite3
from typing import Any


def find_high_reaction_messages(conn: sqlite3.Connection, min_total: int = 0, limit: int = 10) -> list[tuple[int, int, int, int]]:
    """
    查询高反应消息

    Args:
        conn: 数据库连接
        min_total: 最小反应总数，0表示无限制
        limit: 返回条数上限

    Returns:
        [(message_id, positive, heart, total), ...]
    """
    cursor = conn.cursor()
    if min_total > 0:
        cursor.execute('''
            SELECT message_id, positive, heart, total FROM (
                SELECT
                    message_id,
                    json_extract(reactions, '$.positive') AS positive,
                    json_extract(reactions, '$.heart') AS heart,
                    (json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) AS total
                FROM messages
                WHERE is_valid = 1 AND reactions IS NOT NULL
            ) WHERE total > ?
            ORDER BY total DESC
            LIMIT ?
        ''', (min_total, limit))
    else:
        cursor.execute('''
            SELECT message_id, positive, heart, total FROM (
                SELECT
                    message_id,
                    json_extract(reactions, '$.positive') AS positive,
                    json_extract(reactions, '$.heart') AS heart,
                    (json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) AS total
                FROM messages
                WHERE is_valid = 1 AND reactions IS NOT NULL
            ) WHERE total > 0
            ORDER BY total DESC
            LIMIT ?
        ''', (limit,))
    return cursor.fetchall()


def find_reaction_messages_over_threshold(conn: sqlite3.Connection, threshold: int = 50, limit: int | None = None) -> list[tuple[int, int, int, int]]:
    """
    查询超过阈值的高反应消息

    Args:
        conn: 数据库连接
        threshold: 反应总数阈值，默认50
        limit: 返回条数上限，None表示无限制

    Returns:
        [(message_id, positive, heart, total), ...]
    """
    cursor = conn.cursor()
    if limit:
        cursor.execute('''
            SELECT message_id, positive, heart, total FROM (
                SELECT
                    message_id,
                    json_extract(reactions, '$.positive') AS positive,
                    json_extract(reactions, '$.heart') AS heart,
                    (json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) AS total
                FROM messages
                WHERE is_valid = 1 AND reactions IS NOT NULL
            ) WHERE total > ?
            ORDER BY total DESC
            LIMIT ?
        ''', (threshold, limit))
    else:
        cursor.execute('''
            SELECT message_id, positive, heart, total FROM (
                SELECT
                    message_id,
                    json_extract(reactions, '$.positive') AS positive,
                    json_extract(reactions, '$.heart') AS heart,
                    (json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) AS total
                FROM messages
                WHERE is_valid = 1 AND reactions IS NOT NULL
            ) WHERE total > ?
            ORDER BY total DESC
        ''', (threshold,))
    return cursor.fetchall()


def find_large_media(conn: sqlite3.Connection, min_size: int = 1048576, max_size: int = 1073741824) -> list[tuple[int, int, str]]:
    """
    查询指定大小范围外的媒体消息

    Args:
        conn: 数据库连接
        min_size: 最小文件大小(字节)，默认1MB
        max_size: 最大文件大小(字节)，默认1GB

    Returns:
        [(message_id, file_size, media_type), ...]
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_id, file_size, media_type
        FROM messages
        WHERE (file_size < ? OR file_size > ?)
        AND media_type IN ('video', 'document')
        ORDER BY file_size DESC
    """, (min_size, max_size))
    return cursor.fetchall()


def get_forward_sources(conn: sqlite3.Connection, limit: int = 10) -> list[tuple[int, int]]:
    """
    查询转发来源统计

    Args:
        conn: 数据库连接
        limit: 返回条数上限

    Returns:
        [(source_id, count), ...]
    """
    cursor = conn.cursor()
    cursor.execute('''
        SELECT source_id, COUNT(*) as count
        FROM messages
        WHERE is_valid = 1 AND source_id IS NOT NULL
        GROUP BY source_id
        ORDER BY count DESC
        LIMIT ?
    ''', (limit,))
    return cursor.fetchall()
