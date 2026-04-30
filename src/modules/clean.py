"""
统一清理模块

功能：
1. 增量同步消息 (sync)
2. 检测并删除重复消息 (deduplicate)
3. 检测并删除无效消息 (deinvalid)

参数组合：
- 无参数: 仅执行sync
- -d: sync + deduplicate
- -i: sync + deinvalid
- -di/-id: sync + deduplicate + deinvalid

使用：
- 仅同步: python modules/clean.py
- 同步+去重: python modules/clean.py -d
- 同步+清理无效: python modules/clean.py -i
- 同步+去重+清理无效: python modules/clean.py -di
"""
import argparse
import json
import sqlite3
import sys
import time
from typing import Optional

from pyrogram import Client, errors, types

from database import get_database_path, get_schema_path, get_db
from utils.telegram_client import get_client, get_config
from utils.telegram_link import generate_tg_link
from utils.media import extract_media_info, extract_reaction_data, extract_source_id

# ====== SYNC FUNCTIONALITY ======

def init_database() -> sqlite3.Connection:
    """初始化数据库连接并创建表结构"""
    db_path = get_database_path()
    schema_path = get_schema_path()

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 执行schema.sql内容
    with open(schema_path) as f:
        cursor.executescript(f.read())

    # 确保channels表存在
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    ''')

    # 创建索引以优化查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_unique_id ON messages(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON messages(media_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_valid ON messages(is_valid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_duplicate ON messages(is_duplicate)")

    conn.commit()
    return conn

def check_restricted(message: types.Message) -> str:
    """
    多维度判断消息是否受限或无法显示
    """
    # 1. 基础类型检查
    if not message or hasattr(message, "empty") and message.empty:
        return "message is empty"

    # 2. 检查消息本身的限制原因 (Message Level)
    restrictions = getattr(message, "restrictions", None)
    if restrictions:
        for r in restrictions:
            reason = getattr(r, "reason", "").lower()
            # 定义不可跳过的硬性限制原因
            hard_restrictions = ["copyright", "violence", "scam", "terms", "user_opt_out"]

            if any(hard_reason in reason for hard_reason in hard_restrictions):
                return reason

    # 3. 检查转发源频道 (Chat Level)
    source_chat = getattr(message, "forward_from_chat", None)
    if source_chat:
        restrictions = getattr(source_chat, "restrictions", None)
        if restrictions:
            for r in restrictions:
                reason = getattr(r, "reason", "").lower()
                hard_restrictions = ["copyright", "violence", "scam", "terms", "user_opt_out"]

                if any(hard_reason in reason for hard_reason in hard_restrictions):
                    return reason

    # 4. 媒体有效性深度检查 - 只有当media对象存在但file_id为空时才算无效
    if message.media:
        media_obj = (message.video or message.photo or message.document or message.animation)
        if media_obj:
            # 检查file_id是否为空（这是最可靠的无效指标）
            if not getattr(media_obj, "file_id", None):
                return "media no file_id"

    return ""

def get_last_processed_id(conn: sqlite3.Connection) -> int:
    """获取最后处理的消息ID，用于断点续同步"""
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(message_id) FROM messages")
    result = cursor.fetchone()
    return result[0] or 0

def process_batch(client: Client, conn: sqlite3.Connection, messages: list, seen_files: set) -> int:
    """批量处理消息以提高性能"""
    cursor = conn.cursor()
    new_files = []
    duplicates = []
    skipped = 0

    # First pass: extract all required data and identify duplicates
    for message in messages:
        # 使用共享函数提取媒体信息
        media_info = extract_media_info(message)

        # Skip if no valid file_unique_id
        if not media_info.file_unique_id or media_info.file_unique_id == "":
            skipped += 1
            continue

        # Check for duplicates
        if media_info.file_unique_id in seen_files:
            duplicates.append((message.id, media_info.file_unique_id, media_info.file_size, media_info.media_type))
        else:
            # 使用共享函数提取反应数据
            reaction = extract_reaction_data(message)

            # Check message validity
            is_valid = 0 if check_restricted(message) else 1

            # 使用共享函数提取源频道 ID
            source_id = extract_source_id(message)

            # 提取消息文本
            caption = message.caption or message.text or ''

            new_files.append((
                message.id,
                media_info.file_unique_id,
                media_info.file_size,
                media_info.media_type,
                caption,
                0,
                is_valid,
                json.dumps({"positive": reaction.positive, "heart": reaction.heart}),
                source_id
            ))
            seen_files.add(media_info.file_unique_id)

    # Process duplicates in bulk
    if duplicates:
        for duplicate in duplicates:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, is_duplicate) "
                    "VALUES (?, ?, ?, ?, 1)",
                    duplicate
                )
            except sqlite3.IntegrityError:
                print(f"[SYNC] 消息 #{duplicate[0]} 已存在，跳过插入")
                continue

    # Process new messages with bulk insert
    if new_files:
        try:
            cursor.executemany(
                "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                new_files
            )
        except sqlite3.IntegrityError:
            for new_file in new_files:
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        new_file
                    )
                except sqlite3.IntegrityError:
                    print(f"[SYNC] 消息 #{new_file[0]} 已存在，跳过插入")

    conn.commit()
    return skipped

def run_sync(channel_id: Optional[str] = None) -> None:
    """主同步流程

    Args:
        channel_id: 频道ID，如果为None则从配置文件读取
    """
    config = get_config()
    _api_id = config['api_id']
    _api_hash = config['api_hash']
    _channel_id = channel_id if channel_id else config['channel_id']

    conn = init_database()
    last_processed_id = get_last_processed_id(conn)

    with get_client("tg-mgr") as client:
        start_time = time.time()
        print(f"[SYNC] 开始同步，从消息ID #{last_processed_id + 1} 开始...")
        print(f"[DEBUG] CHANNEL_ID: {_channel_id}")

        # 初始化已处理文件集合，包含数据库中已存在的文件
        seen_files = set()
        cursor = conn.cursor()
        cursor.execute("SELECT file_unique_id, message_id FROM messages WHERE is_duplicate = 0")
        for file_unique_id, message_id in cursor.fetchall():
            seen_files.add(file_unique_id)

        # 初始化计数器
        total_messages = 0
        total_skipped = 0

        # 同步消息并传递 seen_files 集合
        batch_size = 100
        offset_id = last_processed_id
        has_more = True

        while has_more:
            batch_messages = []
            try:
                for message in client.get_chat_history(_channel_id, offset_id=offset_id, limit=batch_size):
                    batch_messages.append(message)
                    offset_id = message.id
            except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
                print(f"[SYNC] 频道 {_channel_id} 无法访问")
                break

            if not batch_messages:
                has_more = False
                break

            batch_skipped = process_batch(client, conn, batch_messages, seen_files)
            total_messages += len(batch_messages)
            total_skipped += batch_skipped
            print(f"[SYNC] 处理进度 - 总消息数: {total_messages}\r", end="", flush=True)

        # 统计各类消息数量
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                media_type,
                COUNT(*) as total,
                SUM(is_valid=0) as invalid_count,
                SUM(is_duplicate=1) as duplicate_count
            FROM messages
            GROUP BY media_type
        """)
        stats = cursor.fetchall()

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

def exponential_backoff(retry_count: int, retry_delay_base: int) -> float:
    """计算等待时间：1s, 2s, 4s, 8s, 16s..."""
    return retry_delay_base * (2 ** retry_count) + (retry_count * 0.1)

def delete_message_safely(client: Client, conn: sqlite3.Connection, message_id: int, channel_id: str, retry_count: int = 0) -> bool:
    """安全删除消息（带重试机制）"""
    config = get_config()
    max_retries = config['max_retries']
    retry_delay_base = config['retry_delay_base']

    try:
        client.delete_messages(channel_id, message_id)
        print(f"    [CLEAN] 已从Telegram删除消息 #{message_id}")

        # 更新数据库中的 is_duplicate 标志
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE messages SET is_duplicate = 1 WHERE message_id = ?",
            (message_id,)
        )
        return True
    except errors.FloodWait as e:
        wait_time = max(e.value, 5)
        print(f"    [WARNING] FloodWait: 等待 {wait_time} 秒后重试...")
        time.sleep(wait_time)
        return delete_message_safely(client, conn, message_id, channel_id, retry_count)
    except Exception as e:
        if retry_count < max_retries:
            wait_time = exponential_backoff(retry_count, retry_delay_base)
            print(f"    [WARNING] 删除消息 #{message_id} 失败: {str(e)} - {retry_count+1}/{max_retries} 次重试 (等待 {wait_time:.1f} 秒)")
            time.sleep(wait_time)
            return delete_message_safely(client, conn, message_id, channel_id, retry_count + 1)
        print(f"    [ERROR] 删除消息 #{message_id} 失败: {str(e)}")
        return False

def find_duplicates(conn: sqlite3.Connection) -> list:
    """查找所有重复媒体组（基于文件唯一ID）"""
    cursor = conn.cursor()
    # 使用与清理脚本相同的分组逻辑（file_unique_id）
    cursor.execute("""
        SELECT file_unique_id, MAX(file_size), MIN(media_type), MIN(message_id) as keep_id
        FROM messages
        GROUP BY file_unique_id
        HAVING COUNT(*) > 1 AND file_unique_id != ''
    """)

    duplicates = []
    for file_unique_id, file_size, media_type, keep_id in cursor.fetchall():
        # 按时间排序获取消息ID列表（排除保留ID）
        cursor.execute("""
            SELECT message_id
            FROM messages
            WHERE file_unique_id = ? AND message_id != ?
            ORDER BY timestamp ASC
        """, (file_unique_id, keep_id))

        delete_ids = [row[0] for row in cursor.fetchall()]
        duplicates.append((file_size, media_type, keep_id, delete_ids))

    return duplicates

def run_deduplicate(delete: bool = False, channel_id: Optional[str] = None) -> None:
    """重复检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取
    """
    config = get_config()
    _api_id = config['api_id']
    _api_hash = config['api_hash']
    _channel_id = channel_id if channel_id else config['channel_id']

    with get_db() as conn:
        duplicates = find_duplicates(conn)

        if not duplicates:
            print("[CLEAN] 未检测到重复媒体")
            return

        total_deleted = 0
        total_failed = 0

        print(f"[CLEAN] 检测到 {len(duplicates)} 组重复媒体:")
        client = None
        if delete:
            client = get_client("tg-mgr")
            client.start()

        try:
            for i, (file_size, media_type, keep_id, delete_ids) in enumerate(duplicates, 1):
                print(f"\n重复组 #{i} (共 {len(delete_ids)+1} 条消息, {media_type}, {file_size} bytes):")
                print(f"  - 保留: {generate_tg_link(_channel_id, keep_id)}")

                for msg_id in delete_ids:
                    status = "删除" if delete else "标记删除"
                    print(f"  - {status}: {generate_tg_link(_channel_id, msg_id)}")

                    if delete:
                        success = delete_message_safely(client, conn, msg_id, _channel_id)
                        if success:
                            total_deleted += 1
                        else:
                            total_failed += 1

            if delete:
                conn.commit()
                print(f"\n[CLEAN] 去重完成 - 共处理 {len(duplicates)} 组重复消息, 成功删除 {total_deleted} 条, 失败 {total_failed} 条")
            else:
                print("\n[CLEAN] 检测完成")

        finally:
            if client and client.is_connected:
                client.stop()

def find_invalid_messages(conn: sqlite3.Connection) -> list:
    """查找所有无效消息（is_valid = 0）"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, timestamp
        FROM messages
        WHERE is_valid = 0
    """)

    return cursor.fetchall()

def run_deinvalid(delete: bool = False, channel_id: Optional[str] = None) -> None:
    """无效消息检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取
    """
    config = get_config()
    _api_id = config['api_id']
    _api_hash = config['api_hash']
    _channel_id = channel_id if channel_id else config['channel_id']

    with get_db() as conn:
        invalid_messages = find_invalid_messages(conn)

        if not invalid_messages:
            print("[CLEAN] 未检测到无效消息")
            return

        total_deleted = 0
        total_failed = 0

        print(f"[CLEAN] 检测到 {len(invalid_messages)} 条无效消息:")
        client = None
        if delete:
            client = get_client("tg-mgr")
            client.start()

        try:
            for idx, msg in enumerate(invalid_messages, 1):
                msg_id, file_unique_id, file_size, media_type, timestamp = msg
                tg_link = generate_tg_link(_channel_id, msg_id)

                print(f"\n消息 #{idx} (ID: {msg_id}, {media_type}, {file_size} bytes):")
                print(f"  - 链接: {tg_link}")
                print(f"  - 时间: {timestamp}")

                if delete:
                    success = delete_message_safely(client, conn, msg_id, _channel_id)
                    if success:
                        total_deleted += 1
                    else:
                        total_failed += 1

            if delete:
                conn.commit()
                print(f"\n[CLEAN] 清理完成 - 共处理 {len(invalid_messages)} 条无效消息, 成功删除 {total_deleted} 条, 失败 {total_failed} 条")
            else:
                print("\n[CLEAN] 检测完成")

        finally:
            if client and client.is_connected:
                client.stop()

def main():
    """主执行流程"""
    parser = argparse.ArgumentParser(description='Telegram 清理工具')
    parser.add_argument('-d', '--deduplicate', action='store_true', help='启用去重模式')
    parser.add_argument('-i', '--deinvalid', action='store_true', help='启用清理无效消息模式')
    parser.add_argument('-u', '--sync', action='store_true', help='强制同步消息')
    parser.add_argument('-f', '--force-reset', action='store_true', help='强制重置数据库')
    args = parser.parse_args()

    # 从配置文件读取频道ID
    config = get_config()
    channel_id = config.get('channel_id')

    # 判断是否需要执行sync
    should_sync = not args.deduplicate and not args.deinvalid or args.sync or any('u' in arg for arg in sys.argv[1:])

    # 只有在同步或强制重置时才清空数据库
    if should_sync or args.force_reset:
        db_path = get_database_path()
        if db_path.exists():
            db_path.unlink()
        run_sync(channel_id=channel_id)

    # 根据参数执行操作
    if args.deduplicate or args.deinvalid:
        if args.deduplicate:
            run_deduplicate(delete=True, channel_id=channel_id)
        if args.deinvalid:
            run_deinvalid(delete=True, channel_id=channel_id)
    else:
        # 未指定清理参数时，只检测不删除
        print("\n未指定清理参数，仅执行检测:")
        run_deduplicate(delete=False, channel_id=channel_id)
        run_deinvalid(delete=False, channel_id=channel_id)

if __name__ == "__main__":
    main()
