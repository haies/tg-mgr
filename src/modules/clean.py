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
import re
import signal
import sqlite3
import sys
import time

from pyrogram import Client, errors, types

from database import get_database_path, get_db, get_schema_path
from utils.media import extract_media_info, extract_reaction_data, extract_source_id
from utils.telegram_client import get_client, get_config
from utils.telegram_link import generate_tg_link

# Telegram 消息链接正则 (t.me/c/123/456 或 t.me/username/123)
TG_MSG_LINK_PATTERN = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me|telegramdog\.com)/'
    r'(?:c/\d+/\d+|[\w_]+/\d+)',
    re.IGNORECASE
)

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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)

    # 创建索引以优化查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_unique_id ON messages(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON messages(media_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_valid ON messages(is_valid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_duplicate ON messages(is_duplicate)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")

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
        media_obj = message.video or message.photo or message.document or message.animation
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

        # 检查是否为纯文字消息（无媒体）
        is_text_message = media_info.media_type == "text" and not media_info.file_unique_id

        # Skip media messages without valid file_unique_id
        if not is_text_message and (not media_info.file_unique_id or media_info.file_unique_id == ""):
            skipped += 1
            continue

        # 获取媒体组ID
        media_group_id = getattr(message, "media_group_id", None) or ""

        # Check for duplicates (skip for text messages - they don't have file_unique_id)
        if not is_text_message and media_info.file_unique_id in seen_files:
            duplicates.append(
                (message.id, media_info.file_unique_id, media_info.file_size, media_info.media_type)
            )
        else:
            # 使用共享函数提取反应数据
            reaction = extract_reaction_data(message)

            # Check message validity
            is_valid = 0 if check_restricted(message) else 1

            # 使用共享函数提取源频道 ID
            source_id = extract_source_id(message)

            # 提取消息文本
            caption = message.caption or message.text or ""

            # 对于文字消息，使用空字符串作为 file_unique_id
            file_unique_id = media_info.file_unique_id if media_info.file_unique_id else ""

            new_files.append(
                (
                    message.id,
                    file_unique_id,
                    media_info.file_size,
                    media_info.media_type,
                    caption,
                    0,
                    is_valid,
                    json.dumps({"positive": reaction.positive, "heart": reaction.heart}),
                    source_id,
                    media_group_id,
                )
            )
            seen_files.add(media_info.file_unique_id)

    # Process duplicates in bulk
    if duplicates:
        for duplicate in duplicates:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, is_duplicate) "
                    "VALUES (?, ?, ?, ?, 1)",
                    duplicate,
                )
            except sqlite3.IntegrityError:
                print(f"[SYNC] 消息 #{duplicate[0]} 已存在，跳过插入")
                continue

    # Process new messages with bulk insert
    if new_files:
        try:
            cursor.executemany(
                "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id, media_group_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                new_files,
            )
        except sqlite3.IntegrityError:
            for new_file in new_files:
                try:
                    cursor.execute(
                        "INSERT OR IGNORE INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id, media_group_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        new_file,
                    )
                except sqlite3.IntegrityError:
                    print(f"[SYNC] 消息 #{new_file[0]} 已存在，跳过插入")

    conn.commit()
    return skipped


def run_sync(channel_id: str | None = None) -> None:
    """主同步流程

    Args:
        channel_id: 频道ID，如果为None则从配置文件读取
    """
    config = get_config()
    _api_id = config["api_id"]
    _api_hash = config["api_hash"]
    _channel_id = channel_id if channel_id else config["channel_id"]

    # 验证 channel_id 格式
    if not _channel_id:
        raise ValueError("channel_id is required but not provided")
    _channel_id_str = str(_channel_id)
    if not (_channel_id_str.startswith("-100") or _channel_id_str.lstrip("-").isdigit()):
        raise ValueError(f"Invalid channel_id format: {_channel_id}")

    conn = init_database()
    try:
        last_processed_id = get_last_processed_id(conn)

        # 信号处理：优雅关闭
        shutdown_requested = False
        def signal_handler(signum, frame):
            nonlocal shutdown_requested
            print("\n[SIGNAL] 收到中断信号，正在优雅关闭...")
            shutdown_requested = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

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

            while has_more and not shutdown_requested:
                batch_messages = []
                try:
                    for message in client.get_chat_history(
                        _channel_id, offset_id=offset_id, limit=batch_size
                    ):
                        if shutdown_requested:
                            break
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

            end_time = time.time()
            duration = end_time - start_time
            print(f"[SYNC] 同步完成，耗时: {duration:.2f} 秒")
    finally:
        conn.close()


