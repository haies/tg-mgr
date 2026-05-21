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
        views,
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
        """
        SELECT
            message_id,
            json_extract(reactions, '$.positive') AS positive,
            json_extract(reactions, '$.heart') AS heart,
            COALESCE(json_extract(reactions, '$.total'), json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) AS total
        FROM messages
        WHERE is_valid = 1 AND reactions IS NOT NULL
          AND COALESCE(json_extract(reactions, '$.total'), json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) > ?
        ORDER BY total DESC
        LIMIT ?
        """,
        (threshold, limit),
    )
    return cursor.fetchall()


def find_reaction_messages_over_threshold(
    conn: sqlite3.Connection, threshold: int = 50, limit: int | None = None, source_id: int | None = None
) -> list[tuple[int, int, int, int, int | None, int | None]]:
    """
    查询超过阈值的高反应消息

    使用 WHERE total > threshold 确保只返回有实际反应的消息。

    Args:
        conn: 数据库连接
        threshold: 反应总数阈值，默认50。0表示 total > 0
        limit: 返回条数上限，None表示无限制
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, positive, heart, total, source_id, views), ...]
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
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
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
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
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
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
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
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
                WHERE total > ?
                ORDER BY total DESC
                """,
                (effective_threshold,),
            )
    return cursor.fetchall()


def find_reaction_messages_above_multiplier(
    conn: sqlite3.Connection,
    multiplier: float = 5,
    limit: int = 200,
    source_id: int | None = None,
) -> list[tuple[int, int, int, int, int | None, int | None]]:
    """
    查询超过频道内平均反应数 * 倍数的高反应消息

    规则：
    - 只计算 reactions > 0 的消息的平均反应数
    - threshold = avg_total * multiplier
    - 如果 avg = 0，回退到 total > 0

    Args:
        conn: 数据库连接
        multiplier: 倍数（默认5，即 > 5 * avg）
        limit: 返回条数上限
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, positive, heart, total, source_id, views), ...]
    """
    cursor = conn.cursor()

    # 构建 AVG 子查询（直接查询 messages 表，避免 CTE 依赖）
    # 只计算 reactions > 0 的消息的平均反应数
    if source_id is not None:
        avg_subquery = """
            SELECT AVG(COALESCE(json_extract(reactions, '$.total'),
                             json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')))
            FROM messages
            WHERE is_valid = 1 AND reactions IS NOT NULL
              AND COALESCE(json_extract(reactions, '$.total'),
                           json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) > 0
              AND source_id = ?
        """
        cursor.execute(avg_subquery, (source_id,))
    else:
        avg_subquery = """
            SELECT AVG(COALESCE(json_extract(reactions, '$.total'),
                             json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')))
            FROM messages
            WHERE is_valid = 1 AND reactions IS NOT NULL
              AND COALESCE(json_extract(reactions, '$.total'),
                           json_extract(reactions, '$.positive') + json_extract(reactions, '$.heart')) > 0
        """
        cursor.execute(avg_subquery)

    row = cursor.fetchone()
    avg_total = row[0] if row and row[0] else 0

    # 计算阈值：如果 avg = 0，回退到 threshold = 0（即 total > 0）
    threshold = avg_total * multiplier if avg_total > 0 else 0

    # 查询超过阈值的消息
    if source_id is not None:
        if limit is not None:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
                WHERE total > ? AND source_id = ?
                ORDER BY total DESC
                LIMIT ?
                """,
                (threshold, source_id, limit),
            )
        else:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
                WHERE total > ? AND source_id = ?
                ORDER BY total DESC
                """,
                (threshold, source_id),
            )
    else:
        if limit is not None:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
                WHERE total > ?
                ORDER BY total DESC
                LIMIT ?
                """,
                (threshold, limit),
            )
        else:
            cursor.execute(
                f"""
                {_REACTION_CTE}
                SELECT message_id, positive, heart, total, source_id, views FROM reaction_messages
                WHERE total > ?
                ORDER BY total DESC
                """,
                (threshold,),
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
        WHERE is_valid = 1 AND views >= ?
        ORDER BY views DESC
        LIMIT ?
    """,
        (min_views, limit),
    )
    return cursor.fetchall()


