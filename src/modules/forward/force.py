"""转发强制模式模块 - 下载后重新上传"""
import os
import shutil
import time
from typing import Optional

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

from utils.media import extract_reaction_data
from utils.download import download_with_resume, DownloadOptions


def _get_download_dir() -> str:
    """获取下载目录，默认为 ~/.tg-mgr/downloads/"""
    config_dir = os.path.expanduser("~/.tg-mgr")
    download_dir = os.path.join(config_dir, "downloads")
    os.makedirs(download_dir, exist_ok=True)
    return download_dir


def _get_reaction_total(message: Message) -> int:
    """从消息中提取反应总数"""
    return extract_reaction_data(message).total


def get_channel_address(channel_id: int) -> str:
    """获取频道链接地址"""
    return f"https://t.me/c/{abs(channel_id)}"


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
    options = DownloadOptions(max_retries=max_retries)
    return download_with_resume(client, message, target_path, options)


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
    client: Client, target_channel_id: int, messages: list[Message] | list[int],
    download_dir: str | None = None, source_channel_id: int | None = None
) -> bool:
    """强制发送媒体组（下载后重新上传，保持媒体组结构）

    Args:
        client: Telegram 客户端
        target_channel_id: 目标频道ID
        messages: 媒体组消息列表（按 message_id 排序），可以是 Message 对象列表或 message_id 列表
        download_dir: 下载目录，默认为 ~/.tg-mgr/downloads/
        source_channel_id: 源频道ID（当 messages 是 message_id 列表时必须提供）

    Returns:
        True if successful, False otherwise
    """
    import shutil

    if not messages:
        return False

    # 确定下载目录
    if download_dir is None:
        download_dir = _get_download_dir()

    # 如果传入的是 message_id 列表，需要先获取 Message 对象
    if messages and isinstance(messages[0], int):
        # message_id 列表：需要获取 Message 对象
        if source_channel_id is None:
            print("[FORWARD] _force_send_media_group 需要 source_channel_id 参数")
            return False
        message_ids = messages  # type: ignore[assignment]
        try:
            # 批量获取消息对象
            fetched_messages = client.get_messages(source_channel_id, message_ids)  # type: ignore[union-attr]
            messages = [m for m in fetched_messages if m]  # type: ignore[assignment]
        except Exception as e:
            print(f"[FORWARD] 获取媒体组消息失败: {e}")
            return False

    # 创建媒体组专用临时目录
    group_id = messages[0].media_group_id  # type: ignore[attr-defined]
    group_temp_dir = os.path.join(download_dir, f"force_group_{group_id}_{int(time.time())}")
    os.makedirs(group_temp_dir, exist_ok=True)

    downloaded_files: list[tuple[str, Message]] = []  # (file_path, message)

    try:
        # 按 message_id 排序确保顺序正确
        messages = sorted(messages, key=lambda m: m.id)

        # 第一步：下载所有媒体到本地
        for i, msg in enumerate(messages):
            # 确定文件扩展名
            ext = ""
            if msg.video:
                ext = ".mp4"
            elif msg.document:
                ext = os.path.splitext(msg.document.file_name)[1] if msg.document.file_name else ""
            elif msg.photo:
                ext = ".jpg"
            elif msg.animation:
                ext = ".gif"

            temp_filename = f"group_{msg.id}{ext}"
            temp_path = os.path.join(group_temp_dir, temp_filename)

            # 下载单个媒体（支持断点续传）
            downloaded_path = _download_with_resume(client, msg, temp_path, max_retries=3)
            if downloaded_path and os.path.exists(downloaded_path):
                downloaded_files.append((downloaded_path, msg))
                print(f"[DOWNLOAD] 媒体组 {i+1}/{len(messages)} 下载完成: {downloaded_path}")
            else:
                print(f"[DOWNLOAD] 媒体组 {i+1}/{len(messages)} 下载失败，跳过此消息")

        if not downloaded_files:
            print("[FORWARD] 媒体组所有文件下载失败")
            return False

        # 第二步：准备本地文件的 InputMedia 列表
        media_list: list[InputMedia] = []
        for file_path, msg in downloaded_files:
            caption = msg.caption or ""
            try:
                # 根据媒体类型选择 InputMedia（使用本地文件路径）
                if msg.photo:
                    media_list.append(InputMediaPhoto(file_path, caption=caption))
                elif msg.video:
                    media_list.append(InputMediaVideo(file_path, caption=caption))
                elif msg.document:
                    media_list.append(InputMediaDocument(file_path, caption=caption))
                elif msg.animation:
                    media_list.append(InputMediaAnimation(file_path, caption=caption))
                elif msg.audio:
                    media_list.append(InputMediaAudio(file_path, caption=caption))
            except Exception as e:
                print(f"[FORWARD] 准备媒体失败: {e}")
                continue

        if not media_list:
            print("[FORWARD] 媒体组无可发送的媒体")
            return False

        # 第三步：发送媒体组（使用本地文件）
        print(f"[UPLOAD] 上传媒体组 ({len(media_list)} 条)...")
        client.send_media_group(target_channel_id, media_list)  # type: ignore[unused-coroutine, arg-type]
        print(f"[FORWARD] 媒体组强制转发成功 ({len(media_list)} 条)")
        return True

    except errors.FloodWait as e:
        wait = max(e.value, 5)
        print(f"[FORWARD] FloodWait: 等待 {wait} 秒...")
        time.sleep(wait)
        return False
    except Exception as e:
        print(f"[FORWARD] 媒体组强制转发失败: {e}")
        return False
    finally:
        # 第四步：清理临时文件
        try:
            if os.path.exists(group_temp_dir):
                shutil.rmtree(group_temp_dir)
                print(f"[CLEANUP] 已清理临时目录: {group_temp_dir}")
        except Exception as e:
            print(f"[CLEANUP] 清理临时目录失败: {e}")
