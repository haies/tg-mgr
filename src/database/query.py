"""
数据库查询模块

提供共享的数据库查询功能：
1. 高反应消息查询
2. 大文件媒体查询

供 info、forward、clean 等模块使用
"""

import sqlite3
from typing import Any

# 共享的 CTE 查询片段，用于从 reactions 整数列中提取数据
_REACTION_CTE = """
WITH reaction_messages AS (
    SELECT
        message_id,
        source_id,
        channel_id,
        views,
        media_group_id,
        reactions AS total
    FROM messages
    WHERE is_invalid = 0 AND reactions > 0
)
"""


def find_high_reaction_messages(
    conn: sqlite3.Connection, min_total: int = 0, limit: int = 10
) -> list[tuple[int, int]]:
    """
    查询高反应消息

    Args:
        conn: 数据库连接
        min_total: 最小反应总数，0表示无限制
        limit: 返回条数上限

    Returns:
        [(message_id, total), ...]
    """
    cursor = conn.cursor()
    threshold = min_total if min_total > 0 else 0
    cursor.execute(
        """
        SELECT
            message_id,
            reactions AS total
        FROM messages
        WHERE is_invalid = 0 AND reactions > ?
        ORDER BY total DESC
        LIMIT ?
        """,
        (threshold, limit),
    )
    return cursor.fetchall()


def find_reaction_messages_over_threshold(
    conn: sqlite3.Connection, threshold: int = 50, limit: int | None = None, source_id: int | None = None
) -> list[tuple[int, int, int | None, int | None]]:
    """
    查询超过阈值的高反应消息

    使用 WHERE total > threshold 确保只返回有实际反应的消息。

    Args:
        conn: 数据库连接
        threshold: 反应总数阈值，默认50。0表示 total > 0
        limit: 返回条数上限，None表示无限制
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, total, source_id, views), ...]
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
                SELECT message_id, total, source_id, views FROM reaction_messages
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
                SELECT message_id, total, source_id, views FROM reaction_messages
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
                SELECT message_id, total, source_id, views FROM reaction_messages
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
                SELECT message_id, total, source_id, views FROM reaction_messages
                WHERE total > ?
                ORDER BY total DESC
                """,
                (effective_threshold,),
            )
    return cursor.fetchall()


def find_reaction_messages_above_multiplier(
    conn: sqlite3.Connection,
    limit: int = 200,
    source_id: int | None = None,
    channel_id: int | None = None,
) -> list[tuple]:
    """
    查询高反应消息

    规则：
    - 只计算 reactions >= 1 的消息的平均反应数
    - threshold = 0.8 * max + 0.2 * avg
    - 如果 avg = 0，回退到 total > 0

    Args:
        conn: 数据库连接
        limit: 返回条数上限
        source_id: 可选，按来源频道ID过滤（转发来源）
        channel_id: 可选，按消息所在频道ID过滤（同步目标）

    Returns:
        [(message_id, total, source_id, views, media_group_id), ...]
    """
    cursor = conn.cursor()

    # 构建 AVG+MAX 查询（只计算 reactions >= 1 的消息）
    stats_parts = ["SELECT MAX(reactions), AVG(reactions) FROM messages WHERE is_invalid = 0 AND reactions >= 1"]
    stats_params: list = []
    if channel_id is not None:
        stats_parts.append("AND channel_id = ?")
        stats_params.append(channel_id)
    if source_id is not None:
        stats_parts.append("AND source_id = ?")
        stats_params.append(source_id)
    cursor.execute(" ".join(stats_parts), stats_params)

    row = cursor.fetchone()
    max_reactions = row[0] if row and row[0] else 0
    avg_reactions = row[1] if row and row[1] else 0

    # 新公式：threshold = 0.8 * max + 0.2 * avg
    # 如果 max = 0，回退到 threshold = 0（即 total > 0）
    # 高反应条件：total > threshold 或 total > 50
    threshold = 0.8 * max_reactions + 0.2 * avg_reactions if max_reactions > 0 else 0

    # 构建 SELECT 查询（包含 media_group_id 用于媒体组去重）
    select_parts = [f"{_REACTION_CTE} SELECT message_id, total, source_id, views, media_group_id FROM reaction_messages WHERE (total > ? OR total > 50)"]
    select_params: list = [threshold]
    if channel_id is not None:
        select_parts.append("AND channel_id = ?")
        select_params.append(channel_id)
    if source_id is not None:
        select_parts.append("AND source_id = ?")
        select_params.append(source_id)
    select_parts.append("ORDER BY total DESC")
    if limit is not None:
        select_parts.append("LIMIT ?")
        select_params.append(limit)
    cursor.execute(" ".join(select_parts), select_params)

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
        WHERE is_invalid = 0 AND source_id IS NOT NULL
        GROUP BY source_id
        ORDER BY count DESC
        LIMIT ?
    """,
        (limit,),
    )
    return cursor.fetchall()


def find_messages_by_views(
    conn: sqlite3.Connection, min_views: int = 1, limit: int = 10
) -> list[tuple[int, int, int, int]]:
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
        WHERE is_invalid = 0 AND views >= ?
        ORDER BY views DESC
        LIMIT ?
    """,
        (min_views, limit),
    )
    return cursor.fetchall()


