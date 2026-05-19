"""
高反应消息复制模块

功能：
- 找出频道中高反应消息（次数 > 0 或 浏览量 top 10）
- 自动将符合条件的消息复制到目标频道
- 支持递归深度转发（-r 参数）

使用：
- tg forward <源频道ID或链接> [-o <目标频道ID>] [-c] [-r <深度>]

递归深度规则：
- 参数中的频道 = 第 1 层
- 第 1 层频道中高反应消息的来源频道 = 第 2 层
- 依此类推...
"""

import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from database import get_database_dir, get_database_path, get_db_connection
from pyrogram import Client, errors
from pyrogram.types import (
    InputMedia,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from database.query import (
    find_reaction_messages_for_display,
    get_forward_sources,
)
from utils.telegram_client import get_client, get_config, get_log_path
from utils.telegram_link import get_channel_address

logger = logging.getLogger(__name__)

# 默认递归深度
DEFAULT_RECURSION_DEPTH = 5

# 媒体组搜索偏移量上限
MAX_MEDIA_GROUP_SEARCH_OFFSET = 20


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


def _get_reaction_total(message: Message) -> int:
    """从消息中提取反应总数"""
    total = 0
    if message.reactions:
        for reaction in message.reactions:
            total += reaction.count
    return total


def summarize_messages_for_forward(
    conn: sqlite3.Connection,
    messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """统计待转发消息的累计大小和条数

    Returns:
        {
            "total_count": int,
            "media_count": int,
            "total_size_mb": float,
        }
    """
    if not messages:
        return {"total_count": 0, "media_count": 0, "total_size_mb": 0.0}

    msg_ids = [m["message_id"] for m in messages]

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(msg_ids))
    cursor.execute(
        f"""
        SELECT
            COUNT(*) as total_count,
            SUM(CASE WHEN file_size > 0 THEN 1 ELSE 0 END) as media_count,
            COALESCE(SUM(file_size), 0) as total_size
        FROM messages
        WHERE message_id IN ({placeholders})
        """,
        msg_ids,
    )
    row = cursor.fetchone()

    total_count = row[0] if row else 0
    media_count = row[1] if row else 0
    total_size_bytes = row[2] if row else 0

    return {
        "total_count": total_count,
        "media_count": media_count,
        "total_size_mb": total_size_bytes / 1024 / 1024,
    }


def confirm_forward(
    messages: list[dict[str, Any]],
    summary: dict[str, Any]
) -> bool:
    """显示统计摘要并询问确认

    Args:
        messages: 消息列表
        summary: 统计结果 from summarize_messages_for_forward

    Returns:
        True if user confirms with 'y', False otherwise
    """
    if not messages:
        return False

    total_count = summary.get("total_count", 0)
    media_count = summary.get("media_count", 0)
    total_size_mb = summary.get("total_size_mb", 0.0)

    # 大小分级提示
    if total_size_mb < 10:
        size_level = "小（<10MB）"
    elif total_size_mb < 100:
        size_level = "中等（10MB-100MB）"
    elif total_size_mb < 500:
        size_level = "较大（100MB-500MB）"
    else:
        size_level = "大（>500MB）"

    print(f"[FORWARD] 待转发消息统计：")
    print(f"  - 消息条数：{total_count} 条")
    print(f"  - 有媒体：{media_count} 条")
    print(f"  - 媒体累计大小：{total_size_mb:.1f} MB")
    print(f"  - 预估大小级别：{size_level}")

    # 显示消息列表预览
    print(f"\n[FORWARD] 待转发消息列表（共 {len(messages)} 条）：")
    for i, msg in enumerate(messages[:20]):  # 限制显示20条
        link = f"https://t.me/c/{abs(msg.get('source_id', 0))}/{msg['message_id']}"
        total = msg.get("total", 0)
        views = msg.get("views", 0)
        file_size = msg.get("file_size", 0)
        size_mb = file_size / 1024 / 1024 if file_size else 0
        is_media_group = msg.get("is_media_group", False)

        group_suffix = " (媒体组)" if is_media_group else ""
        stats = _build_stats_str(total, views)
        size_str = f"{size_mb:.1f}MB" if size_mb > 0 else "无媒体"
        print(f"  {i+1}. {link} | {size_str}{group_suffix}{stats}")

    if len(messages) > 20:
        print(f"  ... 还有 {len(messages) - 20} 条消息")

    print()

    try:
        response = input(f"媒体累计大小 {total_size_mb:.1f}MB，是否继续转发？[y/N] ").strip().lower()
        return response == "y"
    except (EOFError, KeyboardInterrupt):
        print("\n[FORWARD] 已取消")
        return False


def parse_source_arg(arg: str) -> tuple[int | None, int | None, str | None]:
    """解析参数为 (channel_id, message_id, username)

    Args:
        arg: 频道ID或链接

    Returns:
        (channel_id, message_id, username) - message_id 为 None 表示整个频道
        username 用于延迟解析（如 jn2678 -> -1001234567890）
    """
    # 标准化：补全缺失的 https:// 前缀
    if arg.startswith("t.me/") and not arg.startswith("https://"):
        arg = "https://" + arg

    # 解析链接：https://t.me/c/1234567890/12345 格式（超级群/私有频道）
    c_pattern = r"https?://t\.me/c/(\d+)/(\d+)(?:\?.*)?$"
    c_match = re.match(c_pattern, arg)
    if c_match:
        raw_id = int(c_match.group(1))
        message_id = int(c_match.group(2))
        # t.me/c/xxxx/ 中的 xxxx 是超级群 ID，需要加 -100 前缀
        channel_id = -(raw_id + 1000000000000) if raw_id < 100000000000 else -raw_id
        return (channel_id, message_id, None)

    # 解析链接：https://t.me/username/123 格式
    link_pattern = r"https?://t\.me/([\w_]+)/(\d+)(?:\?.*)?$"
    match = re.match(link_pattern, arg)
    if match:
        identifier = match.group(1)
        message_id = int(match.group(2))
        # 用户名格式，需要延迟解析（调用 API）
        return (None, message_id, identifier)

    # 纯数字频道ID
    try:
        return (int(arg), None, None)
    except ValueError:
        print(f"[ERROR] 无法解析参数: {arg}")
        return (None, None, None)


def resolve_username_to_channel_id(client: Client, username: str) -> int | None:
    """将用户名解析为频道ID

    Args:
        client: Telegram 客户端
        username: 用户名（可以是 'jn2678' 或 '@jn2678'）

    Returns:
        频道ID，解析失败返回 None
    """
    try:
        # 确保 username 有 @ 前缀
        if not username.startswith('@'):
            username = f"@{username}"
        chat = client.get_chat(username)  # type: ignore[attr-defined]
        return chat.id  # type: ignore[attr-defined]
    except Exception as e:
        print(f"[ERROR] 无法解析用户名 {username}: {e}")
        return None


def find_messages_to_forward(
    conn: sqlite3.Connection,
    channel_id: int,
    reaction_limit: int = 10,
    filter_by_source: bool = False,
) -> list[dict[str, Any]]:
    """查找要转发的消息（高反应 + 高浏览量 TOP，与 info 保持一致）

    查询逻辑与 info.py 的 analyze_channel 保持一致：
    1. 高反应消息 TOP（使用 find_reaction_messages_for_display）
    2. 高浏览量消息 TOP（views > 8x avg，使用 find_messages_by_views_top）

    Args:
        conn: 数据库连接
        channel_id: 频道ID（仅当 filter_by_source=True 时作为 source_id 过滤条件）
        reaction_limit: 高反应/高浏览量消息数量限制
        filter_by_source: 是否按 source_id 过滤（第1层为False，递归层为True）

    Returns:
        消息列表，每条消息包含 message_id, positive, heart, total, views, source_id, media_type
    """
    from database.query import find_messages_by_views_top

    # source_id 过滤：仅递归层启用（第1层查所有消息）
    source_id_for_query = channel_id if filter_by_source else None

    # 1. 高反应消息
    reaction_results = find_reaction_messages_for_display(
        conn, reaction_limit=reaction_limit, source_id=source_id_for_query
    )

    # 2. 高浏览量消息（views > 8x avg）
    view_rows = find_messages_by_views_top(conn, limit=reaction_limit, source_id=source_id_for_query)
    view_results = [
        {
            "message_id": row[0],
            "views": row[1],
            "source_id": row[2],
            "media_type": row[3],
        }
        for row in view_rows
    ]

    # 合并两类消息（按 message_id 去重，reaction 优先）
    # 注意：reaction 结果可能缺少 views 字段，需要从 view 结果补充
    seen_ids = set()
    merged = []
    for msg in reaction_results:
        seen_ids.add(msg["message_id"])
        merged.append(msg)
    for msg in view_results:
        if msg["message_id"] not in seen_ids:
            seen_ids.add(msg["message_id"])
            merged.append(msg)

    # 补充 reaction 消息的 views 字段（从 view_results 中获取）
    view_map = {row[0]: row[1] for row in view_rows}
    for msg in merged:
        if msg.get("total", 0) > 0 and "views" not in msg:
            msg["views"] = view_map.get(msg["message_id"], 0)

    return merged


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


def get_channel_temp_db_path(channel_id: int) -> Path:
    """获取频道专属的临时数据库路径（不会被后续同步覆盖）"""
    db_dir = get_database_dir()
    return db_dir / f"messages_{abs(channel_id)}.tmp.db"


def cleanup_channel_temp_dbs(channel_ids: list[int]) -> None:
    """清理频道专属临时数据库"""
    for ch_id in channel_ids:
        temp_path = get_channel_temp_db_path(ch_id)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def sync_channel_for_forward(channel_id: int, temp_db_path: Path | None = None) -> None:
    """为转发同步频道数据（使用临时数据库）

    Args:
        channel_id: 频道ID
        temp_db_path: 可选的临时数据库路径（用于per-channel模式）
    """
    from modules.sync import sync_channel

    if temp_db_path is None:
        temp_db_path = get_channel_temp_db_path(channel_id)

    try:
        sync_channel(channel_id=str(channel_id), db_path=str(temp_db_path))
    finally:
        pass  # sync_channel handles env cleanup internally

    # 注意：不移动到主数据库，保持temp DB独立直到cleanup


def forward_single_message(
    client: Client,
    source_channel_id: int,
    target_channel_id: int,
    message_id: int,
    force: bool = False,
) -> bool:
    """转发单条消息

    Args:
        client: Telegram 客户端
        source_channel_id: 源频道ID
        target_channel_id: 目标频道ID
        message_id: 消息ID
        force: 是否强制转发（忽略限制）

    Returns:
        True if successful, False otherwise
    """
    try:
        # 检查是否是媒体组的一部分
        group_msg = _get_original_media_group_message(client, source_channel_id, message_id)
        if group_msg and group_msg.media_group_id:
            # 媒体组：使用 send_media_group 转发
            media_group_messages = _get_media_group_messages(
                client, source_channel_id, group_msg.media_group_id, message_id
            )
            return _forward_media_group(client, source_channel_id, target_channel_id, media_group_messages, force=force)
        else:
            # 普通消息：直接复制
            client.copy_message(  # type: ignore[unused-coroutine]
                chat_id=target_channel_id,
                from_chat_id=source_channel_id,
                message_id=message_id,
            )
        return True
    except errors.FloodWait as e:
        wait = max(e.value, 5)
        time.sleep(wait)
        return False
    except (errors.Forbidden, errors.BadRequest):
        return False
    except Exception as e:
        logger.debug(f"转发消息失败: {e}")
        return False


def _get_original_media_group_message(
    client: Client, channel_id: int, message_id: int
) -> Message | None:
    """获取消息所在的媒体组原消息"""
    try:
        # 先调用 get_chat 建立会话，解决 CHAT_ID_INVALID 问题
        client.get_chat(channel_id)  # type: ignore[unused-coroutine]
        msgs = client.get_messages(channel_id, message_id)  # type: ignore[union-attr]
        return msgs  # type: ignore[return-value]
    except Exception:
        return None


def _get_media_group_messages(
    client: Client, channel_id: int, media_group_id: str, center_msg_id: int | None = None
) -> list[Message]:
    """获取媒体组的所有消息（双向搜索优化版）

    Args:
        client: Telegram 客户端
        channel_id: 频道ID
        media_group_id: 媒体组ID
        center_msg_id: 中心消息ID，用于双向搜索

    Returns:
        媒体组消息列表（按 message_id 排序）
    """
    try:
        # 先调用 get_chat 建立会话
        client.get_chat(channel_id)  # type: ignore[unused-coroutine]

        messages = []
        seen_ids = set()

        # 如果有 center_msg_id，先获取它作为起点
        if center_msg_id:
            try:
                center_msg = client.get_messages(channel_id, center_msg_id)  # type: ignore[union-attr]
                if center_msg and str(center_msg.media_group_id) == str(media_group_id):  # type: ignore[attr-defined]
                    messages.append(center_msg)
                    seen_ids.add(center_msg.id)  # type: ignore[attr-defined]
            except Exception:
                pass

        # 双向搜索：
        # - 向后搜索（offset_id=center_msg_id）：获取 id < center_msg_id 的消息
        # - 向前搜索（offset_id=0）：获取 id > center_msg_id 的消息

        if center_msg_id:
            # 向后搜索：offset_id=center_msg_id 返回 <= center_msg_id 的消息
            # 由于历史消息是 id 越低越旧，我们需要找 id < center_msg_id 的
            found_any = False
            for msg in client.get_chat_history(channel_id, limit=200, offset_id=center_msg_id):  # type: ignore[union-attr]
                if msg.media_group_id == media_group_id and msg.id not in seen_ids:  # type: ignore[attr-defined]
                    messages.append(msg)
                    seen_ids.add(msg.id)  # type: ignore[attr-defined]
                    found_any = True
                    if len(messages) >= 10:
                        break
                elif found_any and msg.media_group_id != media_group_id:
                    # 只有在找到过消息且遇到不同媒体组时才停止
                    break

            # 向前搜索：offset_id=0 返回最新消息，需要过滤 id > center_msg_id 的
            found_any = False
            for msg in client.get_chat_history(channel_id, limit=200, offset_id=0):  # type: ignore[union-attr]
                if msg.id > center_msg_id and msg.media_group_id == media_group_id and msg.id not in seen_ids:  # type: ignore[attr-defined]
                    messages.append(msg)
                    seen_ids.add(msg.id)  # type: ignore[attr-defined]
                    found_any = True
                    if len(messages) >= 10:
                        break
                elif found_any and msg.id > center_msg_id and msg.media_group_id != media_group_id:
                    # 只有在找到过消息且 id 已经在增长时，遇到不同媒体组才停止
                    break
        else:
            # 没有 center_msg_id，使用批量获取（可能不准）
            for msg in client.get_chat_history(channel_id, limit=200):  # type: ignore[union-attr]
                if msg.media_group_id == media_group_id and msg.id not in seen_ids:  # type: ignore[attr-defined]
                    messages.append(msg)
                    seen_ids.add(msg.id)  # type: ignore[attr-defined]
                    if len(messages) >= 10:
                        break

        # 按 message_id 排序
        messages.sort(key=lambda m: m.id)  # type: ignore[attr-defined]
        return messages  # type: ignore[return-value]
    except Exception:
        return []


def _forward_media_group(
    client: Client,
    source_channel_id: int,
    target_channel_id: int,
    messages: list[Message],
    force: bool = False,
) -> bool:
    """转发媒体组

    Args:
        client: Telegram 客户端
        source_channel_id: 源频道ID
        target_channel_id: 目标频道ID
        messages: 媒体组消息列表
        force: 是否强制转发（忽略限制）

    Returns:
        True if successful, False otherwise
    """
    if not messages:
        return False

    # 按 message_id 排序确保顺序正确
    messages = sorted(messages, key=lambda m: m.id)

    # 准备媒体组输入
    media_list = []
    for msg in messages:
        media_input = _prepare_media_for_send(msg)
        if media_input:
            media_list.append(media_input)

    if not media_list:
        return False

    try:
        client.send_media_group(target_channel_id, media_list)  # type: ignore[unused-coroutine, arg-type]
        return True
    except errors.FloodWait as e:
        wait = max(e.value, 5)
        time.sleep(wait)
        return False
    except Exception as e:
        logger.debug(f"媒体组转发失败: {e}")
        return False


def _prepare_media_for_send(message: Message) -> InputMedia | None:
    """准备消息用于 send_media_group，返回正确的 InputMedia 类型"""
    try:
        caption = message.caption or ""
        if message.photo:
            return InputMediaPhoto(message.photo.file_id, caption=caption)
        elif message.video:
            return InputMediaVideo(message.video.file_id, caption=caption)
        elif message.document:
            return InputMediaDocument(message.document.file_id, caption=caption)
        elif message.audio:
            return InputMediaAudio(message.audio.file_id, caption=caption)
        elif message.animation:
            return InputMediaAnimation(message.animation.file_id, caption=caption)
        elif message.voice:
            # voice 和 video_note 不支持媒体组，使用 InputMedia
            return InputMedia(message.voice.file_id, caption=caption)
        elif message.video_note:
            return InputMedia(message.video_note.file_id, caption=caption)
        elif message.text:
            # 纯文本消息不是媒体组的一部分
            return None
        return None
    except Exception as e:
        logger.debug(f"准备媒体发送失败: {e}")
        return None


def _get_download_dir() -> str:
    """获取下载目录，默认为 ~/.tg-mgr/downloads/"""
    import os

    config_dir = os.path.expanduser("~/.tg-mgr")
    download_dir = os.path.join(config_dir, "downloads")
    os.makedirs(download_dir, exist_ok=True)
    return download_dir


def _download_with_resume(client: Client, message: Message, target_path: str, max_retries: int = 3) -> str | None:
    """下载媒体文件，支持断点续传和重试

    Args:
        client: Telegram 客户端
        message: 源消息
        target_path: 目标文件路径
        max_retries: 最大重试次数

    Returns:
        下载完成的文件路径，失败返回 None
    """
    import os
    import time

    # 获取文件信息
    file_size = 0
    if hasattr(message, 'video') and message.video:
        file_size = message.video.file_size
    elif hasattr(message, 'document') and message.document:
        file_size = message.document.file_size
    elif hasattr(message, 'photo') and message.photo:
        file_size = getattr(message.photo, 'file_size', 0)
    elif hasattr(message, 'audio') and message.audio:
        file_size = message.audio.file_size
    elif hasattr(message, 'animation') and message.animation:
        file_size = message.animation.file_size

    print(f"[DOWNLOAD] 文件大小: {file_size / 1024 / 1024:.1f} MB")

    for attempt in range(max_retries):
        try:
            # 检查已下载的部分
            downloaded_size = 0
            if os.path.exists(target_path):
                downloaded_size = os.path.getsize(target_path)
                if downloaded_size >= file_size:
                    print(f"[DOWNLOAD] 文件已完整下载: {downloaded_size} bytes")
                    return target_path
                print(f"[DOWNLOAD] 断点续传: 已下载 {downloaded_size / 1024 / 1024:.1f} MB，继续...")

            # 使用 Pyrogram 的下载功能
            # 添加进度回调
            def progress(current, total):
                if total > 0:
                    pct = current / total * 100
                    if current % (1024 * 1024 * 10) == 0:  # 每10MB打印一次
                        print(f"[DOWNLOAD] 进度: {current / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB ({pct:.1f}%)")

            result_path = client.download_media(
                message,
                file_name=target_path,
                progress=progress
            )

            # 验证下载完整性
            if result_path and os.path.exists(result_path):  # type: ignore[arg-type, union-attr]
                final_size = os.path.getsize(result_path)  # type: ignore[arg-type, union-attr]
                if file_size > 0 and final_size > 0 and final_size < file_size * 0.95:  # 允许5%误差
                    print(f"[DOWNLOAD] 下载不完整: {final_size} / {file_size} bytes，重试...")
                    continue
                print(f"[DOWNLOAD] 下载完成: {final_size / 1024 / 1024:.1f} MB")
                return result_path  # type: ignore[return-value]

        except Exception as e:
            print(f"[DOWNLOAD] 下载失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                print(f"[DOWNLOAD] 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)

    return None


def _force_send_single_message(client: Client, target_channel_id: int, message: Message, download_dir: str | None = None) -> bool:
    """强制发送单条消息（绕过转发限制）

    使用下载-上传方式，支持断点续传和重试机制

    Args:
        client: Telegram 客户端
        target_channel_id: 目标频道ID
        message: 源消息
        download_dir: 下载目录，默认为 ~/.tg-mgr/downloads/

    Returns:
        True if successful, False otherwise
    """
    import os
    import time

    try:
        caption = message.caption or ""

        # 确定下载目录
        if download_dir is None:
            download_dir = _get_download_dir()

        # 生成临时文件路径
        # 根据媒体类型确定扩展名
        ext = ""
        if message.video:
            ext = ".mp4"
        elif message.document:
            ext = os.path.splitext(message.document.file_name)[1] if message.document.file_name else ""
        elif message.photo:
            ext = ".jpg"
        elif message.audio:
            ext = ".mp3"
        elif message.animation:
            ext = ".gif"

        temp_filename = f"force_forward_{message.chat.id}_{message.id}{ext}"
        temp_path = os.path.join(download_dir, temp_filename)

        # 下载（支持断点续传和重试）
        downloaded_path = _download_with_resume(client, message, temp_path, max_retries=3)

        if not downloaded_path or not os.path.exists(downloaded_path):
            print("[FORWARD] 下载失败，无法发送")
            return False

        print(f"[FORWARD] 发送媒体: {downloaded_path}")

        # 根据媒体类型发送（上传本地文件）
        for attempt in range(3):
            try:
                if message.photo:
                    client.send_photo(chat_id=target_channel_id, photo=downloaded_path, caption=caption)  # type: ignore[unused-coroutine]
                elif message.video:
                    client.send_video(chat_id=target_channel_id, video=downloaded_path, caption=caption)  # type: ignore[unused-coroutine]
                elif message.document:
                    client.send_document(chat_id=target_channel_id, document=downloaded_path, caption=caption)  # type: ignore[unused-coroutine]
                elif message.animation:
                    client.send_animation(chat_id=target_channel_id, animation=downloaded_path, caption=caption)  # type: ignore[unused-coroutine]
                elif message.audio:
                    client.send_audio(chat_id=target_channel_id, audio=downloaded_path, caption=caption)  # type: ignore[unused-coroutine]
                elif message.voice:
                    client.send_voice(chat_id=target_channel_id, voice=downloaded_path, caption=caption)  # type: ignore[unused-coroutine]
                elif message.video_note:
                    client.send_video_note(chat_id=target_channel_id, video_note=downloaded_path)  # type: ignore[unused-coroutine]
                elif message.text:
                    client.send_message(chat_id=target_channel_id, text=message.text)  # type: ignore[unused-coroutine]
                else:
                    return False

                print("[FORWARD] 发送成功")
                return True

            except Exception as e:
                print(f"[FORWARD] 发送失败 (尝试 {attempt + 1}/3): {e}")
                if attempt < 2:
                    wait_time = (attempt + 1) * 5
                    print(f"[FORWARD] 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)

        # 发送失败，保留文件以便下次续传
        print(f"[FORWARD] 发送多次失败，文件保留在: {downloaded_path}")
        return False

    except Exception as e:
        print(f"[FORWARD] 强制发送异常: {e}")
        return False


def _force_send_media_group(
    client: Client, target_channel_id: int, messages: list[Message]
) -> bool:
    """强制发送媒体组（下载后重新上传，保持媒体组结构）"""
    try:
        # 按 message_id 排序确保顺序正确
        messages = sorted(messages, key=lambda m: m.id)

        # 准备媒体组输入
        media_list = []
        for msg in messages:
            media_input = _prepare_media_for_send(msg)
            if media_input:
                media_list.append(media_input)

        if not media_list:
            return False

        client.send_media_group(target_channel_id, media_list)  # type: ignore[unused-coroutine, arg-type]
        return True
    except Exception as e:
        logger.debug(f"强制发送媒体组失败: {e}")
        return False


def forward_messages_batch(
    source_channel_id: int,
    target_channel_ids: list[int],
    messages: list[dict[str, Any]],
    check_exists: bool = False,
    force: bool = False,
) -> tuple[int, int, int]:
    """批量转发消息

    Args:
        source_channel_id: 源频道ID
        target_channel_ids: 目标频道ID列表
        messages: 要转发的消息列表
        check_exists: 是否检查消息是否存在
        force: 是否强制转发（忽略限制）

    Returns:
        (forwarded, skipped, failed)
    """
    log_file = get_log_path("forward.log")
    write_date = not log_file.exists()

    forwarded = 0
    skipped = 0
    failed = 0

    with get_client("tg-mgr") as client:
        # 确保已加入目标频道
        for target_id in target_channel_ids:
            try:
                client.get_chat(target_id)
            except Exception:
                if join_channel(client, target_id):
                    print(f"[FORWARD] 已加入目标频道 {target_id}")

        for msg in messages:
            msg_id = msg["message_id"]
            # 检查是否是媒体组消息（提前设置，供后续复用）
            try:
                original_msg = _get_original_media_group_message(client, source_channel_id, msg_id)
                msg["is_media_group"] = bool(original_msg and original_msg.media_group_id)
            except Exception:
                msg["is_media_group"] = False
                original_msg = None

            link = f"{get_channel_address(source_channel_id)}/{msg_id}"

            # 获取文件大小用于进度显示
            file_size = 0
            try:
                if original_msg:
                    # 尝试从 original_msg 获取文件大小
                    if hasattr(original_msg, 'file_size') and original_msg.file_size:
                        file_size = int(original_msg.file_size) if isinstance(original_msg.file_size, (int, float)) else 0
                    elif hasattr(original_msg, 'photo') and original_msg.photo:
                        photo_file_size = getattr(original_msg.photo, 'file_size', 0)
                        file_size = int(photo_file_size) if isinstance(photo_file_size, (int, float)) else 0
                    elif hasattr(original_msg, 'video') and original_msg.video:
                        video_file_size = getattr(original_msg.video, 'file_size', 0)
                        file_size = int(video_file_size) if isinstance(video_file_size, (int, float)) else 0
                    elif hasattr(original_msg, 'document') and original_msg.document:
                        doc_file_size = getattr(original_msg.document, 'file_size', 0)
                        file_size = int(doc_file_size) if isinstance(doc_file_size, (int, float)) else 0
            except Exception:
                file_size = 0

            size_mb = file_size / 1024 / 1024 if file_size else 0
            is_media_group = msg.get("is_media_group", False)

            # 下载阶段提示
            if size_mb > 0:
                group_suffix = " (媒体组)" if is_media_group else ""
                print(f"[DOWNLOAD] 下载中: {link} | {size_mb:.1f}MB{group_suffix}")

            for target_id in target_channel_ids:
                if check_exists:
                    if message_exists_in_channel(client, target_id, msg_id):
                        print(f"[FORWARD] 跳过（已存在）: {link} -> {target_id}")
                        skipped += 1
                        continue

                try:
                    # 检查是否是媒体组消息（已在循环开始时获取 original_msg）
                    if original_msg and original_msg.media_group_id:
                        # 媒体组消息，使用 send_media_group 转发
                        media_group_msgs = _get_media_group_messages(client, source_channel_id, original_msg.media_group_id, msg_id)
                        if media_group_msgs:
                            # 有完整媒体组，转发整个组
                            # 上传阶段提示
                            print(f"[UPLOAD] 上传中: {link}")
                            if _forward_media_group(client, source_channel_id, target_id, media_group_msgs):
                                forwarded += 1
                                total = msg.get("total", 0)
                                views = msg.get("views", 0)
                                stats = _build_stats_str(total, views, is_media_group=True)
                                print(f"[FORWARD] 转发成功: {link} -> {target_id}{stats}")
                            else:
                                failed += 1
                                continue
                        else:
                            # 媒体组消息但找不到其他同组消息，降级为 copy_message
                            client.copy_message(
                                chat_id=target_id,
                                from_chat_id=source_channel_id,
                                message_id=msg_id,
                            )
                            forwarded += 1
                            total = msg.get("total", 0)
                            views = msg.get("views", 0)
                            stats = _build_stats_str(total, views, is_media_group=True)
                            print(f"[FORWARD] 转发成功: {link} -> {target_id}{stats}")
                    else:
                        # 普通消息，使用 copy_message
                        # 上传阶段提示
                        print(f"[UPLOAD] 上传中: {link}")
                        client.copy_message(
                            chat_id=target_id,
                            from_chat_id=source_channel_id,
                            message_id=msg_id,
                        )
                        forwarded += 1
                        total = msg.get("total", 0)
                        views = msg.get("views", 0)
                        stats = _build_stats_str(total, views)
                        print(f"[FORWARD] 转发成功: {link} -> {target_id}{stats}")

                    # 写入日志
                    if write_date:
                        from datetime import datetime
                        with open(log_file, "w") as f:
                            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                        write_date = False
                    with open(log_file, "a") as f:
                        f.write(f"{link}\n")

                except errors.Forbidden:
                    print(f"[FORWARD] 频道 {target_id} 禁止复制")
                    break
                except errors.BadRequest as e:
                    if "CHAT_ADMIN_REQUIRED" in str(e):
                        print(f"[FORWARD] 频道 {target_id} 需要管理员权限")
                        break
                    if "CHAT_FORWARDS_RESTRICTED" in str(e):
                        if force:
                            # force 模式：下载后重新上传（保持媒体组结构）
                            try:
                                if original_msg and original_msg.media_group_id:
                                    media_group_msgs = _get_media_group_messages(
                                        client, source_channel_id, original_msg.media_group_id, msg_id
                                    )
                                    print(f"[DOWNLOAD] 下载完成: {link} ({len(media_group_msgs)} 条)")
                                    print(f"[UPLOAD] 上传中: {link} (媒体组 {len(media_group_msgs)} 条)")
                                    if media_group_msgs and _force_send_media_group(client, target_id, media_group_msgs):
                                        forwarded += 1
                                        total = msg.get("total", 0)
                                        views = msg.get("views", 0)
                                        stats = _build_stats_str(total, views, is_media_group=True)
                                        print(f"[FORWARD] 强制转发成功: {link}{stats}")
                                        continue
                                    else:
                                        failed += 1
                                        continue
                                else:
                                    # 非媒体组消息，使用单条强制转发
                                    if forward_single_message(client, source_channel_id, target_id, msg_id):
                                        forwarded += 1
                                        total = msg.get("total", 0)
                                        views = msg.get("views", 0)
                                        stats = _build_stats_str(total, views)
                                        print(f"[FORWARD] 强制转发成功: {link}{stats}")
                                        continue
                                    else:
                                        failed += 1
                                        continue
                            except Exception:
                                failed += 1
                                continue
                        print(f"[FORWARD] 频道 {target_id} 禁止转发内容")
                        break
                    print(f"[FORWARD] 转发失败: {link} - {e}")
                    failed += 1
                except errors.FloodWait as e:
                    wait = max(e.value, 5)
                    print(f"[FORWARD] FloodWait: 等待 {wait} 秒...")
                    time.sleep(wait)
                    # 重试一次
                    if forward_single_message(client, source_channel_id, target_id, msg_id):
                        forwarded += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"[FORWARD] 转发失败: {link} - {e}")
                    failed += 1

                time.sleep(0.5)

    return forwarded, skipped, failed


def forward_with_recursion(
    source_channels: list[int],
    target_channel: int,
    current_depth: int = 1,
    max_depth: int = DEFAULT_RECURSION_DEPTH,
    processed_channels: set[int] | None = None,
    check_exists: bool = False,
    force: bool = False,
    reaction_limit: int = 10,
) -> tuple[int, int, int]:
    """递归转发高反应消息

    Args:
        source_channels: 当前层级的源频道列表
        target_channel: 目标频道ID
        current_depth: 当前深度
        max_depth: 最大深度
        processed_channels: 已处理的频道集合
        check_exists: 是否检查消息是否存在
        force: 是否强制转发（忽略限制）

    Returns:
        (total_forwarded, total_skipped, total_failed)
    """
    if processed_channels is None:
        processed_channels = set()

    # 检查深度限制
    if current_depth > max_depth:
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
            with get_client("tg-mgr") as client:
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

        # 连接频道专属的临时数据库（不是主数据库）
        temp_db_path = get_channel_temp_db_path(channel_id)
        conn = sqlite3.connect(str(temp_db_path))

        # 获取平均浏览量用于计算阈值
        avg_row = conn.execute("SELECT COALESCE(AVG(views), 0) FROM messages WHERE views > 0").fetchone()
        avg_views = avg_row[0] if avg_row and isinstance(avg_row[0], (int, float)) else 0
        threshold = avg_views * 8 if avg_views > 0 else 0

        # 查找要转发的消息（第1层不按source_id过滤，与info一致；递归层按source_id过滤）
        filter_by_source = current_depth > 1
        messages = find_messages_to_forward(conn, channel_id, reaction_limit, filter_by_source=filter_by_source)
        # 统计高反应和高浏览量消息数量
        high_reaction_count = sum(1 for m in messages if m.get("total", 0) > 0)
        high_views_count = sum(1 for m in messages if m.get("views", 0) > threshold)
        print(f"[FORWARD] 深度 {current_depth}: 频道 {channel_id} 找到 {len(messages)} 条消息 (高反应: {high_reaction_count}, 高浏览: {high_views_count})")

        if messages:
            # 转发到目标
            f, s, fa = forward_messages_batch(channel_id, [target_channel], messages, check_exists, force)
            total_forwarded += f
            total_skipped += s
            total_failed += fa

            # 递归处理下一层（使用 info 的转发来源TOP逻辑）
            if current_depth < max_depth:
                next_channels = get_forward_sources(conn, limit=reaction_limit)
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
                    )
                    total_forwarded += nf
                    total_skipped += ns
                    total_failed += nfa
                else:
                    print(f"[FORWARD] 深度 {current_depth}: 未找到来源频道，停止递归")

        conn.close()
        processed_channels.add(channel_id)

    return total_forwarded, total_skipped, total_failed


