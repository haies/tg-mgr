"""转发模块核心逻辑"""
from modules.forward.cli import parse_source_arg, resolve_username_to_channel_id
from modules.forward import preview
import modules.forward.send as send_module
from modules.forward.send import (
    forward_single_message,
    _get_original_media_group_message,
    _get_media_group_messages,
    _forward_media_group,
    _build_stats_str as send_build_stats_str,
    get_channel_address as _get_channel_address,
)
from modules.forward.force import _force_send_single_message, _force_send_media_group
from modules.forward import recursive
from modules.forward import forward_core

# Use forward_core's config so tests can patch it via forward_core.get_config
DEFAULT_CONFIG = forward_core.DEFAULT_CONFIG


def get_db():
    return forward_core.get_db()


def get_client(session_name="tg-mgr"):
    return forward_core.get_client(session_name)


def get_config():
    return forward_core.get_config()


def sync_channel(channel_id):
    return forward_core.sync_channel(channel_id)


def find_messages_to_forward(conn, channel_id, reaction_limit=10, views_limit=50, filter_by_source=False):
    return recursive.find_messages_to_forward(conn, channel_id, reaction_limit, views_limit, filter_by_source)


def is_channel_forwarding_allowed(client, channel_id):
    return recursive.is_channel_forwarding_allowed(client, channel_id)


def sync_channel_for_forward(channel_id: int) -> None:
    return recursive.sync_channel_for_forward(channel_id)


def forward_messages_batch(source_channel_id, target_channel_ids, messages, check_exists=False, force=False):
    return send_module.forward_messages_batch(source_channel_id, target_channel_ids, messages, check_exists, force)


def forward_with_recursion(source_channels, target_channel, current_depth=1, max_depth=None, processed_channels=None, check_exists=False, force=False, reaction_limit=10, views_limit=50, max_source_channels=10):
    return recursive.forward_with_recursion(source_channels, target_channel, current_depth, max_depth, processed_channels, check_exists, force, reaction_limit, views_limit, max_source_channels)


# Wrap preview functions so patches on preview.* take effect
def summarize_messages_for_forward(conn, messages):
    return preview.summarize_messages_for_forward(conn, messages)


def confirm_forward(messages, summary):
    return preview.confirm_forward(messages, summary)


def _get_reaction_total(message):
    from utils.media import extract_reaction_data
    return extract_reaction_data(message).total


def _build_stats_str(total: int, views: int, is_media_group: bool = False) -> str:
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


def _join_channel(client, channel_id: int) -> bool:
    from pyrogram import errors
    try:
        client.join_chat(channel_id)
        return True
    except Exception:
        pass
    try:
        chat = client.get_chat(channel_id)
        if hasattr(chat, "username") and chat.username:
            client.join_chat(f"https://t.me/{chat.username}")
            return True
    except Exception:
        pass
    return False