def find_messages_by_views_multiplier(
    conn: sqlite3.Connection,
    limit: int = 100,
    source_id: int | None = None,
    channel_id: int | None = None,
) -> list[tuple[int, int, int, int]]:
    """
    查询高浏览量消息

    规则：
    - 只计算 views > 0 的消息的平均浏览量
    - threshold = 0.8 * max + 0.2 * avg
    - 如果 max = 0，回退到 views > 0

    Args:
        conn: 数据库连接
        limit: 返回条数上限
        source_id: 可选，按来源频道ID过滤（转发来源）
        channel_id: 可选，按消息所在频道ID过滤（同步目标）

    Returns:
        [(message_id, views, source_id, media_type), ...]
    """
    cursor = conn.cursor()

    # 构建 MAX+AVG 查询（只计算 views > 0 的消息）
    stats_parts = ["SELECT MAX(views), AVG(views) FROM messages WHERE is_invalid = 0 AND views > 0"]
    stats_params: list = []
    if channel_id is not None:
        stats_parts.append("AND channel_id = ?")
        stats_params.append(channel_id)
    if source_id is not None:
        stats_parts.append("AND source_id = ?")
        stats_params.append(source_id)
    cursor.execute(" ".join(stats_parts), stats_params)

    row = cursor.fetchone()
    max_views = row[0] if row and row[0] else 0
    avg_views = row[1] if row and row[1] else 0

    # 新公式：threshold = 0.8 * max + 0.2 * avg
    # 如果 max = 0，回退到 threshold = 0（即 views > 0）
    # 高浏览条件：views > threshold 或 views > 8 * avg_views
    threshold = 0.8 * max_views + 0.2 * avg_views if max_views > 0 else 0
    views_multiplier_threshold = 8 * avg_views if avg_views > 0 else 0

    # 筛选 views > threshold 或 views > 8*avg_views 的消息
    select_query_parts = ["SELECT message_id, views, source_id, media_type FROM messages WHERE is_invalid = 0 AND (views > ? OR views > ?)"]
    select_params: list = [threshold, views_multiplier_threshold]
    if channel_id is not None:
        select_query_parts.append("AND channel_id = ?")
        select_params.append(channel_id)
    if source_id is not None:
        select_query_parts.append("AND source_id = ?")
        select_params.append(source_id)
    select_query_parts.append("ORDER BY views DESC")
    cursor.execute(" ".join(select_query_parts), select_params)

    results = list(cursor.fetchall())

    # 限制返回数量
    return results[:limit]