def exponential_backoff(retry_count: int, retry_delay_base: int) -> float:
    """计算等待时间：1s, 2s, 4s, 8s, 16s..."""
    return retry_delay_base * (2**retry_count) + (retry_count * 0.1)


def delete_message_safely(
    client: Client, conn: sqlite3.Connection, message_id: int, channel_id: str, retry_count: int = 0
) -> bool:
    """安全删除消息（带重试机制）"""
    config = get_config()
    max_retries = config["max_retries"]
    retry_delay_base = config["retry_delay_base"]

    while retry_count < max_retries:
        try:
            client.delete_messages(channel_id, message_id)
            print(f"    [CLEAN] 已从Telegram删除消息 #{message_id}")

            # 更新数据库中的 is_duplicate 标志
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET is_duplicate = 1 WHERE message_id = ?", (message_id,))
            return True
        except errors.FloodWait as e:
            wait_time = max(e.value, 5)
            print(f"    [WARNING] FloodWait: 等待 {wait_time} 秒后重试...")
            time.sleep(wait_time)
            retry_count += 1
        except Exception as e:
            wait_time = exponential_backoff(retry_count, retry_delay_base)
            print(
                f"    [WARNING] 删除消息 #{message_id} 失败: {e} - {retry_count + 1}/{max_retries} 次重试 (等待 {wait_time:.1f} 秒)"
            )
            time.sleep(wait_time)
            retry_count += 1

    print(f"    [ERROR] 删除消息 #{message_id} 失败，已达到最大重试次数")
    return False


# 文件大小阈值：小于2MB视为小文件（垃圾消息判定用）
JUNK_MEDIA_SIZE_THRESHOLD = 2 * 1024 * 1024  # 2MB


def count_chinese_chars(text: str) -> int:
    """统计字符串中中文字符的数量"""
    return sum(1 for char in text if '一' <= char <= '鿿')


def is_junk_message(text: str, file_size: int | None = None, media_type: str | None = None) -> bool:
    """判断消息是否为垃圾消息

    判定规则（同时满足）：
    - 媒体类型为 photo 或 video
    - 30字以上中文 或 100字符以上英文
    - 文件大小小于 2MB
    """
    if not text:
        return False

    # 必须是有具体文字内容
    stripped = text.strip()
    links_only = TG_MSG_LINK_PATTERN.sub('', stripped).strip()
    if links_only == '' and TG_MSG_LINK_PATTERN.search(stripped):
        return False

    # 规则：长文字
    chinese_count = count_chinese_chars(text)
    if not (chinese_count >= 30 or len(text) >= 100):
        return False

    # 规则：媒体类型必须为 photo 或 video
    if media_type not in ('photo', 'video'):
        return False

    # 规则：文件大小必须小于 2MB
    if file_size is not None and file_size >= JUNK_MEDIA_SIZE_THRESHOLD:
        return False

    return True


def is_spam_text(text: str) -> bool:
    """检测纯文字消息是否为垃圾

    判定规则：
    - 所有纯文字消息都算垃圾（不论长短）
    - 排除仅包含 Telegram 消息链接的文字
    """
    if not text:
        return False

    # 排除仅包含链接的文字
    stripped = text.strip()
    links_only = TG_MSG_LINK_PATTERN.sub('', stripped).strip()
    if links_only == '' and TG_MSG_LINK_PATTERN.search(stripped):
        return False

    # 所有非空的纯文字消息都算垃圾
    return True


