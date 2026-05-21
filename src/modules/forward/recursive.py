"""转发模块递归处理逻辑"""
import logging
import sqlite3
from typing import Any

from modules.forward import forward_core
from database import get_db
from database.query import (
    find_forward_sources_by_channel,
    find_top_messages,
)

logger = logging.getLogger(__name__)

# 导入 forward_messages_batch（从 send.py）
from modules.forward.send import forward_messages_batch


def find_messages_to_forward(
    conn: sqlite3.Connection,
    channel_id: int,
    reaction_limit: int = 10,
    views_limit: int = 50,
    filter_by_source: bool = False,
) -> list[dict[str, Any]]:
    """查找要转发的消息（高反应 + 高浏览量 TOP，与 info 保持一致）

    使用 query.find_top_messages 统一查询逻辑：
    1. 高反应消息 TOP（reactions > 0）
    2. 高浏览量消息 TOP（views > 8x avg）
    3. 合并去重，reaction 优先

    Args:
        conn: 数据库连接
        channel_id: 频道ID（仅当 filter_by_source=True 时作为 source_id 过滤条件）
        reaction_limit: 高反应消息数量限制
        views_limit: 高浏览量消息数量限制
        filter_by_source: 是否按 source_id 过滤（第1层为False，递归层为True）

    Returns:
        消息列表，每条消息包含 message_id, positive, heart, total, views, source_id, media_type, msg_type
    """
    # source_id 过滤：仅递归层启用（第1层查所有消息）
    source_id_for_query = channel_id if filter_by_source else None

    return find_top_messages(conn, reaction_limit=reaction_limit, views_limit=views_limit, source_id=source_id_for_query)


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
    """为转发同步频道数据（使用主数据库）"""
    from modules.sync import sync_channel
    sync_channel(channel_id=str(channel_id))


def forward_with_recursion(
    source_channels: list[int],
    target_channel: int,
    current_depth: int = 1,
    max_depth: int | None = None,
    processed_channels: set[int] | None = None,
    check_exists: bool = False,
    force: bool = False,
    reaction_limit: int = 10,
    views_limit: int = 50,
    max_source_channels: int = 10,
) -> tuple[int, int, int]:
    """递归转发高反应消息

    语义约定：
    - max_depth=None → 无限制（递归到所有层级）
    - max_depth=0 → 禁用递归（上层调用时会避免传入0）
    - max_depth=N → 最多递归N层

    Args:
        source_channels: 当前层级的源频道列表
        target_channel: 目标频道ID
        current_depth: 当前深度
        max_depth: 最大深度（None=无限制，0=禁用递归，N=最多N层）
        processed_channels: 已处理的频道集合
        check_exists: 是否检查消息是否存在
        force: 是否强制转发（忽略限制）
        reaction_limit: 高反应消息数量限制（必须从config解析后传入）
        views_limit: 高浏览量消息数量限制（必须从config解析后传入）
        max_source_channels: 每层递归最多处理的来源频道数（必须从config传入）

    Returns:
        (total_forwarded, total_skipped, total_failed)
    """
    if processed_channels is None:
        processed_channels = set()

    # 检查深度限制（max_depth 为 None 表示无限制）
    if max_depth is not None and current_depth > max_depth:
        return 0, 0, 0

    total_forwarded = 0
    total_skipped = 0
    total_failed = 0

    for channel_id in source_channels:
        if channel_id in processed_channels:
            print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 已处理过，跳过")
            continue

        # 检查频道转发权限（force模式跳过）
        print(f"[FORWARD] 深度 {current_depth}: 检查频道 {channel_id}...")
        if not force:
            with forward_core.get_client("tg-mgr") as client:
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

        # 使用主数据库查询（与 info 保持一致）
        with get_db() as conn:
            # 查找要转发的消息（第1层不按source_id过滤，与info一致；递归层按source_id过滤）
            filter_by_source = current_depth > 1
            messages = find_messages_to_forward(conn, channel_id, reaction_limit, views_limit, filter_by_source=filter_by_source)
            # 统计高反应和高浏览量消息数量（通过 msg_type 字段）
            high_reaction_count = sum(1 for m in messages if m.get("msg_type") == "high_reaction")
            high_views_count = sum(1 for m in messages if m.get("msg_type") == "high_views")
            print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 找到 {len(messages)} 条消息 (高反应: {high_reaction_count}, 高浏览: {high_views_count})")

            if messages:
                # 转发到目标
                f, s, fa = forward_messages_batch(channel_id, [target_channel], messages, check_exists, force)
                total_forwarded += f
                total_skipped += s
                total_failed += fa

                # 递归处理下一层（使用 max_source_channels 限制来源频道数）
                if max_depth is not None and current_depth < max_depth:
                    next_channels = find_forward_sources_by_channel(conn, channel_id, limit=max_source_channels)
                    if next_channels:
                        source_channel_ids = [ch[0] for ch in next_channels]
                        print(f"[FORWARD] 深度 {current_depth}: 发现来源频道 {len(source_channel_ids)} 个")
                        nf, ns, nfa = forward_with_recursion(
                            source_channel_ids,
                            target_channel,
                            current_depth + 1,
                            max_depth,
                            processed_channels,
                            check_exists,
                            force,
                            reaction_limit,
                            views_limit,
                            max_source_channels,
                        )
                        total_forwarded += nf
                        total_skipped += ns
                        total_failed += nfa
                    else:
                        print(f"[FORWARD] 深度 {current_depth}: 未找到来源频道，停止递归")

        processed_channels.add(channel_id)

    return total_forwarded, total_skipped, total_failed


def is_channel_forwarding_allowed(client, channel_id: int) -> bool:
    """检查频道是否允许转发"""
    from pyrogram import errors
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