def find_messages_by_views_top(conn: sqlite3.Connection, limit: int = 50, source_id: int | None = None) -> list[tuple[int, int, int, int]]:
    """
    按浏览量TOP查询（显示高于平均8倍的消息）

    规则：
    - 默认：views > 8 * avg_views，最多返回 limit 条（上限 50）
    - 若结果 < 10 条，回退到浏览量 top 10（views > 1）
    - 浏览量必须 > 1

    Args:
        conn: 数据库连接
        limit: 返回条数上限（默认 50）
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, views, source_id, media_type), ...]
    """
    cursor = conn.cursor()
    actual_limit = min(limit, 50)

    # 计算平均浏览量（仅 views > 0 的消息）
    if source_id is not None:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_invalid = 0 AND views > 0 AND source_id = ?", (source_id,))
    else:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_invalid = 0 AND views > 0")
    row = cursor.fetchone()
    avg_views = row[0] if row and row[0] else 0

    if avg_views <= 0:
        # 没有足够的浏览量数据，回退到取最高浏览量消息（views > 1）
        if source_id is not None:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_invalid = 0 AND views > 1 AND source_id = ?
                ORDER BY views DESC
                LIMIT ?
                """,
                (source_id, actual_limit),
            )
        else:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_invalid = 0 AND views > 1
                ORDER BY views DESC
                LIMIT ?
                """,
                (actual_limit,),
            )
        results = cursor.fetchall()
        if len(results) < 10:
            # 回退到 top 10
            return _top_views_fallback(cursor, source_id, 10)
        return results

    threshold = avg_views * VIEWS_THRESHOLD_MULTIPLIER

    # 筛选 views > 8 * avg_views 的消息
    if source_id is not None:
        cursor.execute(
            """
            SELECT message_id, views, source_id, media_type
            FROM messages
            WHERE is_invalid = 0 AND views > ? AND source_id = ?
            ORDER BY views DESC
            """,
            (threshold, source_id),
        )
    else:
        cursor.execute(
            """
            SELECT message_id, views, source_id, media_type
            FROM messages
            WHERE is_invalid = 0 AND views > ?
            ORDER BY views DESC
            """,
            (threshold,),
        )
    results = list(cursor.fetchall())

    # 如果数量不足 10，回退到 top 10（views > 1）
    if len(results) < 10:
        return _top_views_fallback(cursor, source_id, 10)

    return results[:actual_limit]


def _top_views_fallback(cursor: sqlite3.Cursor, source_id: int | None, limit: int) -> list[tuple[int, int, int, int]]:
    """回退查询：浏览量 top N（views > 1）"""
    if source_id is not None:
        cursor.execute(
            """
            SELECT message_id, views, source_id, media_type
            FROM messages
            WHERE is_invalid = 0 AND views > 1 AND source_id = ?
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
            WHERE is_invalid = 0 AND views > 1
            ORDER BY views DESC
            LIMIT ?
            """,
            (limit,),
        )
    return cursor.fetchall()


# 高浏览量阈值倍数（统一常量，供 find_top_messages 和 find_messages_by_views_top 共用）
VIEWS_THRESHOLD_MULTIPLIER = 8