def run_forward(args):
    """主导出流程"""
    config = get_config()
    target_channel_id = args.target if args.target else config.get("channel_id")

    if not target_channel_id:
        print("[ERROR] 未指定目标频道，且环境变量 TG_CHANNEL_ID 未配置")
        return

    default_reaction_limit = config.get("reaction_limit") if config.get("reaction_limit") is not None else DEFAULT_CONFIG["reaction_limit"]
    default_views_limit = config.get("views_limit") if config.get("views_limit") is not None else DEFAULT_CONFIG["views_limit"]
    default_max_source_channels = config.get("max_source_channels", default_reaction_limit)

    configured_depth = config.get("recursion_depth") if config.get("recursion_depth") is not None else DEFAULT_CONFIG["recursion_depth"]
    recursion_depth = args.depth if args.depth is not None else configured_depth

    if args.force and args.depth is None and recursion_depth is not None and recursion_depth > 0:
        print("[FORWARD] 注意：-f 默认使用主数据库同步（与 tg info 一致）")
        print("[FORWARD] 如需递归模式，请额外指定 -r 参数")
        recursion_depth = 0

    reaction_limit = args.limit if args.limit is not None else default_reaction_limit
    views_limit = args.views_limit if args.views_limit is not None else default_views_limit

    # 分离链接和频道ID
    channel_ids = []
    link_messages = []

    for source in args.channels:
        parsed = parse_source_arg(source)
        if parsed[0] is None and parsed[2] is None:
            continue
        if parsed[1] is not None:
            link_messages.append(parsed)
        else:
            channel_ids.append(parsed[0])

    # 处理链接参数（直接转发，不递归）
    if link_messages:
        print(f"[FORWARD] 处理 {len(link_messages)} 条直接转发...")
        force = args.force
        with get_client("tg-mgr") as client:
            try:
                client.get_chat(target_channel_id)
            except Exception:
                if _join_channel(client, target_channel_id):
                    print(f"[FORWARD] 已加入目标频道 {target_channel_id}")

            for channel_id, msg_id, username in link_messages:
                if channel_id is None and username:
                    resolved_id = resolve_username_to_channel_id(client, username)
                    if resolved_id is None:
                        print(f"[ERROR] 无法解析用户名 {username}，跳过")
                        continue
                    channel_id = resolved_id

                link = f"{_get_channel_address(channel_id)}/{msg_id}"
                try:
                    original_msg = _get_original_media_group_message(client, channel_id, msg_id)
                    if original_msg and original_msg.media_group_id:
                        media_group_msgs = _get_media_group_messages(client, channel_id, original_msg.media_group_id, msg_id)
                        if media_group_msgs:
                            try:
                                if _forward_media_group(client, channel_id, target_channel_id, media_group_msgs, force=force):
                                    total = _get_reaction_total(original_msg)
                                    views = original_msg.views or 0
                                    stats = _build_stats_str(total, views, is_media_group=True)
                                    print(f"[FORWARD] 直接转发成功: {link}{stats}")
                                else:
                                    print(f"[FORWARD] 直接转发失败(媒体组): {link}")
                            except Exception as e:
                                if "CHAT_FORWARDS_RESTRICTED" in str(e) and force:
                                    if _force_send_media_group(client, target_channel_id, media_group_msgs):
                                        total = _get_reaction_total(original_msg)
                                        views = original_msg.views or 0
                                        stats = _build_stats_str(total, views, is_media_group=True)
                                        print(f"[FORWARD] 强制转发成功: {link}{stats}")
                                    else:
                                        print(f"[FORWARD] 强制转发失败(媒体组): {link}")
                                else:
                                    print(f"[FORWARD] 直接转发失败: {link} - {e}")
                        else:
                            _force_send_single_message(client, target_channel_id, original_msg)
                            total = _get_reaction_total(original_msg)
                            views = original_msg.views or 0
                            stats = _build_stats_str(total, views, is_media_group=True)
                            print(f"[FORWARD] 直接转发成功: {link}{stats}")
                    else:
                        if force:
                            _force_send_single_message(client, target_channel_id, original_msg)
                            total = _get_reaction_total(original_msg)
                            views = original_msg.views or 0
                            stats = _build_stats_str(total, views)
                            print(f"[FORWARD] 强制转发成功: {link}{stats}")
                        else:
                            client.copy_message(
                                chat_id=target_channel_id,
                                from_chat_id=channel_id,
                                message_id=msg_id,
                            )
                            total = _get_reaction_total(original_msg)
                            views = original_msg.views or 0
                            stats = _build_stats_str(total, views)
                            print(f"[FORWARD] 直接转发成功: {link}{stats}")
                except Exception as e:
                    print(f"[FORWARD] 直接转发失败: {link} - {e}")

    # 处理频道参数（支持递归）
    if channel_ids:
        if recursion_depth <= 0:
            print(f"[FORWARD] 处理 {len(channel_ids)} 个频道（无递归）...")
            for channel_id in channel_ids:
                print(f"[FORWARD] ========== 处理频道: {channel_id} ==========")

                with get_client("tg-mgr") as client:
                    if not args.force and not is_channel_forwarding_allowed(client, channel_id):
                        print(f"[FORWARD] 频道 {channel_id} 禁止转发，跳过")
                        continue

                print(f"[FORWARD] 同步频道 {channel_id}...")
                try:
                    sync_channel(channel_id=str(channel_id))
                except Exception as e:
                    print(f"[FORWARD] 同步失败: {e}")
                    continue

                with get_db() as conn:
                    messages = find_messages_to_forward(conn, channel_id, reaction_limit, views_limit, filter_by_source=False)

                    if args.force and messages:
                        summary = summarize_messages_for_forward(conn, messages)
                        if not confirm_forward(messages, summary):
                            print("[FORWARD] 已取消")
                            return

                    if messages:
                        high_reaction_count = sum(1 for m in messages if m.get("msg_type") == "high_reaction")
                        high_views_count = sum(1 for m in messages if m.get("msg_type") == "high_views")
                        print(f"[FORWARD] 频道 {channel_id} 找到 {len(messages)} 条消息 (高反应: {high_reaction_count}, 高浏览: {high_views_count})")
                    else:
                        print(f"[FORWARD] 频道 {channel_id} 找到 0 条消息")
                    if messages:
                        f, s, fa = forward_messages_batch(channel_id, [target_channel_id], messages, args.check, args.force)
                        print(f"[FORWARD] 完成: 转发 {f}, 跳过 {s}, 失败 {fa}")
        else:
            # 递归转发
            if args.force:
                all_messages = []
                for ch_id in channel_ids:
                    try:
                        sync_channel_for_forward(ch_id)
                    except Exception as e:
                        print(f"[FORWARD] 同步频道 {ch_id} 失败: {e}")
                        continue

                    with get_db() as conn:
                        msgs = find_messages_to_forward(conn, ch_id, reaction_limit, views_limit, filter_by_source=False)
                        if msgs:
                            all_messages.extend(msgs)

                if not all_messages:
                    print("[FORWARD] 所有频道无可转发消息")
                    return

                summary = summarize_messages_for_forward(get_db(), all_messages)
                print(f"[FORWARD] 待转发消息统计（{len(channel_ids)} 个频道）：")
                print(f"  合计: {summary['total_count']} 条消息, {summary['media_count']} 条有媒体, {summary['total_size_mb']:.1f} MB")
                print()

                if not confirm_forward(all_messages, summary):
                    print("[FORWARD] 已取消")
                    return

                print("[FORWARD] 开始转发...")
                total_f, total_s, total_fa = 0, 0, 0
                for ch_id in channel_ids:
                    ch_msgs = [m for m in all_messages if m.get('source_id') == ch_id]
                    if ch_msgs:
                        f, s, fa = forward_messages_batch(ch_id, [target_channel_id], ch_msgs, args.check, args.force)
                        total_f += f
                        total_s += s
                        total_fa += fa
                        print(f"[FORWARD] 频道 {ch_id} 完成: 转发 {f}, 跳过 {s}, 失败 {fa}")

                print("\n[FORWARD] ========== 全部完成 ==========")
                print(f"[FORWARD] 总计: 转发 {total_f}, 跳过 {total_s}, 失败 {total_fa}")
                return
            else:
                for ch_id in channel_ids:
                    try:
                        sync_channel_for_forward(ch_id)
                    except Exception as e:
                        print(f"[FORWARD] 同步频道 {ch_id} 失败: {e}")
                        continue
                    processed_channels: set[int] = set()
                    nf, ns, nfa = forward_with_recursion(
                        source_channels=[ch_id],
                        target_channel=target_channel_id,
                        current_depth=1,
                        max_depth=recursion_depth,
                        processed_channels=processed_channels,
                        check_exists=args.check,
                        force=args.force,
                        reaction_limit=reaction_limit,
                        views_limit=views_limit,
                        max_source_channels=default_max_source_channels,
                    )
                    print(f"[FORWARD] 频道 {ch_id} 递归转发完成: 转发 {nf}, 跳过 {ns}, 失败 {nfa}")
                return