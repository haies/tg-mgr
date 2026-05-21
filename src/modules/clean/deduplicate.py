"""去重逻辑"""
import sqlite3

from pyrogram import Client, errors

from database import get_db, find_duplicates
from utils.telegram_client import get_client, get_config
from utils.telegram_link import generate_tg_link


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

    import time

    while retry_count < max_retries:
        try:
            client.delete_messages(channel_id, message_id)  # type: ignore[unused-coroutine]
            print(f"    [CLEAN] 已从Telegram删除消息 #{message_id}")

            # 更新数据库中的 is_duplicate 标志
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET is_duplicate = 1 WHERE message_id = ?", (message_id,))
            return True
        except errors.FloodWait as e:
            wait_time = float(max(e.value, 5))
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


def run_deduplicate(delete: bool = False, channel_id: str | None = None) -> dict[str, int]:
    """重复检测与清理流程

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
            client.start()  # type: ignore[unused-coroutine]

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
                        assert client is not None
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
                client.stop()  # type: ignore[unused-coroutine]

        return stats