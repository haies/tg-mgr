"""转发模块核心逻辑 - main() 入口"""
import sys
from pathlib import Path
from typing import Any

from pyrogram import Client

# 确保 src/ 在 sys.path 中
src_path = Path(__file__).parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# 模块级导入（支持测试 patch）
from utils.telegram_client import DEFAULT_CONFIG, get_client, get_config
from modules.sync import sync_channel
from database import get_db


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


def _get_reaction_total(message):
    """从消息中提取反应总数"""
    from utils.media import extract_reaction_data
    return extract_reaction_data(message).total


def get_channel_address(channel_id: int) -> str:
    """获取频道链接地址，与 telegram_link.py 保持一致"""
    return f"https://t.me/c/{abs(channel_id)}"


def main():
    """主执行流程"""
    import argparse

    # 从各子模块导入需要的函数
    from modules.forward.cli import parse_source_arg, resolve_username_to_channel_id
    from modules.forward.preview import summarize_messages_for_forward, confirm_forward
    from modules.forward.recursive import sync_channel_for_forward, is_channel_forwarding_allowed, forward_with_recursion
    from modules.forward.recursive import find_messages_to_forward
    from modules.forward.send import (
        forward_single_message,
        _get_original_media_group_message,
        _get_media_group_messages,
        _forward_media_group,
        forward_messages_batch,
        _build_stats_str,
        get_channel_address as _get_channel_address,
    )
    from modules.forward.force import (
        _force_send_single_message,
        _force_send_media_group,
    )
    from database.query import VIEWS_THRESHOLD_MULTIPLIER

    parser = argparse.ArgumentParser(description="高反应消息转发模块")
    parser.add_argument("sources", nargs="+", help="源频道ID或消息链接")
    parser.add_argument("-o", "--target", type=int, help="目标频道ID")
    parser.add_argument("-c", "--check", action="store_true", help="转发前检查目标频道是否已存在")
    parser.add_argument(
        "-r", "--depth", type=int, nargs="?", const=5, default=None,
        help="递归深度（-r3 或 -r 3，默认5，0表示不递归）"
    )
    parser.add_argument("-f", "--force", action="store_true",
        help="强制转发禁止转发的消息（通过复制内容而非转发）")
    parser.add_argument("-l", "--limit", type=int, default=None,
        help="高反应消息数量限制（默认从配置文件读取）")
    parser.add_argument("-v", "--views-limit", type=int, dest="views_limit", default=None,
        help="高浏览量消息数量限制（默认从配置文件读取）")

    args = parser.parse_args()

    config = get_config()
    target_channel_id = args.target if args.target else config.get("channel_id")

    if not target_channel_id:
        print("[ERROR] 未指定目标频道，且环境变量 TG_CHANNEL_ID 未配置")
        sys.exit(1)

    # 从配置读取默认值（程序传入的参数可覆盖）
    default_reaction_limit = config.get("reaction_limit") or DEFAULT_CONFIG["reaction_limit"]
    default_views_limit = config.get("views_limit") or DEFAULT_CONFIG["views_limit"]
    default_max_source_channels = config.get("max_source_channels", default_reaction_limit)

    # 从配置读取递归深度（默认 5），-r 参数可覆盖
    configured_depth = config.get("recursion_depth") or DEFAULT_CONFIG["recursion_depth"]
    recursion_depth = args.depth if args.depth is not None else configured_depth

    # 如果未指定 -r 参数且使用 -f，改为非递归模式（使用主数据库，与 info 保持一致）
    # 只有明确使用 -r 参数时才启用递归 temp DB 模式（避免 views 数据丢失）
    if args.force and args.depth is None and recursion_depth > 0:
        print("[FORWARD] 注意：-f 默认使用主数据库同步（与 tg info 一致）")
        print("[FORWARD] 如需递归模式，请额外指定 -r 参数")
        recursion_depth = 0

    reaction_limit = args.limit if args.limit is not None else default_reaction_limit
    views_limit = args.views_limit if args.views_limit is not None else default_views_limit

    # 分离链接和频道ID
    channel_ids = []
    link_messages = []  # [(channel_id, message_id, username), ...]

    for source in args.sources:
        parsed = parse_source_arg(source)
        if parsed[0] is None and parsed[2] is None:
            # 无法解析，跳过
            continue
        if parsed[1] is not None:
            # 链接：直接转发，不递归
            link_messages.append(parsed)  # (channel_id or None, message_id, username or None)
        else:
            channel_ids.append(parsed[0])

    # 处理链接参数（直接转发，不递归）
    if link_messages:
        print(f"[FORWARD] 处理 {len(link_messages)} 条直接转发...")
        force = args.force
        with get_client("tg-mgr") as client:
            # 确保已加入目标频道
            try:
                client.get_chat(target_channel_id)
            except Exception:
                if _join_channel(client, target_channel_id):
                    print(f"[FORWARD] 已加入目标频道 {target_channel_id}")

            for channel_id, msg_id, username in link_messages:
                # 解析用户名（如果需要）
                if channel_id is None and username:
                    resolved_id = resolve_username_to_channel_id(client, username)
                    if resolved_id is None:
                        print(f"[ERROR] 无法解析用户名 {username}，跳过")
                        continue
                    channel_id = resolved_id

                link = f"{_get_channel_address(channel_id)}/{msg_id}"
                try:
                    # 检查是否是媒体组消息
                    original_msg = _get_original_media_group_message(client, channel_id, msg_id)
                    if original_msg and original_msg.media_group_id:
                        # 媒体组消息
                        media_group_msgs = _get_media_group_messages(client, channel_id, original_msg.media_group_id, msg_id)
                        if media_group_msgs:
                            # 有完整媒体组，尝试转发
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
                                    # force 模式：下载后重新上传（保持媒体组结构）
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
                            # 媒体组消息但找不到其他同组消息，降级为重新发送
                            _force_send_single_message(client, target_channel_id, original_msg)
                            total = _get_reaction_total(original_msg)
                            views = original_msg.views or 0
                            stats = _build_stats_str(total, views, is_media_group=True)
                            print(f"[FORWARD] 直接转发成功: {link}{stats}")
                    else:
                        # 普通消息
                        if force:
                            _force_send_single_message(client, target_channel_id, original_msg)
                            total = _get_reaction_total(original_msg)
                            views = original_msg.views or 0
                            stats = _build_stats_str(total, views)
                            print(f"[FORWARD] 强制转发成功: {link}{stats}")
                        else:
                            # 普通消息，使用 copy_message
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
            # 不递归，只处理当前频道
            print(f"[FORWARD] 处理 {len(channel_ids)} 个频道（无递归）...")
            for channel_id in channel_ids:
                print(f"[FORWARD] ========== 处理频道: {channel_id} ==========")

                # 检查权限（force模式跳过）
                with get_client("tg-mgr") as client:
                    if not args.force and not is_channel_forwarding_allowed(client, channel_id):
                        print(f"[FORWARD] 频道 {channel_id} 禁止转发，跳过")
                        continue

                # 同步到主数据库（与 info 保持一致，避免 temp DB 中 views=0 导致数据不一致）
                print(f"[FORWARD] 同步频道 {channel_id}...")
                try:
                    sync_channel(channel_id=str(channel_id))
                except Exception as e:
                    print(f"[FORWARD] 同步失败: {e}")
                    continue

                # 使用主数据库查询（与 info 保持一致）
                with get_db() as conn:
                    messages = find_messages_to_forward(conn, channel_id, reaction_limit, views_limit, filter_by_source=False)

                    # 使用 -f 时先统计后确认
                    if args.force and messages:
                        summary = summarize_messages_for_forward(conn, messages)
                        if not confirm_forward(messages, summary):
                            print("[FORWARD] 已取消")
                            return

                    if messages:
                        # 统计高反应和高浏览量消息数量
                        avg_row = conn.execute("SELECT COALESCE(AVG(views), 0) FROM messages WHERE views > 0").fetchone()
                        avg_views = avg_row[0] if avg_row and isinstance(avg_row[0], (int, float)) else 0
                        threshold = avg_views * VIEWS_THRESHOLD_MULTIPLIER if avg_views > 0 else 0
                        high_reaction_count = sum(1 for m in messages if m.get("total", 0) > 0)
                        high_views_count = sum(1 for m in messages if m.get("views", 0) > threshold)
                        print(f"[FORWARD] 频道 {channel_id} 找到 {len(messages)} 条消息 (高反应: {high_reaction_count}, 高浏览: {high_views_count})")
                    else:
                        print(f"[FORWARD] 频道 {channel_id} 找到 0 条消息")
                    if messages:
                        f, s, fa = forward_messages_batch(channel_id, [target_channel_id], messages, args.check, args.force)
                        print(f"[FORWARD] 完成: 转发 {f}, 跳过 {s}, 失败 {fa}")
        else:
            # 递归转发
            if args.force:
                # 同步所有频道到主数据库并获取消息
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

                # 显示统计信息
                summary = summarize_messages_for_forward(get_db(), all_messages)
                print(f"[FORWARD] 待转发消息统计（{len(channel_ids)} 个频道）：")
                print(f"  合计: {summary['total_count']} 条消息, {summary['media_count']} 条有媒体, {summary['total_size_mb']:.1f} MB")
                print()

                if not confirm_forward(all_messages, summary):
                    print("[FORWARD] 已取消")
                    return

                # 确认后转发
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
                # 非 force 模式：直接调用递归转发
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


def _join_channel(client: Client, channel_id: int) -> bool:
    """尝试加入频道"""
    from pyrogram import errors
    try:
        client.join_chat(channel_id)  # type: ignore[unused-coroutine]
        return True
    except Exception:
        pass

    try:
        chat = client.get_chat(channel_id)
        if hasattr(chat, "username") and chat.username:
            client.join_chat(f"https://t.me/{chat.username}")  # type: ignore[unused-coroutine]
            return True
    except Exception:
        pass

    return False


# 向后兼容别名 - 移到 recursive.py
def find_high_reaction_messages(channel_id: int, conn) -> list:
    """向后兼容别名"""
    from modules.forward.recursive import find_messages_to_forward as _find
    return _find(conn, channel_id)


if __name__ == "__main__":
    main()
