"""
同步模块

负责从 Telegram 频道同步消息到本地数据库

提供统一的同步接口供其他模块使用
"""

import sqlite3
import time

from database import get_database_path, get_schema_path
from database.messages import (
    get_existing_files,
    get_last_processed_id,
    get_message_stats,
    init_database,
    insert_messages,
)
from utils.telegram_client import get_client, get_config


def sync_channel(channel_id: str | None = None, db_path: str | None = None) -> None:
    """同步频道消息到数据库

    Args:
        channel_id: 频道ID，如果为None则从配置文件读取
        db_path: 可选的数据库路径（用于临时同步）
    """
    config = get_config()
    _channel_id = channel_id if channel_id else config["channel_id"]

    # 如果指定了自定义数据库路径，设置环境变量
    import os

    original_db_path = None
    if db_path:
        original_db_path = os.environ.get("TG_MGR_DB_PATH")
        os.environ["TG_MGR_DB_PATH"] = db_path

    try:
        _sync_impl(_channel_id)
    finally:
        # 恢复原始数据库路径
        if original_db_path is not None:
            os.environ["TG_MGR_DB_PATH"] = original_db_path
        elif "TG_MGR_DB_PATH" in os.environ:
            del os.environ["TG_MGR_DB_PATH"]


def _sync_impl(_channel_id: str) -> None:
    """同步实现"""
    schema_path = get_schema_path()
    db_path = get_database_path()

    conn = sqlite3.connect(str(db_path))

    # 执行 schema
    with open(schema_path) as f:
        cursor = conn.cursor()
        cursor.executescript(f.read())

    # 初始化数据库
    init_database(conn)

    last_processed_id = get_last_processed_id(conn)

    with get_client("tg-mgr") as client:
        start_time = time.time()
        print(f"[SYNC] 开始同步，从消息ID #{last_processed_id + 1} 开始...")
        print(f"[DEBUG] CHANNEL_ID: {_channel_id}")

        # 初始化已处理文件集合
        seen_files = get_existing_files(conn)

        # 初始化计数器
        total_messages = 0
        total_skipped = 0

        # 同步消息
        batch_size = 100
        offset_id = last_processed_id
        has_more = True

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
                print(f"[SYNC] 频道 {_channel_id} 无法访问")
                break

            if not batch_messages:
                has_more = False
                break

            cursor = conn.cursor()
            _, _, batch_skipped = insert_messages(cursor, batch_messages, seen_files)
            total_messages += len(batch_messages)
            total_skipped += batch_skipped
            print(f"[SYNC] 处理进度 - 总消息数: {total_messages}\r", end="", flush=True)

        # 统计各类消息数量
        stats = get_message_stats(conn)

        print("\n[SYNC] 消息数量统计:")
        for media_type, total, invalid_count, duplicate_count in stats:
            print(f"  {media_type}: {total}")
            if invalid_count:
                print(f"    - 无效数量: {invalid_count}")
            if duplicate_count:
                print(f"    - 重复数量: {duplicate_count}")

        if total_skipped > 0:
            print(f"  跳过（无file_unique_id）: {total_skipped}")

        conn.close()
        end_time = time.time()
        duration = end_time - start_time
        print(f"[SYNC] 同步完成，耗时: {duration:.2f} 秒")


def run_sync(channel_id: str | None = None) -> None:
    """主同步流程（兼容旧接口）

    Args:
        channel_id: 频道ID，如果为None则从配置文件读取
    """
    sync_channel(channel_id=channel_id)