def find_junk_messages(conn: sqlite3.Connection) -> list:
    """查找所有垃圾消息

    检测类型：
    1. 媒体消息（photo/video）+ 长文字 + 文件小于2MB + 非媒体组成员
    2. 纯文字消息 + 推广/引流关键词
    """
    cursor = conn.cursor()

    # 类型1：photo/video 媒体消息 + 长文字（需要同时满足文字长度、文件大小条件，且不在媒体组中）
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, caption, timestamp
        FROM messages
        WHERE media_type IN ('photo', 'video')
        AND caption IS NOT NULL AND caption != ''
        AND (media_group_id IS NULL OR media_group_id = '')
    """)
    media_junk = [msg for msg in cursor.fetchall() if is_junk_message(msg[4], msg[2], msg[3])]

    # 类型2：纯文字消息 + 推广/引流关键词
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, caption, timestamp
        FROM messages
        WHERE (media_type IS NULL OR media_type = 'text' OR media_type = '')
        AND caption IS NOT NULL AND caption != ''
    """)
    text_junk = [msg for msg in cursor.fetchall() if is_spam_text(msg[4])]

    return media_junk + text_junk


def find_duplicates(conn: sqlite3.Connection) -> list:
    """查找所有重复媒体组（基于文件唯一ID）

    使用窗口函数在单次查询中获取所有待删除消息ID，避免 N+1 问题
    """
    cursor = conn.cursor()

    # 使用窗口函数一次性获取所有重复消息
    # ROW_NUMBER() 按 file_unique_id 分组，按 timestamp 排序
    # rn = 1 表示保留的消息，rn > 1 表示待删除
    cursor.execute("""
        WITH RankedMessages AS (
            SELECT
                message_id,
                file_unique_id,
                file_size,
                media_type,
                timestamp,
                ROW_NUMBER() OVER (
                    PARTITION BY file_unique_id
                    ORDER BY timestamp ASC
                ) as rn
            FROM messages
            WHERE file_unique_id IS NOT NULL AND file_unique_id != ''
        )
        SELECT
            file_unique_id,
            MAX(file_size) as file_size,
            MIN(media_type) as media_type,
            MAX(CASE WHEN rn = 1 THEN message_id END) as keep_id,
            GROUP_CONCAT(CASE WHEN rn > 1 THEN message_id END) as delete_ids
        FROM RankedMessages
        WHERE rn > 1 OR (
            (SELECT COUNT(*) FROM messages m2 WHERE m2.file_unique_id = RankedMessages.file_unique_id) > 1
            AND rn = 1
        )
        GROUP BY file_unique_id
        HAVING COUNT(*) > 0 AND delete_ids IS NOT NULL
    """)

    duplicates = []
    for row in cursor.fetchall():
        file_unique_id, file_size, media_type, keep_id, delete_ids_str = row
        if delete_ids_str:
            delete_ids = [int(x) for x in delete_ids_str.split(',')]
            duplicates.append((file_size, media_type, keep_id, delete_ids))

    return duplicates


def run_deduplicate(delete: bool = False, channel_id: str | None = None) -> dict[str, int]:
    """重复检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取

    Returns:
        按media_type统计的待清理消息数量 dict
    """
    config = get_config()
    _api_id = config["api_id"]
    _api_hash = config["api_hash"]
    _channel_id = channel_id if channel_id else config["channel_id"]

    stats: dict[str, int] = {}

    with get_db() as conn:
        duplicates = find_duplicates(conn)

        if not duplicates:
            print("[CLEAN] 未检测到重复媒体")
            return stats

        total_deleted = 0
        total_failed = 0

        # 统计每种media_type的待删除数量
        for _, media_type, _, delete_ids in duplicates:
            key = media_type or "unknown"
            stats[key] = stats.get(key, 0) + len(delete_ids)

        print(f"[CLEAN] 检测到 {len(duplicates)} 组重复媒体:")
        client = None
        if delete:
            client = get_client("tg-mgr")
            client.start()

        try:
            for i, (file_size, media_type, keep_id, delete_ids) in enumerate(duplicates, 1):
                print(
                    f"\n重复组 #{i} (共 {len(delete_ids) + 1} 条消息, {media_type}, {file_size} bytes):"
                )
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
                print(
                    f"\n[CLEAN] 去重完成 - 共处理 {len(duplicates)} 组重复消息, 成功删除 {total_deleted} 条, 失败 {total_failed} 条"
                )
            else:
                print("\n[CLEAN] 检测完成")

        finally:
            if client and client.is_connected:
                client.stop()

        return stats