def find_messages_by_views_multiplier(
    conn: sqlite3.Connection,
    multiplier: float = 5,
    limit: int = 100,
    source_id: int | None = None,
) -> list[tuple[int, int, int, int]]:
    """
    查询超过频道内平均浏览量 * 倍数的高浏览量消息

    规则：
    - threshold = avg_views * multiplier
    - 如果 avg = 0，回退到 views > 0

    Args:
        conn: 数据库连接
        multiplier: 倍数（默认5，即 > 5 * avg）
        limit: 返回条数上限
        source_id: 可选，按来源频道ID过滤

    Returns:
        [(message_id, views, source_id, media_type), ...]
    """
    cursor = conn.cursor()

    # 计算频道内平均浏览量（仅 views > 0 的消息）
    if source_id is not None:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_valid = 1 AND views > 0 AND source_id = ?", (source_id,))
    else:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_valid = 1 AND views > 0")

    row = cursor.fetchone()
    avg_views = row[0] if row and row[0] else 0

    # 计算阈值：如果 avg = 0，回退到 views > 0
    threshold = avg_views * multiplier if avg_views > 0 else 0

    # 筛选 views > threshold 的消息
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
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_valid = 1 AND views > 0 AND source_id = ?", (source_id,))
    else:
        cursor.execute("SELECT AVG(views) FROM messages WHERE is_valid = 1 AND views > 0")
    row = cursor.fetchone()
    avg_views = row[0] if row and row[0] else 0

    if avg_views <= 0:
        # 没有足够的浏览量数据，回退到取最高浏览量消息（views > 1）
        if source_id is not None:
            cursor.execute(
                """
                SELECT message_id, views, source_id, media_type
                FROM messages
                WHERE is_valid = 1 AND views > 1 AND source_id = ?
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
                WHERE is_valid = 1 AND views > 1
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
            WHERE is_valid = 1 AND views > 1 AND source_id = ?
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
            WHERE is_valid = 1 AND views > 1
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
    reaction_threshold_multiplier: float = 5,
) -> list[dict[str, Any]]:
    """
    统一的"高反应消息查询"逻辑（info.py 和 forward.py 共用）

    规则：
    - 计算频道内平均反应数（reactions > 0 的消息）
    - threshold = avg_total * multiplier
    - 如果 avg = 0，回退到 total > 0
    - 返回最多 reaction_limit 条

    Args:
        conn: 数据库连接
        reaction_limit: 高反应消息数量限制
        source_id: 可选，按来源频道ID过滤（用于 forward 模块指定源频道）
        reaction_threshold_multiplier: 倍数（默认5，即 > 5 * avg）

    Returns:
        消息列表，每条消息包含 message_id, positive, heart, total, views, source_id, media_type
    """
    from utils.media import row_to_reaction_dict

    # 使用 multiplier 逻辑查询高反应消息
    results = find_reaction_messages_above_multiplier(
        conn,
        multiplier=reaction_threshold_multiplier,
        limit=reaction_limit,
        source_id=source_id,
    )
    return [row_to_reaction_dict(row) for row in results]


def find_top_messages(
    conn: sqlite3.Connection,
    reaction_limit: int = 200,
    views_limit: int = 100,
    source_id: int | None = None,
    reaction_threshold_multiplier: float = 5,
    views_threshold_multiplier: float = 8,
) -> list[dict[str, Any]]:
    """
    统一的高反应+高浏览TOP查询（info.py 和 forward.py 共用）

    逻辑：
    1. 高反应消息 TOP（total > multiplier * avg_total，reactions > 0 参与平均计算）
    2. 高浏览量消息 TOP（views > multiplier * avg_views）
    3. 合并去重，reaction 优先
    4. 补充缺失字段（views, source_id, media_type）
    5. 标记 msg_type（high_reaction / high_views）

    Args:
        conn: 数据库连接
        reaction_limit: 高反应消息数量限制
        views_limit: 高浏览量消息数量限制
        source_id: 可选，按来源频道ID过滤
        reaction_threshold_multiplier: 反应数倍数（默认5）
        views_threshold_multiplier: 浏览量倍数（默认7）

    Returns:
        消息列表，每条消息包含 message_id, positive, heart, total, views, source_id, media_type, msg_type
    """

    # 1. 高反应消息
    reaction_results = find_reaction_messages_for_display(
        conn,
        reaction_limit=reaction_limit,
        source_id=source_id,
        reaction_threshold_multiplier=reaction_threshold_multiplier,
    )
    for msg in reaction_results:
        msg["msg_type"] = "high_reaction"

    # 2. 高浏览量消息（views > multiplier * avg_views）
    view_rows = find_messages_by_views_multiplier(
        conn,
        multiplier=views_threshold_multiplier,
        limit=views_limit,
        source_id=source_id,
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
        channel_id: 频道ID（作为 source_id 过滤）
        limit: 返回条数上限

    Returns:
        [(source_id, count), ...]
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT source_id, COUNT(*) as count
        FROM messages
        WHERE is_valid = 1 AND source_id IS NOT NULL AND source_id = ?
        GROUP BY source_id
        ORDER BY count DESC
        LIMIT ?
        """,
        (channel_id, limit),
    )
    return cursor.fetchall()


def _deduplicate_media_groups(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一媒体组只保留一条（反应最高的）"""
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
            result.append(max(msgs, key=lambda m: m.get("total", 0)))
    return result
