"""
同步模块

负责从 Telegram 频道同步消息到本地数据库

提供统一的同步接口供其他模块使用
"""

import sqlite3
import time
from pathlib import Path

from database import get_database_path, get_schema_path
from database.messages import (
    get_existing_files,
    get_last_processed_id,
    get_message_stats,
    init_database,
    insert_messages,
)
from utils.media import extract_media_info
from utils.telegram_client import get_client, get_config


def force_reset_database() -> None:
    """统一的重置数据库逻辑（删除旧数据库）"""
    db_path = get_database_path()
    if db_path.exists():
        db_path.unlink()
        print("[INFO] 已删除旧数据库...")


def sync_channel(channel_id: str | None = None, db_path: str | None = None, joined_channels: set[int] | None = None) -> bool:
    """同步频道消息到数据库

    Args:
        channel_id: 频道ID，如果为None则从配置文件读取
        db_path: 可选的数据库路径（用于临时同步）
        joined_channels: 用于收集自动加入的频道ID集合

    Returns:
        True if channel was synced successfully (may have auto-joined), False otherwise
    """
    config = get_config()
    _channel_id = channel_id if channel_id else config["channel_id"]

    # 如果指定了自定义数据库路径，设置环境变量
    import os

    original_db_path = os.environ.get("TG_MGR_DB_PATH")
    db_path_was_set = False
    if db_path:
        os.environ["TG_MGR_DB_PATH"] = db_path
        db_path_was_set = True

    try:
        return _sync_impl(_channel_id, joined_channels=joined_channels)
    finally:
        # 恢复原始数据库路径
        if db_path_was_set:
            if original_db_path is not None:
                os.environ["TG_MGR_DB_PATH"] = original_db_path
            elif "TG_MGR_DB_PATH" in os.environ:
                del os.environ["TG_MGR_DB_PATH"]


