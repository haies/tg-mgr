"""
数据库查询模块

提供共享的数据库查询功能：
1. 高反应消息查询
2. 大文件媒体查询

供 info、forward、clean 等模块使用
"""

import sqlite3
from typing import Any

# 共享的 CTE 查询片段，用于从 reactions JSON 中提取数据
# total 优先从 JSON 读取，兼容旧数据（无 total 字段时回退到 positive + heart）
_REACTION_CTE = """
WITH reaction_messages AS (
    SELECT
        message_id,
        source_id,
        json_extract(reactions, '$.positive') AS positive,
        json_extract(reactions, '$.heart') AS heart,
        COALESCE(json_extract(reactions, '$.total'), json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) AS total
    FROM messages
    WHERE is_valid = 1 AND reactions IS NOT NULL
)
"""


def find_high_reaction_messages(
    conn: sqlite3.Connection, min_total: int = 0, limit: int = 10
) -> list[tuple[int, int, int, int]]:
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
    threshold = min_total if min_total > 0 else 0
    cursor.execute(
        f"""
        {_REACTION_CTE}
        SELECT message_id, positive, heart, total FROM reaction_messages
        WHERE total > ?
        ORDER BY total DESC
        LIMIT ?
        """,
        (threshold, limit),
    )
    return cursor.fetchall()


def find_reaction_messages_over_threshold(
    conn: sqlite3.Connection, threshold: int = 50, limit: int | None = None, source_id: int | None = None
) -> list[tuple[int, int, int, int]]:
    """
    查询超过阈值的高反应消息

    使用 WHERE total > threshold 确保只返回有实际反应的消息。

    Args:
        conn: 数据库连接
        threshold: 反应总数阈值，默认50。0表示 total > 0
        limit: 返回条数上限，None表示无限制
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, positive, heart, total), ...]
    """
    cursor = conn.cursor()

    # threshold=0 返回 total > 0（包含1个reaction的消息）
    effective_threshold = threshold

    if source_id is not None:
        # 使用位置参数避免 SQLite named param 问题
        if limit is not None:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total FROM reaction_messages
                WHERE total > ? AND source_id = ?
                ORDER BY total DESC
                LIMIT ?
                """,
                (effective_threshold, source_id, limit),
            )
        else:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total FROM reaction_messages
                WHERE total > ? AND source_id = ?
                ORDER BY total DESC
                """,
                (effective_threshold, source_id),
            )
    else:
        if limit is not None:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total FROM reaction_messages
                WHERE total > ?
                ORDER BY total DESC
                LIMIT ?
                """,
                (effective_threshold, limit),
            )
        else:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total FROM reaction_messages
                WHERE total > ?
                ORDER BY total DESC
                """,
                (effective_threshold,),
            )
    return cursor.fetchall()


def find_large_media(
    conn: sqlite3.Connection, min_size: int = 1048576, max_size: int = 1073741824
) -> list[tuple[int, int, str]]:
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
    cursor.execute(
        """
        SELECT message_id, file_size, media_type
        FROM messages
        WHERE (file_size < ? OR file_size > ?)
        AND media_type IN ('video', 'document')
        ORDER BY file_size DESC
    """,
        (min_size, max_size),
    )
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
    cursor.execute(
        """
        SELECT source_id, COUNT(*) as count
        FROM messages
        WHERE is_valid = 1 AND source_id IS NOT NULL
        GROUP BY source_id
        ORDER BY count DESC
        LIMIT ?
    """,
        (limit,),
    )
    return cursor.fetchall()


def find_messages_by_views(conn: sqlite3.Connection, min_views: int = 1, limit: int = 10) -> list[tuple[int, int, int, int]]:
    """
    按浏览量查找消息

    Args:
        conn: 数据库连接
        min_views: 最小浏览量
        limit: 返回条数上限

    Returns:
        [(message_id, views, source_id, media_type), ...]
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT message_id, views, source_id, media_type
        FROM messages
        WHERE is_valid = 1 AND views >= ?
        ORDER BY views DESC
        LIMIT ?
    """,
        (min_views, limit),
    )
    return cursor.fetchall()