def find_invalid_messages(conn: sqlite3.Connection) -> list:
    """查找所有无效消息（is_valid = 0）"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_id, file_unique_id, file_size, media_type, timestamp
        FROM messages
        WHERE is_valid = 0
    """)

    return cursor.fetchall()


def run_deinvalid(delete: bool = False, channel_id: str | None = None) -> dict[str, int]:
    """无效消息检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取

    Returns:
        按media_type统计的待清理消息数量 dict
    """
    config = get_config()
    _api_id = config["api_id"]
    _api_hash = config["api_hash"]
    _channel_id = channel_id if channel_id else config["channel_id"]

    stats: dict[str, int] = {}

    with get_db() as conn:
        invalid_messages = find_invalid_messages(conn)

        if not invalid_messages:
            print("[CLEAN] 未检测到无效消息")
            return stats

        # 按media_type统计
        for msg in invalid_messages:
            media_type = msg[3] or "unknown"
            stats[media_type] = stats.get(media_type, 0) + 1

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
                print(
                    f"\n[CLEAN] 清理完成 - 共处理 {len(invalid_messages)} 条无效消息, 成功删除 {total_deleted} 条, 失败 {total_failed} 条"
                )
            else:
                print("\n[CLEAN] 检测完成")

        finally:
            if client and client.is_connected:
                client.stop()

        return stats


def run_dejunk(delete: bool = False, channel_id: str | None = None) -> dict[str, int]:
    """垃圾消息检测与清理流程

    Args:
        delete: 是否实际删除消息
        channel_id: 频道ID，如果为None则从配置文件读取

    Returns:
        按media_type统计的待清理消息数量 dict
    """
    config = get_config()
    _channel_id = channel_id if channel_id else config["channel_id"]

    stats: dict[str, int] = {}

    with get_db() as conn:
        junk_messages = find_junk_messages(conn)

        if not junk_messages:
            print("[CLEAN] 未检测到垃圾消息")
            return stats

        # 按media_type统计
        for msg in junk_messages:
            media_type = msg[3] or "text"
            stats[media_type] = stats.get(media_type, 0) + 1

        total_deleted = 0
        total_failed = 0

        print(f"[CLEAN] 检测到 {len(junk_messages)} 条垃圾消息:")
        client = None
        if delete:
            client = get_client("tg-mgr")
            client.start()

        try:
            for idx, msg in enumerate(junk_messages, 1):
                msg_id, file_unique_id, file_size, media_type, caption, timestamp = msg
                tg_link = generate_tg_link(_channel_id, msg_id)
                size_kb = file_size // 1024 if file_size else 0
                text_len = len(caption) if caption else 0

                print(f"  [{idx}] {tg_link} | {media_type} {size_kb}KB | 文字 {text_len} 字")

                if delete:
                    success = delete_message_safely(client, conn, msg_id, _channel_id)
                    if success:
                        total_deleted += 1
                    else:
                        total_failed += 1

            if delete:
                conn.commit()
                print(f"[CLEAN] 清理完成，已删除 {total_deleted} 条")
            else:
                pass  # 检测完成，不显示多余信息

        finally:
            if client and client.is_connected:
                client.stop()

        return stats