def message_exists_in_channel(client: Client, target_channel_id: int, source_msg_id: int) -> bool:
    """检查消息是否已存在于目标频道"""
    try:
        for msg in client.get_chat_history(target_channel_id, limit=100):  # type: ignore[union-attr]
            if msg.id == source_msg_id:
                return True
        return False
    except Exception:
        return False


def join_channel(client: Client, channel_id: int) -> bool:
    """尝试加入频道"""
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


def is_channel_forwarding_allowed(client: Client, channel_id: int) -> bool:
    """检查频道是否允许转发"""
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


def main():
    """主执行流程"""
    import argparse

    parser = argparse.ArgumentParser(description="高反应消息转发模块")
    parser.add_argument("sources", nargs="+", help="源频道ID或消息链接")
    parser.add_argument("-o", "--target", type=int, help="目标频道ID")
    parser.add_argument("-c", "--check", action="store_true", help="转发前检查目标频道是否已存在")
    parser.add_argument(
        "-r", "--depth", type=int, nargs="?", const=DEFAULT_RECURSION_DEPTH, default=None,
        help=f"递归深度（-r3 或 -r 3，默认{DEFAULT_RECURSION_DEPTH}，0表示不递归）"
    )
    parser.add_argument("-f", "--force", action="store_true",
        help="强制转发禁止转发的消息（通过复制内容而非转发）")
    parser.add_argument("-l", "--limit", type=int, default=10,
        help=f"高反应消息数量限制（默认10）")

    args = parser.parse_args()

    config = get_config()
    target_channel_id = args.target if args.target else config.get("channel_id")

    if not target_channel_id:
        print("[ERROR] 未指定目标频道，且环境变量 TG_CHANNEL_ID 未配置")
        sys.exit(1)

    # 解析递归深度
    recursion_depth = args.depth if args.depth is not None else config.get("recursion_depth", DEFAULT_RECURSION_DEPTH)

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
                if join_channel(client, target_channel_id):
                    print(f"[FORWARD] 已加入目标频道 {target_channel_id}")

            for channel_id, msg_id, username in link_messages:
                # 解析用户名（如果需要）
                if channel_id is None and username:
                    resolved_id = resolve_username_to_channel_id(client, username)
                    if resolved_id is None:
                        print(f"[ERROR] 无法解析用户名 {username}，跳过")
                        continue
                    channel_id = resolved_id

                link = f"{get_channel_address(channel_id)}/{msg_id}"
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
                            except errors.BadRequest as e:
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
                                    raise
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

                # 同步
                print(f"[FORWARD] 同步频道 {channel_id}...")
                try:
                    sync_channel_for_forward(channel_id)
                except Exception as e:
                    print(f"[FORWARD] 同步失败: {e}")
                    continue

                # 连接频道专属的临时数据库（不是主数据库）
                temp_db_path = get_channel_temp_db_path(channel_id)
                conn = sqlite3.connect(str(temp_db_path))
                messages = find_messages_to_forward(conn, channel_id, args.limit, filter_by_source=False)

                # 使用 -f 时先统计后确认
                if args.force and messages:
                    summary = summarize_messages_for_forward(conn, messages)
                    if not confirm_forward(messages, summary):
                        print("[FORWARD] 已取消")
                        conn.close()
                        cleanup_channel_temp_dbs([channel_id])
                        return

                if messages:
                    # 统计高反应和高浏览量消息数量
                    avg_row = conn.execute("SELECT COALESCE(AVG(views), 0) FROM messages WHERE views > 0").fetchone()
                    avg_views = avg_row[0] if avg_row and isinstance(avg_row[0], (int, float)) else 0
                    threshold = avg_views * 8 if avg_views > 0 else 0
                    high_reaction_count = sum(1 for m in messages if m.get("total", 0) > 0)
                    high_views_count = sum(1 for m in messages if m.get("views", 0) > threshold)
                    print(f"[FORWARD] 频道 {channel_id} 找到 {len(messages)} 条消息 (高反应: {high_reaction_count}, 高浏览: {high_views_count})")
                else:
                    print(f"[FORWARD] 频道 {channel_id} 找到 0 条消息")
                if messages:
                    f, s, fa = forward_messages_batch(channel_id, [target_channel_id], messages, args.check, args.force)
                    print(f"[FORWARD] 完成: 转发 {f}, 跳过 {s}, 失败 {fa}")
                conn.close()
                cleanup_channel_temp_dbs([channel_id])
        else:
            # 递归转发 - 使用 -f 时先统计后确认
            if args.force:
                # 同步所有频道到各自的临时数据库
                synced_channels = []
                channel_messages: dict[int, list] = {}
                for ch_id in channel_ids:
                    try:
                        sync_channel_for_forward(ch_id)
                        synced_channels.append(ch_id)
                        # 从频道专属temp DB获取消息
                        temp_db_path = get_channel_temp_db_path(ch_id)
                        temp_conn = sqlite3.connect(str(temp_db_path))
                        msgs = find_messages_to_forward(temp_conn, ch_id, args.limit, filter_by_source=False)
                        if msgs:
                            channel_messages[ch_id] = msgs
                        temp_conn.close()
                    except Exception as e:
                        print(f"[FORWARD] 同步频道 {ch_id} 失败: {e}")
                        continue

                if not synced_channels:
                    print("[FORWARD] 所有频道同步失败")
                    return

                # 计算每个频道的统计信息
                channel_summaries: dict[int, dict] = {}
                all_messages = []
                for ch_id in synced_channels:
                    if ch_id in channel_messages:
                        temp_db_path = get_channel_temp_db_path(ch_id)
                        temp_conn = sqlite3.connect(str(temp_db_path))
                        summary = summarize_messages_for_forward(temp_conn, channel_messages[ch_id])
                        temp_conn.close()
                        channel_summaries[ch_id] = summary
                        all_messages.extend(channel_messages[ch_id])

                if not all_messages:
                    print("[FORWARD] 所有频道无可转发消息")
                    cleanup_channel_temp_dbs(synced_channels)
                    return

                # 显示每个频道的统计信息
                print(f"[FORWARD] 待转发消息统计（{len(synced_channels)} 个频道）：")
                total_count = 0
                total_media_count = 0
                total_size_mb = 0.0
                for ch_id in synced_channels:
                    if ch_id in channel_summaries:
                        s = channel_summaries[ch_id]
                        total_count += s["total_count"]
                        total_media_count += s["media_count"]
                        total_size_mb += s["total_size_mb"]
                        print(f"  频道 {ch_id}: {s['total_count']} 条消息, {s['media_count']} 条有媒体, {s['total_size_mb']:.1f} MB")
                print(f"  合计: {total_count} 条消息, {total_media_count} 条有媒体, {total_size_mb:.1f} MB")
                print()

                summary = {"total_count": total_count, "media_count": total_media_count, "total_size_mb": total_size_mb}
                if not confirm_forward(all_messages, summary):
                    print("[FORWARD] 已取消")
                    cleanup_channel_temp_dbs(synced_channels)
                    return

                # 确认后直接转发已预取的消息，不重复同步/查询
                print(f"[FORWARD] 开始转发（使用已确认的消息）...")
                total_f, total_s, total_fa = 0, 0, 0
                for ch_id in synced_channels:
                    if ch_id in channel_messages and channel_messages[ch_id]:
                        f, s, fa = forward_messages_batch(ch_id, [target_channel_id], channel_messages[ch_id], args.check, args.force)
                        total_f += f
                        total_s += s
                        total_fa += fa
                        print(f"[FORWARD] 频道 {ch_id} 完成: 转发 {f}, 跳过 {s}, 失败 {fa}")

                print("\n[FORWARD] ========== 全部完成 ==========")
                print(f"[FORWARD] 总计: 转发 {total_f}, 跳过 {total_s}, 失败 {total_fa}")
                cleanup_channel_temp_dbs(synced_channels)
                return


# 向后兼容别名
def find_high_reaction_messages(channel_id: int, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """向后兼容别名 - 使用 find_messages_to_forward"""
    return find_messages_to_forward(conn, channel_id)


if __name__ == "__main__":
    main()
