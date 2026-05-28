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
    main_channel_id: int | None = None,
) -> list[dict[str, Any]]:
    """查找要转发的消息（高反应 + 高浏览量 TOP，与 info 保持一致）

    使用 query.find_top_messages 统一查询逻辑：
    1. 高反应消息 TOP（reactions > 0）
    2. 高浏览量消息 TOP（views > 8x avg）
    3. 合并去重，reaction 优先

    Args:
        conn: 数据库连接
        channel_id: 频道ID（source_id 过滤条件，递归层为来源频道）
        reaction_limit: 高反应消息数量限制
        views_limit: 高浏览量消息数量限制
        filter_by_source: 是否按 source_id 过滤（第1层为False，递归层为True）
        main_channel_id: 主频道ID（递归层搜索用，第1层为None）

    Returns:
        消息列表，每条消息包含 message_id, total, views, source_id, media_type, msg_type
    """
    # 第1层（filter_by_source=False）：搜索在 channel_id 中的消息
    # 递归层（filter_by_source=True）：搜索在 main_channel_id 中的消息，source_id=channel_id
    #    - channel_id 是来源频道（消息从这个频道转发到 main_channel）
    #    - main_channel_id 是主频道（最初要转发的频道，消息最终在这里）
    if filter_by_source and main_channel_id is not None:
        # 递归层：在主频道中查找来自 channel_id 的消息
        search_channel_id = main_channel_id
        source_id_for_query = channel_id
    else:
        # 第1层：在 channel_id 中查找消息（无 source 过滤）
        search_channel_id = channel_id
        source_id_for_query = None

    return find_top_messages(
        conn,
        reaction_limit=reaction_limit,
        views_limit=views_limit,
        source_id=source_id_for_query,
        channel_id=search_channel_id,
    )


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


def sync_channel_for_forward(channel_id: int, joined_channels: set[int] | None = None) -> bool:
    """为转发同步频道数据（使用主数据库）

    Args:
        channel_id: 频道ID
        joined_channels: 用于收集自动加入的频道ID集合

    Returns:
        True if sync succeeded, False otherwise
    """
    from modules.sync import sync_channel
    return sync_channel(channel_id=str(channel_id), joined_channels=joined_channels)


