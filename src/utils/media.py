"""
媒体工具模块

提供共享的媒体类型提取和反应数据处理功能：
1. 从 Telegram Message 对象提取媒体信息
2. 提取和处理反应数据
"""
from dataclasses import dataclass
from typing import Any

from pyrogram import types


@dataclass
class MediaInfo:
    """媒体信息数据类"""
    file_unique_id: str | None
    file_size: int | None
    media_type: str | None


@dataclass
class ReactionData:
    """反应数据数据类"""
    positive: int
    heart: int
    total: int


def extract_media_info(message: types.Message) -> MediaInfo:
    """
    从 Message 对象提取媒体信息

    Args:
        message: Telegram Message 对象

    Returns:
        MediaInfo 包含 file_unique_id, file_size, media_type
    """
    if message.photo:
        if hasattr(message.photo, 'sizes') and message.photo.sizes:
            largest_photo = max(message.photo.sizes, key=lambda p: p.file_size)
            return MediaInfo(
                file_unique_id=largest_photo.file_unique_id,
                file_size=largest_photo.file_size,
                media_type="photo"
            )
        return MediaInfo(
            file_unique_id=message.photo.file_unique_id,
            file_size=message.photo.file_size,
            media_type="photo"
        )
    elif message.video:
        return MediaInfo(
            file_unique_id=message.video.file_unique_id,
            file_size=message.video.file_size,
            media_type="video"
        )
    elif message.document:
        return MediaInfo(
            file_unique_id=message.document.file_unique_id,
            file_size=message.document.file_size,
            media_type="document"
        )
    elif message.audio:
        return MediaInfo(
            file_unique_id=message.audio.file_unique_id,
            file_size=message.audio.file_size,
            media_type="audio"
        )
    elif message.animation:
        return MediaInfo(
            file_unique_id=message.animation.file_unique_id,
            file_size=message.animation.file_size,
            media_type="animation"
        )
    elif message.voice:
        return MediaInfo(
            file_unique_id=message.voice.file_unique_id,
            file_size=message.voice.file_size,
            media_type="voice"
        )
    elif message.video_note:
        return MediaInfo(
            file_unique_id=message.video_note.file_unique_id,
            file_size=message.video_note.file_size,
            media_type="video_note"
        )
    elif message.text:
        return MediaInfo(file_unique_id="", file_size=None, media_type="text")
    else:
        return MediaInfo(file_unique_id="", file_size=None, media_type="other")


def extract_reaction_data(message: types.Message) -> ReactionData:
    """
    从 Message 对象提取反应数据

    Args:
        message: Telegram Message 对象

    Returns:
        ReactionData 包含 positive, heart, total
    """
    positive_count = 0
    heart_count = 0

    if hasattr(message, 'reactions') and message.reactions:
        for reaction in message.reactions.reactions:
            if reaction.emoji == '👍':
                positive_count += reaction.count
            elif reaction.emoji in ['❤️', '❤']:
                heart_count += reaction.count

    return ReactionData(
        positive=positive_count,
        heart=heart_count,
        total=positive_count + heart_count
    )


def extract_source_id(message: types.Message) -> int | None:
    """
    提取转发消息的源频道 ID

    Args:
        message: Telegram Message 对象

    Returns:
        源频道 ID，或 None（不是转发消息）
    """
    if message.forward_from_chat:
        return message.forward_from_chat.id
    elif message.forward_sender_name:
        # 来自用户的转发消息，使用负的 message id 表示
        return -abs(message.id)
    return None


def message_to_dict(message: types.Message) -> dict[str, Any]:
    """
    将 Message 对象转换为字典（用于导出）

    Args:
        message: Telegram Message 对象

    Returns:
        包含媒体信息的字典
    """
    media_info = extract_media_info(message)
    reaction = extract_reaction_data(message)
    source_id = extract_source_id(message)

    return {
        "message_id": message.id,
        "file_unique_id": media_info.file_unique_id,
        "file_size": media_info.file_size,
        "media_type": media_info.media_type,
        "caption": message.caption or message.text or "",
        "is_duplicate": 0,
        "is_valid": 1,
        "reactions": {"positive": reaction.positive, "heart": reaction.heart},
        "source_id": source_id,
    }


def row_to_reaction_dict(row: tuple) -> dict[str, Any]:
    """
    将数据库查询结果行转换为反应数据字典

    Args:
        row: (message_id, positive, heart, total) 元组

    Returns:
        包含 positive, heart, total 的字典
    """
    positive = row[1] if row[1] is not None else 0
    heart = row[2] if row[2] is not None else 0
    total = row[3] if len(row) > 3 else positive + heart
    return {
        "message_id": row[0],
        "positive": positive,
        "heart": heart,
        "total": total
    }
