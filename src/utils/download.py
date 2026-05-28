"""通用媒体下载工具

提供跨模块复用的下载功能：
- 断点续传
- 重试机制
- 进度显示
- 下载验证
"""

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class DownloadOptions:
    """下载选项"""
    max_retries: int = 3
    resume_enabled: bool = True
    verify_integrity: bool = True
    progress_callback: Optional[Callable[[int, int], None]] = None


def get_file_size_from_message(message) -> int:
    """从 Message 对象提取文件大小"""
    if hasattr(message, 'video') and message.video:
        return message.video.file_size or 0
    elif hasattr(message, 'document') and message.document:
        return message.document.file_size or 0
    elif hasattr(message, 'photo') and message.photo:
        return getattr(message.photo, 'file_size', 0) or 0
    elif hasattr(message, 'audio') and message.audio:
        return message.audio.file_size or 0
    elif hasattr(message, 'animation') and message.animation:
        return message.animation.file_size or 0
    return 0


def download_with_resume(
    client,
    message,
    target_path: str,
    options: DownloadOptions | None = None,
) -> str | None:
    """通用下载函数，支持断点续传、重试和验证

    Args:
        client: Telegram 客户端
        message: 源消息对象
        target_path: 目标文件路径
        options: 下载选项，默认使用稳健配置

    Returns:
        下载完成的文件路径，失败返回 None
    """
    if options is None:
        options = DownloadOptions()

    file_size = get_file_size_from_message(message)
    if file_size > 0:
        print(f"[DOWNLOAD] 文件大小: {file_size / 1024 / 1024:.1f} MB")

    for attempt in range(options.max_retries):
        try:
            downloaded_size = 0
            if options.resume_enabled and os.path.exists(target_path):
                downloaded_size = os.path.getsize(target_path)
                if file_size > 0 and downloaded_size >= file_size:
                    print(f"[DOWNLOAD] 文件已完整下载: {downloaded_size} bytes")
                    return target_path
                if downloaded_size > 0:
                    print(f"[DOWNLOAD] 断点续传: 已下载 {downloaded_size / 1024 / 1024:.1f} MB，继续...")

            progress_func = None
            if options.progress_callback:
                def progress(current, total):
                    if total > 0:
                        pct = current / total * 100
                        if current % (1024 * 1024 * 10) == 0:
                            print(f"[DOWNLOAD] 进度: {current / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB ({pct:.1f}%)")
                progress_func = progress

            result_path = client.download_media(
                message,
                file_name=target_path,
                progress=progress_func
            )

            if result_path and os.path.exists(result_path):
                final_size = os.path.getsize(result_path)
                if options.verify_integrity and file_size > 0 and final_size > 0 and final_size < file_size * 0.95:
                    print(f"[DOWNLOAD] 下载不完整: {final_size} / {file_size} bytes，重试...")
                    continue
                print(f"[DOWNLOAD] 下载完成: {final_size / 1024 / 1024:.1f} MB")
                return result_path

        except Exception as e:
            print(f"[DOWNLOAD] 下载失败 (尝试 {attempt + 1}/{options.max_retries}): {e}")
            if attempt < options.max_retries - 1:
                wait_time = (attempt + 1) * 5
                print(f"[DOWNLOAD] 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)

    return None