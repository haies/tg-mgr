"""导出核心逻辑"""
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from pyrogram import Client, errors, types

from database import get_db
from database.query import find_top_messages
from modules.sync import sync_channel
from utils.download import download_with_resume, DownloadOptions
from utils.file_sanitizer import sanitize_filename
from utils.telegram_client import create_client, get_config
from utils.telegram_link import generate_tg_link

from modules.export.preview import (
    summarize_messages_for_export as summarize_messages_for_forward,
    confirm_export as confirm_export,
)

from .template import load_html_template

logger = logging.getLogger(__name__)


# ============ 状态管理（断点续传） ============
class ExportState:
    """管理导出状态，支持断点续传"""

    def __init__(self, export_dir: Path):
        self.state_file = export_dir / "export_state.json"
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """加载状态文件"""
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError, OSError) as e:
                logger.warning(f"加载状态文件失败: {e}")
        return {
            "processed_messages": [],  # 已处理的消息ID列表
            "downloaded_files": {},  # 已下载的文件 {file_unique_id: file_path}
            "last_update": None,
        }

    def save(self):
        """保存状态到文件"""
        self.state["last_update"] = datetime.now().isoformat()
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def is_message_processed(self, message_id: int) -> bool:
        """检查消息是否已处理"""
        return message_id in self.state["processed_messages"]

    def mark_message_processed(self, message_id: int):
        """标记消息已处理"""
        if message_id not in self.state["processed_messages"]:
            self.state["processed_messages"].append(message_id)

    def is_file_downloaded(self, file_unique_id: str) -> str | None:
        """检查文件是否已下载，返回文件路径或 None"""
        return self.state["downloaded_files"].get(file_unique_id)

    def mark_file_downloaded(self, file_unique_id: str, file_path: str):
        """标记文件已下载"""
        self.state["downloaded_files"][file_unique_id] = file_path


