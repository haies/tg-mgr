"""转发发送模块 - 普通转发（copy_message API）"""
import logging
import time
from typing import Any

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

from modules.forward import forward_core
from database import get_db
from utils.telegram_client import get_log_path

logger = logging.getLogger(__name__)

# get_channel_address 已移除 - 使用 telegram_link.get_channel_address


def _get_reaction_total(message: Message) -> int:
    """从消息中提取反应总数（委托给 utils.media.extract_reaction_data）"""
    from utils.media import extract_reaction_data
    return extract_reaction_data(message).total


def get_channel_address(channel_id: int) -> str:
    """获取频道链接地址，与 telegram_link.py 保持一致"""
    return f"https://t.me/c/{abs(channel_id)}"


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
    # 导入 force 模块内的函数，避免循环导入
    from modules.forward.force import _force_send_media_group

    log_file = get_log_path("forward.log")
    write_date = not log_file.exists()

    forwarded = 0
    skipped = 0
    failed = 0

    with forward_core.get_client("tg-mgr") as client:
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

            # 获取文件大小用于进度显示（优先使用 DB 中预填充的值，避免依赖 Telegram API）
            file_size = msg.get("file_size", 0)
            if not file_size and original_msg:
                try:
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
            msg.get("is_media_group", False)

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
                            if size_mb > 0:
                                print(f"[UPLOAD] 媒体组: {link} | {size_mb:.1f}MB")
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
                                    print(f"[DOWNLOAD] 下载完成: {link} | {size_mb:.1f}MB (媒体组 {len(media_group_msgs)} 条)")
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
