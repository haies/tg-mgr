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
    views: int = 0
    media_group_id: str | None = None  # 媒体组ID，同一媒体组的消息共享相同的 media_group_id
    media_group_size: int = 0  # 媒体组全部媒体大小


@dataclass
class ReactionData:
    """反应数据数据类"""

    total: int  # 全部表情反应的累计值（正向+付费*20）


# 绝对的正向表情（无论什么语境都是好话）
STRONG_POSITIVE = {"👍", "❤️", "🔥", "🎉", "💯", "🤩", "😍", "🙌", "👏", "💪", "🏆", "🥇", "⭐", "🫶", "🫡"}

# 比较温和、理性的正向（赞同、认可、学到了）
MILD_POSITIVE = {"👌", "🤝", "😎", "🤓", "💡", "✨", "🎯"}

# 需要结合频道属性来看的正向（如果是娱乐/搞笑频道，算正向）
HUMOR_POSITIVE = {"😂", "🤣", "💀"}

# 付费反应倍数
PAID_REACTION_MULTIPLIER = 20


def extract_media_info(message: types.Message) -> MediaInfo:
    """
    从 Message 对象提取媒体信息

    Args:
        message: Telegram Message 对象

    Returns:
        MediaInfo 包含 file_unique_id, file_size, media_type, views
    """
    views = getattr(message, "views", 0) or 0

    if message.photo:
        if hasattr(message.photo, "sizes") and message.photo.sizes:
            largest_photo = max(message.photo.sizes, key=lambda p: p.file_size or 0)
            return MediaInfo(
                file_unique_id=largest_photo.file_unique_id,
                file_size=largest_photo.file_size,
                media_type="photo",
                views=views,
                media_group_id=getattr(message, "media_group_id", None),
            )
        return MediaInfo(
            file_unique_id=message.photo.file_unique_id,
            file_size=message.photo.file_size,
            media_type="photo",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.video:
        return MediaInfo(
            file_unique_id=message.video.file_unique_id,
            file_size=message.video.file_size,
            media_type="video",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.document:
        return MediaInfo(
            file_unique_id=message.document.file_unique_id,
            file_size=message.document.file_size,
            media_type="document",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.audio:
        return MediaInfo(
            file_unique_id=message.audio.file_unique_id,
            file_size=message.audio.file_size,
            media_type="audio",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.animation:
        return MediaInfo(
            file_unique_id=message.animation.file_unique_id,
            file_size=message.animation.file_size,
            media_type="animation",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.voice:
        return MediaInfo(
            file_unique_id=message.voice.file_unique_id,
            file_size=message.voice.file_size,
            media_type="voice",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.video_note:
        return MediaInfo(
            file_unique_id=message.video_note.file_unique_id,
            file_size=message.video_note.file_size,
            media_type="video_note",
            views=views,
            media_group_id=getattr(message, "media_group_id", None),
        )
    elif message.text:
        return MediaInfo(file_unique_id="", file_size=None, media_type="text", views=views, media_group_id=getattr(message, "media_group_id", None))
    else:
        return MediaInfo(file_unique_id="", file_size=None, media_type="other", views=views, media_group_id=getattr(message, "media_group_id", None))


def extract_reaction_data(message: types.Message) -> ReactionData:
    """
    从 Message 对象提取反应数据

    统计正向表情（STRONG_POSITIVE + MILD_POSITIVE + HUMOR_POSITIVE），
    付费表情数量 * 20 累计到 total 上。

    Args:
        message: Telegram Message 对象

    Returns:
        ReactionData 包含 total（正向反应累计 + 付费反应*20）
    """
    positive_count = 0

    if hasattr(message, "reactions") and message.reactions:
        for reaction in message.reactions.reactions:  # type: ignore[attr-defined]
            count = reaction.count or 0

            # 检查是否是付费反应（ReactionTypePaid）
            # Pyrogram 2.0.106 中 Reaction 对象没有 type 属性，
            # 通过 emoji 是否为空且 custom_emoji_id 存在来判断付费
            is_paid = getattr(reaction, "paid", False) or (
                reaction.emoji is None and getattr(reaction, "custom_emoji_id", None) is not None
            )

            if is_paid:
                # 付费反应 * 20
                positive_count += count * PAID_REACTION_MULTIPLIER
            elif reaction.emoji in STRONG_POSITIVE | MILD_POSITIVE | HUMOR_POSITIVE:
                positive_count += count

    return ReactionData(total=positive_count)


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
        "is_invalid": 0,
        "reactions": reaction.total,  # 整数类型
        "source_id": source_id,
        "views": media_info.views,
    }


def row_to_reaction_dict(row: tuple) -> dict[str, Any]:
    """
    将数据库查询结果行转换为反应数据字典

    Args:
        row: (message_id, total, source_id, views, media_group_id) 元组（新版整数格式）

    Returns:
        包含 message_id, total, source_id, views, media_group_id 的字典
    """
    total = row[1] if len(row) > 1 and row[1] is not None else 0
    source_id = row[2] if len(row) > 2 else None
    views = row[3] if len(row) > 3 else None
    media_group_id = row[4] if len(row) > 4 else None
    result: dict[str, Any] = {"message_id": row[0], "total": total}
    if source_id is not None:
        result["source_id"] = source_id
    if views is not None and views > 0:
        result["views"] = views
    if media_group_id:
        result["media_group_id"] = media_group_id
    return result