# ============ Telegram Desktop 格式 JSON 导出 ============
def export_json_telegram_desktop_format(
    messages: list[dict], channel_info: dict, output_path: Path
):
    """
    导出为 Telegram Desktop 格式的 JSON
    """
    export_data = {
        "about": "Here is the data you requested. "
        "Telegram Desktop exports data in a machine-readable JSON format, "
        "which may be used by third-party apps to analyze your data.",
        "chats": {
            "about": "This page lists all chats from this export.",
            "list": [
                {
                    "name": channel_info.get("title", "Unknown"),
                    "type": "channel",
                    "id": channel_info.get("id", 0),
                    "messages": messages,
                }
            ],
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    print(f"[EXPORT] JSON 已导出至: {output_path}")


# ============ Telegram Desktop 风格 HTML 导出 ============
def format_timestamp(ts: str) -> tuple[str, str]:
    """格式化时间戳为日期和时间"""
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def generate_message_html(msg: dict, prev_date: str | None) -> tuple[str, str | None]:
    """生成单条消息的 HTML"""
    date_str, time_str = format_timestamp(msg.get("date", datetime.now()))

    html_parts = []

    # 日期分隔线
    if date_str != prev_date:
        html_parts.append(f'<div class="date-divider"><span>{date_str}</span></div>')

    # 消息气泡
    html_parts.append('<div class="message">')
    html_parts.append('<div class="message-bubble">')

    # 转发信息
    if msg.get("forwarded_from"):
        html_parts.append(f'<div class="forward-info">转发自 {msg["forwarded_from"]}</div>')

    # 发送者（如果有）
    if msg.get("from") and msg.get("from") != "Channel":
        html_parts.append(f'<div class="message-sender">{msg["from"]}</div>')

    # 媒体
    if msg.get("photo"):
        html_parts.append(f'''
        <div class="message-media">
            <img src="{msg["photo"]}" alt="Photo" onclick="openLightbox('{msg["photo"]}')">
        </div>
        ''')
    elif msg.get("video"):
        html_parts.append(f'''
        <div class="message-media">
            <video src="{msg["video"]}" controls preload="metadata"></video>
        </div>
        ''')
    elif msg.get("file"):
        file_info = msg.get("file_info", {})
        file_name = file_info.get("name", "文件")
        file_size = file_info.get("size_formatted", "")
        html_parts.append(f"""
        <div class="message-media">
            <div class="file-attachment">
                <div class="file-icon">📄</div>
                <div class="file-info">
                    <div class="file-name">{file_name}</div>
                    <div class="file-size">{file_size}</div>
                </div>
            </div>
        </div>
        """)

    # 文本内容
    if msg.get("text"):
        # 处理链接
        text = msg["text"]
        text = text.replace("\n", "<br>")
        html_parts.append(f'<div class="message-text">{text}</div>')

    # 时间和链接
    tg_link = generate_tg_link(msg.get("channel_id", ""), msg.get("id", 0))
    html_parts.append(f'''
    <div class="message-time">
        <a href="{tg_link}" target="_blank">#{msg.get("id", "")}</a>
        {time_str}
    </div>
    ''')

    html_parts.append("</div></div>")

    return "\n".join(html_parts), date_str


def export_html_telegram_desktop_style(messages: list[dict], channel_info: dict, output_path: Path):
    """导出为 Telegram Desktop 风格的 HTML"""

    # 加载模板
    html_template = load_html_template()

    messages_html_parts = []
    prev_date = None

    for msg in messages:
        msg_html, prev_date = generate_message_html(msg, prev_date)
        messages_html_parts.append(msg_html)

    # 使用 string.Template 的 safe_substitute 避免缺失占位符导致 KeyError
    html_content = Template(html_template).safe_substitute(
        channel_name=channel_info.get("title", "Unknown Channel"),
        message_count=len(messages),
        export_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        messages_html="\n".join(messages_html_parts),
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[EXPORT] HTML 已导出至: {output_path}")


# ============ 媒体下载（直接使用 Message 对象） ============
def download_media_from_message(
    client: Client, message: types.Message, media_type: str, export_dir: Path, state: ExportState
) -> str | None:
    """
    直接从 Message 对象下载媒体文件，使用 Message 对象包含的 access_hash

    这是优化的下载方式，直接使用 get_chat_history 返回的 Message 对象，
    避免额外的 API 调用导致的 PeerIdInvalid 错误。

    Returns:
        相对于导出目录的文件路径，或 None（下载失败/非媒体消息）
    """
    # 获取媒体信息和 file_unique_id
    file_unique_id = None

    if message.photo:
        file_unique_id = message.photo.file_unique_id
        media_type = "photos"
    elif message.video:
        file_unique_id = message.video.file_unique_id
        media_type = "videos"
    elif message.document:
        file_unique_id = message.document.file_unique_id
        media_type = "files"
    elif message.audio:
        file_unique_id = message.audio.file_unique_id
        media_type = "files"
    elif message.voice:
        file_unique_id = message.voice.file_unique_id
        media_type = "voice"
    elif message.video_note:
        file_unique_id = message.video_note.file_unique_id
        media_type = "video_notes"
    elif message.sticker:
        file_unique_id = message.sticker.file_unique_id
        media_type = "stickers"
    else:
        return None  # 非媒体消息

    # 增量下载：先查 state 记录，再验证实际文件（文件系统为权威依据）
    existing_path = state.is_file_downloaded(file_unique_id)
    if existing_path:
        full_path = export_dir / existing_path
        if full_path.exists() and full_path.stat().st_size > 0:
            print(f"  [SKIP] 文件已存在: {existing_path}")
            return existing_path
        # state 有记录但文件已删除 — 删除 stale 记录
        del state.state["downloaded_files"][file_unique_id]

    # 如果文件实际存在但不在 state 中，补录 state 记录
    if not existing_path:
        for media_subdir in ["photos", "videos", "files", "voice", "video_notes", "stickers"]:
            potential_path = export_dir / media_subdir
            if potential_path.exists():
                for f in potential_path.iterdir():
                    if str(message.id) in f.name and f.stat().st_size > 0:
                        rel = f"{media_subdir}/{f.name}"
                        state.mark_file_downloaded(file_unique_id, rel)
                        print(f"  [SKIP] 文件已存在（来自目录扫描）: {rel}")
                        return rel

    # 确定保存路径
    media_dir = export_dir / media_type
    media_dir.mkdir(exist_ok=True)

    # 生成文件名
    file_ext = ".jpg"  # 默认扩展名
    if message.video or message.video_note:
        file_ext = ".mp4"
    elif message.document and message.document.file_name:
        file_ext = Path(message.document.file_name).suffix or ".bin"
    elif message.audio:
        file_ext = ".mp3"
    elif message.voice:
        file_ext = ".ogg"
    elif message.sticker:
        file_ext = ".webp"

    filename = f"{media_type}_{message.id}{file_ext}"
    save_path = media_dir / filename

    # 下载媒体 - 直接使用 Message 对象
    # 这样可以保留服务器校验所需的 access_hash
    try:
        print(f"  [DOWNLOAD] 正在下载 {media_type}: message_{message.id}")

        # 关键优化：直接传入 Message 对象，而不是 file_id
        # 这样 Pyrogram 会使用 Message 中包含的完整上下文（包括 access_hash）
        downloaded_path = client.download_media(
            message,  # 直接使用 Message 对象
            file_name=str(save_path),
        )

        if downloaded_path and Path(downloaded_path).exists():  # type: ignore[arg-type]
            rel_path = f"{media_type}/{filename}"
            state.mark_file_downloaded(file_unique_id, rel_path)
            print(f"  [SUCCESS] 下载完成: {rel_path}")
            return rel_path

    except errors.FloodWait as e:
        print(f"  [WARNING] FloodWait: 等待 {e.value} 秒...")
        time.sleep(e.value + 1)
        # 递归重试
        return download_media_from_message(client, message, media_type, export_dir, state)
    except errors.MediaInvalid:
        print(f"  [ERROR] 媒体文件无效或已过期: message_{message.id}")
    except Exception as e:
        print(f"  [ERROR] 下载失败: {e}")

    return None


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if not size_bytes:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


# ============ 消息处理 ============
def process_message_for_export(message: types.Message, channel_info: dict) -> dict:
    """
    处理单条消息，提取元数据（不下载媒体）
    返回 Telegram Desktop 格式的消息字典
    """
    # 获取消息基本信息
    msg_data = {
        "id": message.id,
        "type": "message",
        "date": message.date.isoformat() if message.date else datetime.now().isoformat(),
        "from": message.from_user.first_name if message.from_user else "Channel",
        "from_id": message.from_user.id if message.from_user else channel_info["id"],
        "text": message.caption or message.text or "",
        "channel_id": channel_info["id"],
    }

    # 处理转发信息
    if message.forward_from_chat:
        msg_data["forwarded_from"] = message.forward_from_chat.title or "Unknown"
    elif message.forward_sender_name:
        msg_data["forwarded_from"] = message.forward_sender_name

    # 处理媒体元数据（不下载）
    if message.photo:
        msg_data["photo"] = True  # 占位，实际路径在下载后填充
    elif message.video:
        msg_data["video"] = True
        msg_data["duration"] = message.video.duration or 0
        msg_data["width"] = message.video.width or 0
        msg_data["height"] = message.video.height or 0
    elif message.document:
        msg_data["file"] = True
        msg_data["file_info"] = {
            "name": message.document.file_name or f"file_{message.id}",
            "size": message.document.file_size or 0,
            "size_formatted": format_file_size(message.document.file_size),
            "mime_type": message.document.mime_type or "application/octet-stream",
        }
    elif message.audio:
        msg_data["file"] = True
        msg_data["file_info"] = {
            "name": message.audio.file_name or f"audio_{message.id}.mp3",
            "size": message.audio.file_size or 0,
            "size_formatted": format_file_size(message.audio.file_size),
        }
    elif message.voice:
        msg_data["voice"] = True
        msg_data["duration"] = message.voice.duration or 0
    elif message.video_note:
        msg_data["video_note"] = True
        msg_data["duration"] = message.video_note.duration or 0
    elif message.sticker:
        msg_data["sticker"] = True

    return msg_data


def update_message_with_download_path(msg_data: dict, download_path: str | None) -> dict:
    """更新消息数据，填充下载后的文件路径"""
    if not download_path:
        # 下载失败，移除占位符
        for key in ["photo", "video", "file", "voice", "video_note", "sticker"]:
            if msg_data.get(key):
                del msg_data[key]
        return msg_data

    # 填充实际路径
    if msg_data.get("photo"):
        msg_data["photo"] = download_path
    elif msg_data.get("video"):
        msg_data["video"] = download_path
    elif msg_data.get("file"):
        msg_data["file"] = download_path
    elif msg_data.get("voice"):
        msg_data["voice"] = download_path
    elif msg_data.get("video_note"):
        msg_data["video_note"] = download_path
    elif msg_data.get("sticker"):
        msg_data["sticker"] = download_path

    return msg_data


# ============ 导出下载（复用 forward 的下载逻辑）============
def _download_messages_batch(
    client,
    channel_id: int,
    messages: list[dict[str, Any]],
    export_dir: Path,
    state: "ExportState",
) -> tuple[int, int, int]:
    """批量下载消息媒体（与 forward -f 的下载逻辑完全一致）

    Args:
        client: Telegram 客户端
        channel_id: 频道ID
        messages: 消息列表
        export_dir: 导出目录
        state: 状态管理

    Returns:
        (downloaded, skipped, failed)
    """
    from modules.forward.send import _get_media_group_member_ids

    downloaded = 0
    skipped = 0
    failed = 0

    # 已处理的媒体组
    processed_groups: set[str] = set()

    for msg in messages:
        msg_id = msg["message_id"]
        db_media_group_id = msg.get("media_group_id")
        msg["is_media_group"] = bool(db_media_group_id)

        # 媒体组：只处理一次
        if db_media_group_id and db_media_group_id in processed_groups:
            skipped += 1
            continue

        file_size = msg.get("file_size", 0)
        size_mb = file_size / 1024 / 1024 if file_size else 0

        try:
            if db_media_group_id:
                # 媒体组：从数据库获取所有成员
                with get_db() as conn:
                    member_ids = _get_media_group_member_ids(conn, channel_id, db_media_group_id)

                if len(member_ids) >= 2:
                    # 完整媒体组，逐个下载
                    media_group_msgs = client.get_messages(channel_id, member_ids)
                    for gm in media_group_msgs:
                        if gm:
                            dl_path = _download_single_media(client, gm, export_dir, state)
                            if dl_path:
                                downloaded += 1
                            time.sleep(0.1)
                    processed_groups.add(db_media_group_id)
                    link = generate_tg_link(channel_id, msg_id)
                    print(f"[EXPORT] 下载成功: {link} ({size_mb:.1f}MB, 媒体组)")
                    continue
                elif len(member_ids) == 1:
                    # 只有一个成员，当作普通消息处理
                    pass
                else:
                    # 没有成员，可能是数据不一致，当作普通消息处理
                    pass

            # 单条消息下载
            fetched = client.get_messages(channel_id, msg_id)
            if fetched:
                dl_path = _download_single_media(client, fetched, export_dir, state)
                if dl_path:
                    downloaded += 1
                    link = generate_tg_link(channel_id, msg_id)
                    print(f"[EXPORT] 下载成功: {link} ({size_mb:.1f}MB)")
            else:
                failed += 1
                print(f"[EXPORT] 获取消息 {msg_id} 失败")
        except Exception as e:
            failed += 1
            print(f"[EXPORT] 下载失败 message {msg_id}: {e}")

    return downloaded, skipped, failed


def _download_single_media(
    client,
    message,
    export_dir: Path,
    state: "ExportState",
) -> str | None:
    """下载单条媒体消息

    Args:
        client: Telegram 客户端
        message: 消息对象
        export_dir: 导出目录
        state: 状态管理

    Returns:
        下载完成的文件路径，失败返回 None
    """
    try:
        from utils.download import download_with_resume, DownloadOptions

        # 生成文件名
        ext = ""
        if message.video:
            ext = ".mp4"
        elif message.document:
            ext = Path(message.document.file_name).suffix if message.document.file_name else ""
        elif message.photo:
            ext = ".jpg"
        elif message.audio:
            ext = ".mp3"
        elif message.animation:
            ext = ".gif"

        filename = f"media_{message.chat.id}_{message.id}{ext}"
        target_path = str(export_dir / filename)

        # 增量下载：检查文件是否已存在
        if Path(target_path).exists() and Path(target_path).stat().st_size > 0:
            print(f"  [SKIP] 文件已存在: {filename}")
            return target_path

        options = DownloadOptions(max_retries=3)
        result = download_with_resume(client, message, target_path, options)

        if result:
            state.mark_message_processed(message.id)
        return result
    except Exception as e:
        print(f"[EXPORT] 下载媒体失败: {e}")
        return None


# ============ 主导出流程 ============
def find_existing_export_dir(base_dir: Path, channel_title: str) -> Path | None:
    """查找已存在的导出目录"""
    pattern = f"{sanitize_filename(channel_title)}_*"
    matching_dirs = list(base_dir.glob(pattern))

    if matching_dirs:
        return sorted(matching_dirs, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    return None


def run_export(channel_id: str | None = None, message_ids: list[int] | None = None, preview: bool = False, file_size_limit: int | None = None) -> None:
    """
    主导出流程

    Args:
        channel_id: 频道ID
        message_ids: 可选，指定要导出的消息ID列表
        preview: 是否开启预览确认模式
        file_size_limit: 可选，文件大小限制(MB)，只导出小于该大小的消息

    优化点：
    1. 直接使用 get_chat_history 返回的 Message 对象下载媒体
    2. 避免额外的 API 调用导致的 PeerIdInvalid 错误
    3. 支持指定消息导出（用于断点续传）
    4. 支持预览确认模式 (-p 参数)
    5. 支持文件大小过滤 (-l 参数)
    """
    # 加载配置
    config = get_config()
    _channel_id = channel_id if channel_id else config.get("channel_id")

    if not _channel_id:
        print("[ERROR] 未指定频道ID，请在命令行传入或使用 config.json 配置")
        import sys

        sys.exit(1)

    print(f"[EXPORT] 开始导出频道: {_channel_id}")

    # 预览模式：先同步到 DB（与 forward -f 完全一致，在客户端启动前执行）
    if preview:
        print(f"[EXPORT] 同步频道 {_channel_id} 到数据库...")
        sync_channel(str(_channel_id))

    # 创建 Telegram 客户端（使用统一的客户端工具）
    client, is_started = create_client(config, session_name="tg-mgr")
    if not is_started:
        client.start()  # type: ignore[unused-coroutine]

    try:
        # 获取频道信息
        print("[EXPORT] 正在获取频道信息...")
        try:
            chat = client.get_chat(_channel_id)
            channel_info = {
                "id": chat.id,  # type: ignore[attr-defined]
                "title": chat.title or "Unknown Channel",  # type: ignore[attr-defined]
                "username": chat.username,  # type: ignore[attr-defined]
                "type": chat.type.value if hasattr(chat.type, "value") else str(chat.type),  # type: ignore[attr-defined]
            }
        except errors.PeerIdInvalid as e:
            print(f"[ERROR] 无法访问该频道: {e}")
            print("可能的原因：频道 ID 不正确、用户没有加入该频道或没有权限访问")
            raise

        print(f"[EXPORT] 频道名称: {channel_info['title']}")

        # 确定导出目录
        base_download_dir = Path(config.get("download_dir", "~/Downloads/Telegram")).expanduser()
        base_download_dir.mkdir(parents=True, exist_ok=True)

        # 使用频道名称作为目录名（无时间戳，支持重复运行定位同一目录）
        safe_title = sanitize_filename(channel_info["title"])
        export_dir = base_download_dir / safe_title

        if export_dir.exists():
            print(f"[EXPORT] 找到已有导出目录，将继续导出: {export_dir}")
        else:
            export_dir.mkdir(parents=True, exist_ok=True)
            print(f"[EXPORT] 创建新导出目录: {export_dir}")

        # 初始化状态管理
        state = ExportState(export_dir)

        # 预览模式：从数据库查询（sync_channel 已在客户端启动前执行）
        if preview:
            # 从数据库查询消息（与 forward -f 完全一致）
            with get_db() as conn:
                messages = find_top_messages(
                    conn,
                    reaction_limit=200,
                    views_limit=100,
                    channel_id=int(_channel_id),
                    file_size_limit=file_size_limit,
                )

                if not messages:
                    print("[EXPORT] 数据库中无消息")
                    return

                # 预览统计（复用 forward 的函数）
                summary = summarize_messages_for_forward(conn, messages)
                print(f"[EXPORT] 预览模式：共 {len(messages)} 条消息")
                if not confirm_export(messages, summary):
                    print("[EXPORT] 已取消")
                    return

            # 下载媒体（复用 forward 的下载逻辑）
            print("[EXPORT] 开始下载媒体...")
            _download_messages_batch(client, int(_channel_id), messages, export_dir, state)

            print(f"[EXPORT] 媒体下载完成")
            print("[EXPORT] 导出完成！")
            print(f"  - 导出目录: {export_dir}")
            return

        # 收集所有消息
        messages = []
        total_count = 0
        downloaded_count = 0
        skipped_count = 0
        failed_count = 0

        print("[EXPORT] 正在获取消息列表并下载媒体...")

        try:
            # 关键优化：使用 get_chat_history 迭代器直接处理消息
            # 这样可以确保每个 Message 对象包含正确的 access_hash

            # 如果指定了消息ID，只处理这些消息
            target_message_ids = set(message_ids) if message_ids else None
            if target_message_ids:
                print(f"[EXPORT] 将只导出指定的消息: {target_message_ids}")

            offset_id = 0
            batch_size = 100
            has_more = True

            while has_more:
                batch_messages = []

                # 获取一批消息
                for message in client.get_chat_history(  # type: ignore[union-attr]
                    _channel_id, offset_id=offset_id, limit=batch_size
                ):
                    batch_messages.append(message)
                    offset_id = message.id

                if not batch_messages:
                    has_more = False
                    break

                # 处理每批消息
                for message in batch_messages:
                    total_count += 1

                    # 如果指定了目标消息ID，跳过不在目标中的消息
                    if target_message_ids and message.id not in target_message_ids:
                        continue

                    # 检查是否已处理
                    if state.is_message_processed(message.id):
                        skipped_count += 1
                        continue

                    try:
                        # 提取消息元数据
                        msg_data = process_message_for_export(message, channel_info)

                        # 如果是媒体消息，直接下载（使用 Message 对象）
                        download_path = None
                        if message.media:
                            download_path = download_media_from_message(
                                client, message, "", export_dir, state
                            )
                            # 更新消息数据，填充下载路径
                            msg_data = update_message_with_download_path(msg_data, download_path)

                        # 保存消息数据
                        if any(
                            msg_data.get(k)
                            for k in [
                                "photo",
                                "video",
                                "file",
                                "voice",
                                "video_note",
                                "sticker",
                                "text",
                            ]
                        ):
                            messages.append(msg_data)
                            downloaded_count += 1

                        # 标记消息已处理
                        state.mark_message_processed(message.id)

                        # 每处理10条保存一次状态
                        if total_count % 10 == 0:
                            state.save()

                        # 每100条显示进度
                        if total_count % 100 == 0:
                            print(
                                f"  进度: {total_count} 条 (跳过 {skipped_count}, 成功 {downloaded_count}, 失败 {failed_count})"
                            )

                    except Exception as e:
                        failed_count += 1
                        print(f"  [ERROR] 处理消息 {message.id} 失败: {e}")
                        # 继续处理下一条
                        continue

                # 每批次保存一次状态
                state.save()

            # 最终保存状态
            state.save()

            print(
                f"[EXPORT] 消息处理完成: 总计 {total_count} 条, 跳过 {skipped_count} 条, 成功 {downloaded_count} 条, 失败 {failed_count} 条"
            )

            # 导出 JSON
            json_path = export_dir / "messages.json"

            # 如果已有 JSON，合并
            if json_path.exists():
                print("[EXPORT] 发现已有 JSON，正在合并...")
                try:
                    with open(json_path, encoding="utf-8") as f:
                        existing_data = json.load(f)
                    existing_messages = (
                        existing_data.get("chats", {}).get("list", [{}])[0].get("messages", [])
                    )

                    existing_ids = {m["id"] for m in existing_messages}
                    for msg in messages:
                        if msg["id"] not in existing_ids:
                            existing_messages.append(msg)

                    existing_messages.sort(key=lambda m: m["id"])
                    messages = existing_messages
                except Exception as e:
                    print(f"  [WARNING] 合并 JSON 失败: {e}")

            export_json_telegram_desktop_format(messages, channel_info, json_path)

            # 导出 HTML
            html_path = export_dir / "messages.html"
            export_html_telegram_desktop_style(messages, channel_info, html_path)

            print("[EXPORT] 导出完成！")
            print(f"  - 导出目录: {export_dir}")
            print(f"  - JSON 文件: {json_path}")
            print(f"  - HTML 文件: {html_path}")

        except errors.FloodWait as e:
            print(f"[ERROR] 触发 FloodWait，请等待 {e.value} 秒后重试")
            # 保存当前状态
            state.save()
        except Exception as e:
            print(f"[ERROR] 导出过程中出错: {e}")
            # 保存当前状态
            state.save()
            raise
    finally:
        # 确保客户端正确关闭
        if "client" in locals():
            client.stop()  # type: ignore[unused-coroutine]
            print("[EXPORT] 客户端已断开连接")


def main():
    """主执行流程"""
    from .cli import parse_export_args

    args = parse_export_args(sys.argv[1:])

    if not args.channel_ids:
        print("[ERROR] 未指定频道ID，且config.json中未配置channel_id")
        sys.exit(1)

    try:
        # 支持多频道导出和指定消息导出
        for i, channel_id in enumerate(args.channel_ids):
            msg_ids = (
                [args.message_ids[i]] if i < len(args.message_ids) and args.message_ids[i] else None
            )
            print(
                f"[EXPORT] 开始导出频道: {channel_id}" + (f", 消息ID: {msg_ids}" if msg_ids else "")
            )
            run_export(channel_id=channel_id, message_ids=msg_ids, preview=args.preview, file_size_limit=args.limit)
    except KeyboardInterrupt:
        print("\n[EXPORT] 用户中断导出，已保存当前进度，可重新运行继续")
        sys.exit(0)
    except errors.PeerIdInvalid:
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 导出失败: {e}")
        sys.exit(1)