def forward_with_recursion(
    source_channels: list[int],
    target_channel: int,
    current_depth: int = 1,
    max_depth: int | None = None,  # None=不递归，0=禁用，>0=递归层数
    processed_channels: set[int] | None = None,
    main_channel_id: int | None = None,  # 主频道ID（递归时传递）
    check_exists: bool = False,
    force: bool = False,
    reaction_limit: int = 10,
    views_limit: int = 50,
    max_source_channels: int = 10,
    joined_channels: set[int] | None = None,
) -> tuple[int, int, int]:
    """递归转发高反应消息

    语义约定：
    - max_depth=None → 不递归（仅处理当前频道）
    - max_depth=0 → 禁用递归（明确禁用）
    - max_depth>0 → 递归 N 层

    Args:
        source_channels: 当前层级的源频道列表
        target_channel: 目标频道ID
        current_depth: 当前深度
        max_depth: 递归深度
            - None: 不递归（仅处理当前频道）
            - 0: 禁用递归
            - >0: 递归层数
        processed_channels: 已处理的频道集合
        main_channel_id: 主频道ID（第1层时自动设置为source_channels[0]）
        check_exists: 是否检查消息是否存在
        force: 是否强制转发（忽略限制）
        reaction_limit: 高反应消息数量限制（必须从config解析后传入）
        views_limit: 高浏览量消息数量限制（必须从config解析后传入）
        max_source_channels: 每层递归最多处理的来源频道数（必须从config传入）
        joined_channels: 用于收集自动加入的频道ID集合

    Returns:
        (total_forwarded, total_skipped, total_failed)
    """
    if processed_channels is None:
        processed_channels = set()
    if joined_channels is None:
        joined_channels = set()
    # 第1层时，设置主频道ID
    if current_depth == 1 and main_channel_id is None and source_channels:
        main_channel_id = source_channels[0]

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

        # 检查频道 ID 有效性（force 模式跳过）
        print(f"[FORWARD] 深度 {current_depth}: 检查频道 {channel_id}...")
        if not force:
            with forward_core.get_client("tg-mgr") as client:
                is_valid, reason = is_channel_id_valid(client, channel_id)
                if not is_valid:
                    print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} {reason}，跳过")
                    continue
                if not is_channel_forwarding_allowed(client, channel_id):
                    print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 禁止转发，跳过")
                    continue

        # 同步频道数据（传入joined_channels以追踪自动加入的频道）
        print(f"[FORWARD] 深度 {current_depth}: 同步频道 {channel_id}...")
        try:
            sync_ok = sync_channel_for_forward(channel_id, joined_channels=joined_channels)
            if not sync_ok:
                print(f"[FORWARD] 深度 {current_depth}: 同步频道 {channel_id} 失败（无法加入）")
                continue
        except Exception as e:
            print(f"[FORWARD] 深度 {current_depth}: 同步频道 {channel_id} 失败: {e}")
            continue

        # 使用主数据库查询（与 info 保持一致）
        with get_db() as conn:
            # 查找要转发的消息
            # 第1层（filter_by_source=False）：在 channel_id 中查找消息，无 source 过滤
            # 递归层（filter_by_source=True）：在 channel_id（当前深度发现的来源频道）中查找消息，不按 source 过滤
            #    - channel_id 是当前深度的频道
            #    - main_channel_id 是第1层的频道（用于记录）
            #    注意：递归层应该搜索当前 channel_id，而非 main_channel_id
            filter_by_source = current_depth > 1
            if filter_by_source and main_channel_id is not None:
                # 递归层：在 channel_id（当前深度来源频道）中查找消息，不按 source 过滤
                messages = find_top_messages(
                    conn,
                    reaction_limit=reaction_limit,
                    views_limit=views_limit,
                    source_id=None,
                    channel_id=channel_id,
                )
            else:
                # 第1层：在 channel_id 中查找消息（无 source 过滤）
                messages = find_top_messages(
                    conn,
                    reaction_limit=reaction_limit,
                    views_limit=views_limit,
                    source_id=None,
                    channel_id=channel_id,
                )
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
                # 在当前 channel_id 的消息中查找来源频道，形成来源链
                if max_depth is not None and current_depth < max_depth:
                    # 在 channel_id（当前深度处理的频道）的消息中查找来源频道
                    next_channels = find_forward_sources_by_channel(conn, channel_id, limit=max_source_channels)
                    if next_channels:
                        source_channel_ids = [ch[0] for ch in next_channels]
                        print(f"[FORWARD] 深度 {current_depth}: 发现来源频道 {len(source_channel_ids)} 个")
                        nf, ns, nfa = forward_with_recursion(
                            source_channels=source_channel_ids,
                            target_channel=target_channel,
                            current_depth=current_depth + 1,
                            max_depth=max_depth,
                            processed_channels=processed_channels,
                            main_channel_id=main_channel_id,
                            check_exists=check_exists,
                            force=force,
                            reaction_limit=reaction_limit,
                            views_limit=views_limit,
                            max_source_channels=max_source_channels,
                            joined_channels=joined_channels,
                        )
                        total_forwarded += nf
                        total_skipped += ns
                        total_failed += nfa
                    else:
                        print(f"[FORWARD] 深度 {current_depth}: 未找到来源频道，停止递归")

        processed_channels.add(channel_id)

    return total_forwarded, total_skipped, total_failed


def is_channel_id_valid(client, channel_id: int) -> tuple[bool, str]:
    """检查频道 ID 是否有效（可访问）

    Returns:
        (is_valid, reason): is_valid=True 表示可访问，False 表示无效，reason 为原因
    """
    # 频道 ID 格式：超级频道为 -(100... + numeric_id)，如 -1001411818513
    # 普通用户/群组 ID 是小负数（如 -28019），不是有效频道格式，跳过
    if channel_id > -10000000000:
        return False, "非频道格式ID"

    from pyrogram import errors
    try:
        client.get_chat(channel_id)
        return True, ""
    except errors.ChannelInvalid:
        return False, "频道 ID 无效"
    except errors.ChannelPrivate:
        return False, "频道是私密的"
    except errors.ChatForbidden:
        return False, "频道已被封禁"
    except errors.BadRequest as e:
        if "Invalid" in str(e) or "not found" in str(e).lower():
            return False, "频道不存在"
        raise
    except Exception:
        return False, "未知错误"


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