def _sync_impl(_channel_id: str, _db_path: Path | None = None, joined_channels: set[int] | None = None) -> bool:
    """同步实现

    Args:
        _channel_id: 频道ID
        _db_path: 可选的数据库路径（优先使用环境变量 TG_MGR_DB_PATH）
        joined_channels: 用于收集自动加入的频道ID集合

    Returns:
        True if sync succeeded, False if channel is inaccessible
    """
    import os

    schema_path = get_schema_path()

    # 优先使用环境变量（由 sync_channel 设置），其次使用传入的参数，最后使用默认路径
    db_path_str = os.environ.get("TG_MGR_DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str)
    elif _db_path:
        db_path = _db_path
    else:
        db_path = get_database_path()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout = 30000")  # 30s timeout to avoid "database is locked"
    conn.execute("PRAGMA journal_mode = WAL")  # write-ahead log for better concurrency
    try:
        # 执行 schema
        with open(schema_path) as f:
            cursor = conn.cursor()
            cursor.executescript(f.read())

        # 初始化数据库
        init_database(conn)

        last_processed_id = get_last_processed_id(conn, int(_channel_id))

        with get_client("tg-mgr") as client:
            start_time = time.time()

            # 预检查：获取频道最新消息 ID，验证是否有新消息可同步
            # 原因：Telegram API 对 offset_id > MAX(message_id) 会静默钳制到 0，
            # 导致每次都从最新消息重新开始，而非跳过已处理消息
            latest_msgs = list(client.get_chat_history(_channel_id, limit=1))
            if not latest_msgs:
                print(f"[SYNC] 频道 {_channel_id} 无消息或无法访问")
                return False
            latest_message_id = latest_msgs[0].id

            if last_processed_id > 0 and latest_message_id <= last_processed_id:
                print(f"[SYNC] 频道 {_channel_id} 已是最新（本地最高: {last_processed_id}，频道最新: {latest_message_id}）")
                return True

            # 判断是否需要从最新消息向前查找新消息
            # 原因：当 offset_id > MAX(message_id) 时，Telegram API 会钳制到最新消息，
            # 导致 offset_id=last_processed_id+1 失效。此时改为从 offset_id=0 开始，
            # 客户端过滤只接受 message.id > last_processed_id 的消息
            if last_processed_id > 0 and latest_message_id > last_processed_id:
                sync_mode = "forward_from_latest"
                print(f"[SYNC] 开始同步频道 {_channel_id}，从最新消息 {latest_message_id} 向前（新增区间: {last_processed_id + 1}~{latest_message_id}）...")
            else:
                sync_mode = "backward_from_offset"
                print(f"[SYNC] 开始同步频道 {_channel_id}，从消息ID #{last_processed_id + 1} 开始...")

            # 初始化已处理文件集合（只考虑当前频道）
            seen_files = get_existing_files(conn, int(_channel_id))

            # 初始化计数器
            total_fetched = 0   # API 返回的消息总数
            total_new = 0       # 实际入库的新消息数
            total_skipped = 0  # 跳过的消息数（无 file_unique_id）

            # 用于统计媒体组大小
            media_group_sizes: dict[str, int] = {}

            # 同步消息
            batch_size = 100
            # 首次同步用 offset_id=0；增量时：
            #   - backward 模式：offset_id = last_processed_id + 1（向更旧方向）
            #   - forward 模式：offset_id = 0（从最新向前，客户端过滤）
            offset_id = last_processed_id + 1 if last_processed_id > 0 else 0
            has_more = True
            sync_success = True
            stop_after_new_messages = sync_mode == "forward_from_latest"

            from pyrogram import errors

            while has_more:
                batch_messages = []
                try:
                    for message in client.get_chat_history(
                        _channel_id, offset_id=offset_id, limit=batch_size
                    ):
                        batch_messages.append(message)
                        offset_id = message.id
                except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
                    # 同步失败，尝试加入频道
                    print(f"[SYNC] 频道 {_channel_id} 无法访问，尝试加入...")
                    if joined_channels is not None and _try_join_channel(client, int(_channel_id)):
                        joined_channels.add(int(_channel_id))
                        print(f"[SYNC] 已加入频道 {_channel_id}，重新尝试同步...")
                        # 重新获取历史
                        try:
                            for message in client.get_chat_history(
                                _channel_id, offset_id=offset_id, limit=batch_size
                            ):
                                batch_messages.append(message)
                                offset_id = message.id
                        except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
                            print(f"[SYNC] 加入后仍无法访问频道 {_channel_id}")
                            sync_success = False
                            break
                    else:
                        print(f"[SYNC] 无法加入频道 {_channel_id}")
                        sync_success = False
                        break

                if not batch_messages:
                    has_more = False
                    break

                # forward_from_latest 模式：过滤掉已处理的消息
                if stop_after_new_messages:
                    new_only = [m for m in batch_messages if m.id > last_processed_id]
                    old_count = len(batch_messages) - len(new_only)
                    if old_count > 0:
                        print(f" [过滤已处理: {old_count}]\r", end="", flush=True)
                    # 如果所有消息都 <= last_processed_id，说明已越过新消息区间
                    if not new_only:
                        has_more = False
                        break
                    batch_messages = new_only

                # 先计算媒体组大小
                for msg in batch_messages:
                    if msg.media_group_id:
                        media_info = extract_media_info(msg)
                        if media_info.file_size:
                            media_group_sizes[msg.media_group_id] = media_group_sizes.get(msg.media_group_id, 0) + media_info.file_size

                cursor = conn.cursor()
                batch_new, batch_dup, batch_skipped = insert_messages(cursor, batch_messages, seen_files, int(_channel_id), media_group_sizes)
                total_fetched += len(batch_messages)
                total_new += len(batch_new)
                total_skipped += batch_skipped
                conn.commit()  # 必须提交，否则数据回滚
                print(f"[SYNC] 处理进度 - API返回: {total_fetched} | 实际新增: {total_new} | 重复: {total_fetched - total_new - total_skipped}\r", end="", flush=True)

            # 统计各类消息数量（仅当前频道）
            stats = get_message_stats(conn, int(_channel_id))

            # 一行内显示统计信息
            stat_parts = [f"API返回: {total_fetched} | 新增入库: {total_new}"]
            total_invalid = 0
            total_duplicate = 0
            for media_type, total, invalid_count, duplicate_count in stats:
                total_invalid += invalid_count
                total_duplicate += duplicate_count
                extra = ""
                if invalid_count:
                    extra += f", 无效{invalid_count}"
                if duplicate_count:
                    extra += f", 重复{duplicate_count}"
                stat_parts.append(f"{media_type}: {total}{extra}")
            stat_parts.append(f"跳过: {total_skipped}")

            print(f"[SYNC] 统计 - {' | '.join(stat_parts)}")

            end_time = time.time()
            duration = end_time - start_time
            print(f"[SYNC] 同步完成，耗时: {duration:.2f} 秒")
            return sync_success
    finally:
        conn.close()


def _try_join_channel(client, channel_id: int) -> bool:
    """尝试加入频道"""
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


def run_sync(channel_id: str | None = None) -> None:
    """主同步流程（兼容旧接口）

    Args:
        channel_id: 频道ID，如果为None则从配置文件读取
    """
    sync_channel(channel_id=channel_id)