def find_messages_by_views_top(conn: sqlite3.Connection, limit: int = 10, source_id: int | None = None) -> list[tuple[int, int, int, int]]:
    """
    按浏览量TOP查询（显示高于平均2倍的消息，数量不足时补充普通高浏览量消息）

    Args:
        conn: 数据库连接
        limit: 返回条数上限
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, views, source_id, media_type), ...]
    """
    cursor = conn.cursor()

    # 计算平均浏览量（仅 views > 0 的消息）
    if source_id is not None:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_valid = 1 AND views > 0 AND source_id = ?", (source_id,))
    else:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_valid = 1 AND views > 0")
    row = cursor.fetchone()
    avg_views = row[0] if row and row[0] else 0

    if avg_views <= 0:
        # 没有足够的浏览量数据，回退到取最高浏览量消息
        if source_id is not None:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_valid = 1 AND views > 0 AND source_id = ?
                ORDER BY views DESC
                LIMIT ?
                """,
                (source_id, limit),
            )
        else:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_valid = 1 AND views > 0
                ORDER BY views DESC
                LIMIT ?
                """,
                (limit,),
            )
        return cursor.fetchall()

    threshold = avg_views * 8

    # 先筛选 views > 2 * avg_views 的消息
    if source_id is not None:
        cursor.execute(
            """
            SELECT message_id, views, source_id, media_type
            FROM messages
            WHERE is_valid = 1 AND views > ? AND source_id = ?
            ORDER BY views DESC
            """,
            (threshold, source_id),
        )
    else:
        cursor.execute(
            """
            SELECT message_id, views, source_id, media_type
            FROM messages
            WHERE is_valid = 1 AND views > ?
            ORDER BY views DESC
            """,
            (threshold,),
        )
    high_views_results = list(cursor.fetchall())

    # 如果数量不足 limit，补充其他消息（views > 0 但 <= 2*avg）
    if len(high_views_results) < limit:
        if source_id is not None:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_valid = 1 AND views > 0 AND views <= ? AND source_id = ?
                ORDER BY views DESC
                LIMIT ?
                """,
                (threshold, source_id, limit - len(high_views_results)),
            )
        else:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_valid = 1 AND views > 0 AND views <= ?
                ORDER BY views DESC
                LIMIT ?
                """,
                (threshold, limit - len(high_views_results)),
            )
        high_views_results.extend(cursor.fetchall())

    return high_views_results


def find_reaction_messages_for_display(
    conn: sqlite3.Connection,
    reaction_limit: int = 10,
    source_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    统一的"高反应消息查询"逻辑（info.py 和 forward.py 共用）

    规则：
    - 优先：反应 > 50 的消息，如果数量 > reaction_limit，全部返回
    - 否则：获取反应 > 0 的消息（threshold=0），按 reaction_limit 数量返回
    - Fallback：浏览量 TOP 消息（当无反应消息时）

    Args:
        conn: 数据库连接
        reaction_limit: 高反应消息数量限制
        source_id: 可选，按来源频道ID过滤（用于 forward 模块指定源频道）

    Returns:
        消息列表，每条消息包含 message_id, positive, heart, total, views, source_id, media_type
    """
    from utils.media import row_to_reaction_dict

    # source_id 过滤条件
    source_filter = f"AND source_id = {source_id}" if source_id else ""

    # 获取高反应消息
    over50_results = find_reaction_messages_over_threshold(conn, threshold=50, limit=50, source_id=source_id)
    over50_count = len(over50_results)

    if over50_count > reaction_limit:
        # 大于50的数量已超过限制，全部输出
        return [row_to_reaction_dict(row) for row in over50_results]

    # 否则使用 threshold=0 获取所有有反应的消息，取配置的限制数量
    all_results = find_reaction_messages_over_threshold(conn, threshold=0, limit=reaction_limit, source_id=source_id)
    if all_results:
        return [row_to_reaction_dict(row) for row in all_results]

    # Fallback：浏览量 TOP 消息
    view_results = find_messages_by_views_top(conn, limit=reaction_limit, source_id=source_id)
    return [
        {
            "message_id": row[0],
            "views": row[1],
            "source_id": row[2],
            "media_type": row[3],
        }
        for row in view_results
    ]
