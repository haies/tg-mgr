"""
高反应消息复制模块

功能：
- 找出频道中高反应消息（次数 > 50 或 非零 top 10）
- 自动将符合条件的消息复制到目标频道

使用：
- ./tg forward <源频道ID1> [<源频道ID2> ...] [-o <目标频道ID>] [-c]
"""
import logging
import sqlite3
import sys
import time
from typing import Any, Optional

from pyrogram import Client, errors

from database import get_database_path, get_db_connection
from database.query import find_high_reaction_messages as query_high_reaction
from database.query import find_reaction_messages_over_threshold
from utils.media import row_to_reaction_dict
from utils.telegram_client import get_client, get_config, get_log_path
from utils.telegram_link import get_channel_address

logger = logging.getLogger(__name__)


def find_high_reaction_messages(channel_id: int, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """查找高反应消息
    - 如果 >50 的消息超过 10 条，全部转发
    - 否则转发 >0 的 top10
    """
    # 获取 >50 的消息
    over50_results = find_reaction_messages_over_threshold(conn, threshold=50)
    over50 = {row[0]: row for row in over50_results}

    # 如果 >50 的消息超过 10 条，全部转发
    if len(over50) > 10:
        results = []
        for msg_id, row in sorted(over50.items(), key=lambda x: x[1][3], reverse=True):
            results.append(row_to_reaction_dict((msg_id, row[1], row[2], row[3])))
        return results

    # 否则转发 >0 的 top10
    results = []
    for row in query_high_reaction(conn, min_total=0, limit=10):
        results.append(row_to_reaction_dict(row))
    return results


def message_exists_in_channel(client: Client, target_channel_id: int, source_msg_id: int) -> bool:
    """检查消息是否已存在于目标频道"""
    try:
        for msg in client.get_chat_history(target_channel_id, limit=100):
            if msg.id == source_msg_id:
                return True
        return False
    except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
        return False
    except Exception as e:
        logger.debug(f"检查消息是否存在失败: {e}")
        return False


def join_channel(client: Client, channel_id: int) -> bool:
    """尝试加入频道"""
    try:
        client.join_chat(channel_id)
        print(f"[FORWARD] 已加入频道 {channel_id}")
        return True
    except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
        print(f"[FORWARD] 频道 {channel_id} 无法访问")
        return False
    except Exception as e:
        logger.debug(f"通过 ID 加入频道失败: {e}")

    try:
        chat = client.get_chat(channel_id)
        if hasattr(chat, 'username') and chat.username:
            invite_link = f"https://t.me/{chat.username}"
            client.join_chat(invite_link)
            print(f"[FORWARD] 已通过链接加入频道 {channel_id}")
            return True
    except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
        print(f"[FORWARD] 频道 {channel_id} 无法访问")
        return False
    except Exception as e:
        logger.debug(f"通过用户名加入频道失败: {e}")

    return False


def is_channel_forwarding_allowed(client: Client, channel_id: int) -> bool:
    """检查频道是否允许转发

    Returns:
        True: 允许转发
        False: 禁止转发（禁止转发内容或需要管理员权限）
    """
    try:
        chat = client.get_chat(channel_id)
        # 检查是否启用保护内容（禁止转发）
        if hasattr(chat, 'has_protected_content') and chat.has_protected_content:
            return False
        return True
    except errors.BadRequest as e:
        if "CHAT_FORWARDS_RESTRICTED" in str(e):
            return False
        if "CHAT_ADMIN_REQUIRED" in str(e):
            return False
        raise
    except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
        return False


def forward_messages(source_channel_ids: list[int], target_channel_ids: list[int], check_exists: bool = False) -> None:
    """转发高反应消息到目标频道

    Args:
        source_channel_ids: 源频道ID列表
        target_channel_ids: 目标频道ID列表
        check_exists: 是否在转发前检查目标频道是否已存在该消息
    """
    from datetime import datetime

    log_file = get_log_path('forward.log')

    # 记录日期（只在文件不存在或今天首次运行时）
    today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_date = False
    if not log_file.exists():
        write_date = True
    else:
        with open(log_file) as f:
            first_line = f.readline().strip()
            if first_line != today:
                write_date = True

    # 统计
    total_forwarded = 0
    total_skipped = 0
    total_failed = 0

    # 遍历每个源频道
    for source_channel_id in source_channel_ids:
        print(f"\n[FORWARD] ========== 处理源频道: {source_channel_id} ==========")

        # 先检查频道是否允许转发
        print(f"[FORWARD] 检查频道 {source_channel_id} 是否允许转发...")
        with get_client('tg-mgr') as client:
            if not is_channel_forwarding_allowed(client, source_channel_id):
                print(f"[FORWARD] 频道 {source_channel_id} 禁止转发，跳过该频道")
                continue
        print(f"[FORWARD] 频道 {source_channel_id} 允许转发，继续处理")

        # 同步数据
        print(f"[FORWARD] 正在同步频道 {source_channel_id} 的数据...")

        # 删除旧数据库
        db_path = get_database_path()
        if db_path.exists():
            db_path.unlink()

        # 同步
        from modules.clean import run_sync as sync_channel
        sync_channel(channel_id=str(source_channel_id))

        conn = get_db_connection()

        # 查找高反应消息
        messages = find_high_reaction_messages(source_channel_id, conn)
        print(f"[FORWARD] 找到 {len(messages)} 条高反应消息")

        if not messages:
            print("[FORWARD] 没有符合条件的高反应消息")
            conn.close()
            continue

        print(f"[FORWARD] 目标频道: {target_channel_ids}")
        if check_exists:
            print("[FORWARD] 模式: 检查目标频道是否已存在该消息")
        print("[FORWARD] 开始转发消息...")

        # 转发消息
        forwarded_count = 0
        skipped_count = 0
        failed_count = 0

        with get_client('tg-mgr') as client:
            # 尝试加入未加入的频道
            for target_id in target_channel_ids:
                try:
                    client.get_chat(target_id)
                    print(f"[FORWARD] 目标频道 {target_id} 已可访问")
                except Exception as e:
                    logger.debug(f"获取频道 {target_id} 信息失败: {e}")
                    print(f"[FORWARD] 目标频道 {target_id} 未加入，正在尝试加入...")
                    if join_channel(client, target_id):
                        print(f"[FORWARD] 成功加入频道 {target_id}")
                    else:
                        print(f"[FORWARD] 无法加入频道 {target_id}，将跳过该频道")

            # 逐个目标频道进行处理
            for target_id in target_channel_ids:
                print(f"\n[FORWARD] 开始处理目标频道: {target_id}")

                for msg in messages:
                    msg_id = msg['message_id']
                    total = msg['total']
                    link = f"{get_channel_address(source_channel_id)}/{msg_id}"

                    # 如果启用检查模式，先检查目标频道是否已存在该消息
                    if check_exists:
                        print(f"[FORWARD] 检查消息是否存在: {link}")
                        if message_exists_in_channel(client, target_id, msg_id):
                            print(f"[FORWARD] 跳过: 消息已存在于目标频道 {target_id}")
                            skipped_count += 1
                            continue

                    try:
                        # 通过转发方式发送消息
                        client.copy_message(
                            chat_id=target_id,
                            from_chat_id=source_channel_id,
                            message_id=msg_id
                        )
                        forwarded_count += 1
                        print(f"[FORWARD] #{forwarded_count} 复制成功: {link} -> {target_id} (总计: {total})")

                        # 写入日志
                        if write_date:
                            with open(log_file, 'w') as f:
                                f.write(f"{today}\n")
                            write_date = False
                        with open(log_file, 'a') as f:
                            f.write(f"{link}\n")
                    except errors.Forbidden:
                        print(f"[FORWARD] 频道 {target_id} 禁止复制，跳过该频道")
                        break
                    except errors.BadRequest as e:
                        if "CHAT_ADMIN_REQUIRED" in str(e):
                            print(f"[FORWARD] 频道 {target_id} 需要管理员权限，跳过该频道")
                            break
                        if "CHAT_FORWARDS_RESTRICTED" in str(e):
                            print(f"[FORWARD] 频道 {target_id} 禁止转发内容，跳过该频道")
                            break
                        raise
                    except errors.FloodWait as e:
                        wait = e.value if e.value > 5 else 5
                        print(f"[FORWARD] FloodWait: 等待 {wait} 秒...")
                        time.sleep(wait)
                        # 重试（最多3次）
                        retry_success = False
                        for retry in range(3):
                            try:
                                client.copy_message(
                                    chat_id=target_id,
                                    from_chat_id=source_channel_id,
                                    message_id=msg_id
                                )
                                forwarded_count += 1
                                print(f"[FORWARD] #{forwarded_count} 复制成功: {link} -> {target_id} (总计: {total})")

                                # 写入日志
                                if write_date:
                                    with open(log_file, 'w') as f:
                                        f.write(f"{today}\n")
                                    write_date = False
                                with open(log_file, 'a') as f:
                                    f.write(f"{link}\n")
                                retry_success = True
                                break
                            except errors.FloodWait as e2:
                                wait = e2.value if e2.value > 5 else 5
                                print(f"[FORWARD] FloodWait重试 {retry+1}/3: 等待 {wait} 秒...")
                                time.sleep(wait)
                            except errors.Forbidden:
                                print(f"[FORWARD] 频道 {target_id} 禁止复制，跳过该频道")
                                break
                            except errors.BadRequest as e2:
                                if "CHAT_ADMIN_REQUIRED" in str(e2):
                                    print(f"[FORWARD] 频道 {target_id} 需要管理员权限，跳过该频道")
                                    break
                                if "CHAT_FORWARDS_RESTRICTED" in str(e2):
                                    print(f"[FORWARD] 频道 {target_id} 禁止转发内容，跳过该频道")
                                    break
                                print(f"[FORWARD] 复制失败: {link} -> {target_id} - {e2}")
                                break
                            except Exception as e2:
                                print(f"[FORWARD] 复制失败: {link} -> {target_id} - {e2}")
                                break
                        if not retry_success and retry == 2:
                            failed_count += 1
                            print(f"[FORWARD] 复制失败（已重试3次）: {link} -> {target_id}")
                    except Exception as e:
                        failed_count += 1
                        print(f"[FORWARD] 复制失败: {link} -> {target_id} - {e}")
                        continue

                    # 避免频率限制
                    time.sleep(0.5)

                print(f"[FORWARD] 频道 {target_id} 处理完成")

        conn.close()
        total_forwarded += forwarded_count
        total_skipped += skipped_count
        total_failed += failed_count

    print("\n[FORWARD] ========== 全部完成 ==========")
    print(f"[FORWARD] 总计: 成功 {total_forwarded} 条, 跳过 {total_skipped} 条, 失败 {total_failed} 条")


def main():
    """主执行流程"""
    import argparse

    parser = argparse.ArgumentParser(description='高反应消息转发模块')
    parser.add_argument('source_channels', nargs='+', type=int, help='源频道ID（1个或多个）')
    parser.add_argument('-o', '--target', type=int, help='目标频道ID（不指定则使用config.json中的ID）')
    parser.add_argument('-c', '--check', action='store_true', help='转发前检查目标频道是否已存在该消息')

    args = parser.parse_args()

    config = get_config()
    target_channel_id = args.target if args.target else config.get('channel_id')

    if not target_channel_id:
        print("[ERROR] 未指定目标频道，且config.json中未配置channel_id")
        sys.exit(1)

    # 调用转发函数（target_channel_id 转为列表）
    forward_messages(args.source_channels, [target_channel_id], check_exists=args.check)


if __name__ == "__main__":
    main()
