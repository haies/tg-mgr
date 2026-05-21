"""转发模块预览与确认逻辑"""
import sqlite3
from typing import Any


def _build_stats_str(total: int, views: int, is_media_group: bool = False) -> str:
    """构建统计信息字符串

    Args:
        total: 反应总数
        views: 浏览量
        is_media_group: 是否为媒体组

    Returns:
        格式如 "(反应:5, 浏览:1234，媒体组)" 或 "(浏览:1234，媒体组)"
    """
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


def summarize_messages_for_forward(
    conn: sqlite3.Connection,
    messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """统计待转发消息的累计大小和条数，同时填充每条消息的 file_size

    Returns:
        {
            "total_count": int,
            "media_count": int,
            "total_size_mb": float,
            "file_sizes": dict[int, int],  # message_id -> file_size
        }
    """
    if not messages:
        return {"total_count": 0, "media_count": 0, "total_size_mb": 0.0, "file_sizes": {}}

    msg_ids = [m["message_id"] for m in messages]

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(msg_ids))
    cursor.execute(
        f"""
        SELECT
            message_id,
            COALESCE(file_size, 0) as file_size
        FROM messages
        WHERE message_id IN ({placeholders})
        """,
        msg_ids,
    )
    file_sizes = {row[0]: row[1] for row in cursor.fetchall()}

    # 填充每条消息的 file_size
    for msg in messages:
        msg["file_size"] = file_sizes.get(msg["message_id"], 0)

    total_count = len(msg_ids)
    media_count = sum(1 for fs in file_sizes.values() if fs > 0)
    total_size_bytes = sum(file_sizes.values())

    return {
        "total_count": total_count,
        "media_count": media_count,
        "total_size_mb": total_size_bytes / 1024 / 1024,
        "file_sizes": file_sizes,
    }


def confirm_forward(
    messages: list[dict[str, Any]],
    summary: dict[str, Any]
) -> bool:
    """显示统计摘要并询问确认

    Args:
        messages: 消息列表
        summary: 统计结果 from summarize_messages_for_forward

    Returns:
        True if user confirms with 'y', False otherwise
    """
    if not messages:
        return False

    total_count = summary.get("total_count", 0)
    media_count = summary.get("media_count", 0)
    total_size_mb = summary.get("total_size_mb", 0.0)

    # 大小分级提示
    if total_size_mb < 10:
        size_level = "小（<10MB）"
    elif total_size_mb < 100:
        size_level = "中等（10MB-100MB）"
    elif total_size_mb < 500:
        size_level = "较大（100MB-500MB）"
    else:
        size_level = "大（>500MB）"

    print("[FORWARD] 待转发消息统计：")
    print(f"  - 消息条数：{total_count} 条")
    print(f"  - 有媒体：{media_count} 条")
    print(f"  - 媒体累计大小：{total_size_mb:.1f} MB")
    print(f"  - 预估大小级别：{size_level}")

    # 显示消息列表预览
    print(f"\n[FORWARD] 待转发消息列表（共 {len(messages)} 条）：")
    for i, msg in enumerate(messages[:20]):  # 限制显示20条
        source_id = msg.get('source_id') or 0
        link = f"https://t.me/c/{abs(source_id)}/{msg['message_id']}"
        total = msg.get("total", 0)
        views = msg.get("views", 0)
        file_size = msg.get("file_size", 0)
        size_mb = file_size / 1024 / 1024 if file_size else 0
        is_media_group = msg.get("is_media_group", False)
        msg_type = msg.get("msg_type", "")

        group_suffix = " (媒体组)" if is_media_group else ""
        stats = _build_stats_str(total, views)
        size_str = f"{size_mb:.1f}MB" if size_mb > 0 else "无媒体"
        type_suffix = " [高反应]" if msg_type == "high_reaction" else " [高浏览量]" if msg_type == "high_views" else ""
        print(f"  {i+1}. {link} | {size_str}{group_suffix}{stats}{type_suffix}")

    if len(messages) > 20:
        print(f"  ... 还有 {len(messages) - 20} 条消息")

    print()

    try:
        response = input(f"媒体累计大小 {total_size_mb:.1f}MB，是否继续转发？[y/N] ").strip().lower()
        return response == "y"
    except (EOFError, KeyboardInterrupt):
        print("\n[FORWARD] 已取消")
        return False