def print_cleanup_stats(stats_by_type: dict[str, dict[str, int]], dry_run: bool) -> None:
    """打印清理统计信息

    Args:
        stats_by_type: 按类型统计的结果 {"deduplicate": {...}, "deinvalid": {...}, "dejunk": {...}}
        dry_run: 是否为预览模式（-y）
    """
    if not stats_by_type:
        return

    print(f"\n{'='*50}")
    print("[CLEAN] 待清理消息分类统计:")
    print(f"{'='*50}")

    total_all = 0

    type_labels = {
        "deduplicate": "重复媒体",
        "deinvalid": "无效消息",
        "dejunk": "垃圾消息",
    }

    for op_type, stats in stats_by_type.items():
        if not stats:
            continue
        label = type_labels.get(op_type, op_type)
        type_total = sum(stats.values())
        total_all += type_total
        print(f"\n  {label}:")
        for media_type, count in sorted(stats.items()):
            print(f"    {media_type}: {count} 条")
        print(f"    小计: {type_total} 条")

    print(f"\n  总计: {total_all} 条")
    if dry_run:
        print("\n  (以上为预览模式，实际删除请去掉 -y 参数)")


def main():
    """主执行流程"""
    parser = argparse.ArgumentParser(description="Telegram 清理工具")
    parser.add_argument("-d", action="store_true", help="去重（检测并删除重复媒体消息）")
    parser.add_argument("-u", action="store_true", help="强制同步消息到数据库（断点续传）")
    parser.add_argument("-i", action="store_true", help="清理无效消息（受限制无法显示的消息）")
    parser.add_argument("-s", action="store_true", help="清理垃圾消息（长文字媒体或推广引流纯文字）")
    parser.add_argument("-y", action="store_true", help="仅列出待删除消息，不实际删除")
    parser.add_argument("-f", action="store_true", help="强制重置数据库（清空后重新同步）")
    parser.add_argument("channels", nargs="*", default=None, help="指定要清理的频道ID（可选）")
    args = parser.parse_args()

    # 确定要处理的频道列表
    if args.channels:
        channels = args.channels
    else:
        config = get_config()
        channel_id = config.get("channel_id")
        channels = [channel_id] if channel_id else []

    if not channels:
        print("[CLEAN] 错误：未指定频道ID，且配置文件中也未设置频道ID")
        return

    # 判断是否需要执行sync（无清理参数时默认同步，或显式指定-u）
    should_sync = (
        not args.d
        and not args.i
        and not args.s
        or args.u
        or any("u" in arg for arg in sys.argv[1:])
    )

    # dry_run 模式（-y）影响所有删除操作
    dry_run = args.y

    # 收集所有清理类型的统计
    stats_by_type: dict[str, dict[str, int]] = {}

    # 对每个频道分别执行操作
    for channel in channels:
        print(f"\n{'='*50}")
        print(f"[CLEAN] 开始清理频道: {channel}")
        print(f"{'='*50}")

        # 只有在同步或强制重置时才清空数据库
        if should_sync or args.f:
            db_path = get_database_path()
            if db_path.exists():
                db_path.unlink()
            run_sync(channel_id=channel)

        # 执行各项清理操作
        if args.d:
            stats = run_deduplicate(delete=not dry_run, channel_id=channel)
            if stats:
                stats_by_type["deduplicate"] = stats
        if args.i:
            stats = run_deinvalid(delete=not dry_run, channel_id=channel)
            if stats:
                stats_by_type["deinvalid"] = stats
        if args.s:
            stats = run_dejunk(delete=not dry_run, channel_id=channel)
            if stats:
                stats_by_type["dejunk"] = stats

        # 无清理参数时，只检测不删除
        if not args.d and not args.i and not args.s:
            dedup_stats = run_deduplicate(delete=False, channel_id=channel)
            invalid_stats = run_deinvalid(delete=False, channel_id=channel)
            junk_stats = run_dejunk(delete=False, channel_id=channel)

            # 汇总统计
            if dedup_stats:
                stats_by_type["deduplicate"] = dedup_stats
            if invalid_stats:
                stats_by_type["deinvalid"] = invalid_stats
            if junk_stats:
                stats_by_type["dejunk"] = junk_stats

    # -y 模式下打印汇总统计
    if dry_run:
        print_cleanup_stats(stats_by_type, dry_run)


if __name__ == "__main__":
    main()