def find_reaction_messages_for_display(
    conn: sqlite3.Connection,
    reaction_limit: int = 200,
    source_id: int | None = None,
    channel_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    统一的"高反应消息查询"逻辑（info.py 和 forward.py 共用）

    规则：
    - 只计算 reactions >= 1 的消息的平均反应数
    - threshold = 0.8 * max + 0.2 * avg
    - 如果 max = 0，回退到 total > 0
    - 返回最多 reaction_limit 条

    Args:
        conn: 数据库连接
        reaction_limit: 高反应消息数量限制
        source_id: 可选，按来源频道ID过滤（用于 forward 模块指定源频道）
        channel_id: 可选，按消息所在频道ID过滤（同步目标）

    Returns:
        消息列表，每条消息包含 message_id, total, views, source_id, media_group_id
    """
    from utils.media import row_to_reaction_dict

    # 使用新公式查询高反应消息
    results = find_reaction_messages_above_multiplier(
        conn,
        limit=reaction_limit,
        source_id=source_id,
        channel_id=channel_id,
    )
    return [row_to_reaction_dict(row) for row in results]


def find_top_messages(
    conn: sqlite3.Connection,
    reaction_limit: int = 200,
    views_limit: int = 100,
    source_id: int | None = None,
    channel_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    统一的高反应+高浏览TOP查询（info.py 和 forward.py 共用）

    逻辑：
    1. 高反应消息 TOP（threshold = 0.8 * max + 0.2 * avg，reactions >= 1 参与平均计算）
    2. 高浏览量消息 TOP（threshold = 0.8 * max + 0.2 * avg，views > 0 参与平均计算）
    3. 合并去重，reaction 优先
    4. 补充缺失字段（views, source_id, media_type）
    5. 标记 msg_type（high_reaction / high_views）

    Args:
        conn: 数据库连接
        reaction_limit: 高反应消息数量限制
        views_limit: 高浏览量消息数量限制
        source_id: 可选，按消息来源频道ID过滤（转发来源）
        channel_id: 可选，按消息所在频道ID过滤（同步目标）

    Returns:
        消息列表，每条消息包含 message_id, total, views, source_id, media_group_id, msg_type
    """

    # 1. 高反应消息
    reaction_results = find_reaction_messages_for_display(
        conn,
        reaction_limit=reaction_limit,
        source_id=source_id,
        channel_id=channel_id,
    )
    for msg in reaction_results:
        msg["msg_type"] = "high_reaction"

    # 2. 高浏览量消息
    view_rows = find_messages_by_views_multiplier(
        conn,
        limit=views_limit,
        source_id=source_id,
        channel_id=channel_id,
    )
    view_results = [
        {
            "message_id": row[0],
            "views": row[1],
            "source_id": row[2],
            "media_type": row[3],
            "msg_type": "high_views",
        }
        for row in view_rows
    ]

    # 3. 合并去重（按 message_id，reaction 优先）
    seen_ids = set()
    merged = []
    for msg in reaction_results:
        seen_ids.add(msg["message_id"])
        merged.append(msg)
    for msg in view_results:
        if msg["message_id"] not in seen_ids:
            seen_ids.add(msg["message_id"])
            merged.append(msg)

    # 4. 补充 reaction 消息的 views 和 source_id 字段（从 view_results 获取，仅当 views > 0）
    view_map = {row[0]: row[1] for row in view_rows}  # message_id -> views
    source_id_map = {row[0]: row[2] for row in view_rows}  # message_id -> source_id
    for msg in merged:
        if "views" not in msg:
            views = view_map.get(msg["message_id"], 0)
            if views > 0:
                msg["views"] = views
        if "source_id" not in msg:
            msg["source_id"] = source_id_map.get(msg["message_id"]) or source_id

    return _deduplicate_media_groups(merged)


def find_forward_sources_by_channel(
    conn: sqlite3.Connection,
    channel_id: int,
    limit: int = 10
) -> list[tuple[int, int]]:
    """
    查询指定频道的转发来源统计

    Args:
        conn: 数据库连接
        channel_id: 频道ID（作为 channel_id 过滤，表示消息的目标频道）
        limit: 返回条数上限

    Returns:
        [(source_id, count), ...] - 转发来源频道ID及其消息数量
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT source_id, COUNT(*) as count
        FROM messages
        WHERE is_invalid = 0 AND source_id IS NOT NULL
          AND channel_id = ? AND source_id != channel_id
        GROUP BY source_id
        ORDER BY count DESC
        LIMIT ?
        """,
        (channel_id, limit),
    )
    return cursor.fetchall()


def _deduplicate_media_groups(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 media_group_id 分组合并，每组返回整组消息

    媒体组消息展开为所有成员（而非只保留1条），确保转发时保持媒体组完整性。
    非媒体组消息保持原样。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        mg_id = msg.get("media_group_id")
        if mg_id:
            groups.setdefault(mg_id, []).append(msg)
        else:
            groups.setdefault(f"_nomedia_{id(msg)}", []).append(msg)

    result: list[dict[str, Any]] = []
    for mg_id, msgs in groups.items():
        if mg_id.startswith("_nomedia_"):
            result.extend(msgs)
        else:
            # 媒体组：展开所有成员，而非只返回1条，确保整组转发
            result.extend(msgs)

    return result
