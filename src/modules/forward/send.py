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
from utils.telegram_link import generate_tg_link

# 向后兼容：get_channel_address 委托给 telegram_link
def get_channel_address(channel_id: int) -> str:
    """获取频道链接地址，委托给 telegram_link.generate_tg_link"""
    return generate_tg_link(channel_id, 0).rsplit("/", 1)[0]

logger = logging.getLogger(__name__)

# get_channel_address 已移除 - 使用 telegram_link.get_channel_address


def _get_reaction_total(message: Message) -> int:
    """从消息中提取反应总数（委托给 utils.media.extract_reaction_data）"""
    from utils.media import extract_reaction_data
    return extract_reaction_data(message).total




def _build_stats_str(total: int, views: int, size_mb: float = 0, is_media_group: bool = False) -> str:
    """构建统计信息字符串

    Args:
        total: 反应总数
        views: 浏览量
        size_mb: 媒体大小（MB）
        is_media_group: 是否为媒体组

    Returns:
        格式如 "(反应:5, 浏览:1234, 2.3MB，媒体组)" 或 "(浏览:1234)"
    """
    parts = []
    if total > 0:
        parts.append(f"反应:{total}")
    if views > 0:
        parts.append(f"浏览:{views}")
    if size_mb > 0:
        parts.append(f"{size_mb:.1f}MB")
    if is_media_group:
        parts.append("媒体组")
    if parts:
        return f" ({', '.join(parts)})"
    return ""


def _get_media_group_member_ids(conn, channel_id: int, media_group_id: str) -> list[int]:
    """从数据库获取媒体组所有成员的 message_id

    Args:
        conn: 数据库连接
        channel_id: 频道ID
        media_group_id: 媒体组ID

    Returns:
        媒体组成员 message_id 列表（按 message_id 排序）
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT message_id FROM messages WHERE media_group_id = ? AND channel_id = ? ORDER BY message_id",
        (media_group_id, channel_id)
    )
    return [row[0] for row in cursor.fetchall()]


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
    """获取媒体组的所有消息

    策略：单向向后搜索（offset_id=X 返回 id <= X，即比 X 更旧的消息）
    由于 Telegram API 不支持"查找比 X 新的消息"，我们从 center_msg_id 向后搜索更旧的媒体组消息。

    Args:
        client: Telegram 客户端
        channel_id: 频道ID
        media_group_id: 媒体组ID
        center_msg_id: 中心消息ID，用于定位媒体组

    Returns:
        媒体组消息列表（按 message_id 排序）
    """
    try:
        client.get_chat(channel_id)  # type: ignore[unused-coroutine]

        messages = []
        seen_ids: set[int] = set()

        # 收集函数
        def collect(msgs: list) -> None:
            for msg in msgs:
                if msg.media_group_id == media_group_id and msg.id not in seen_ids:  # type: ignore[attr-defined]
                    messages.append(msg)
                    seen_ids.add(msg.id)  # type: ignore[attr-defined]

        # 获取中心消息
        if center_msg_id:
            try:
                center_msg = client.get_messages(channel_id, center_msg_id)  # type: ignore[union-attr]
                if center_msg and str(center_msg.media_group_id) == str(media_group_id):  # type: ignore[attr-defined]
                    messages.append(center_msg)
                    seen_ids.add(center_msg.id)  # type: ignore[attr-defined]
            except Exception:
                pass

        # 单向向后搜索：offset_id=X 返回 id <= X（比 X 更旧的消息）
        # 使用 center_msg_id 作为起点，向后搜索更旧的媒体组消息
        if center_msg_id:
            # 搜索比 center_msg_id 更旧的消息
            # offset_id=X 返回 id <= X，所以用 center_msg_id 获取包含 center 的所有更旧消息
            for msg in client.get_chat_history(channel_id, limit=200, offset_id=center_msg_id):  # type: ignore[union-attr]
                if msg.media_group_id == media_group_id and msg.id not in seen_ids:  # type: ignore[attr-defined]
                    messages.append(msg)
                    seen_ids.add(msg.id)  # type: ignore[attr-defined]
                    if len(messages) >= 20:
                        break
        else:
            # 无 center_msg_id：获取最近的消息
            for msg in client.get_chat_history(channel_id, limit=200):  # type: ignore[union-attr]
                if msg.media_group_id == media_group_id and msg.id not in seen_ids:  # type: ignore[attr-defined]
                    messages.append(msg)
                    seen_ids.add(msg.id)  # type: ignore[attr-defined]
                    if len(messages) >= 20:
                        break

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

    # 收集所有有效媒体（排除纯文本消息）
    media_list: list[InputMedia] = []
    text_messages: list[Message] = []
    for msg in messages:
        media_input = _prepare_media_for_send(msg)
        if media_input:
            media_list.append(media_input)
        elif msg.text:
            # 纯文本消息单独处理，不是媒体组的一部分
            text_messages.append(msg)

    if not media_list:
        # 只有纯文本消息，降级为 copy_message
        return False

    # 检查媒体组完整性：实际媒体数量 >= 2 才认为是有效媒体组
    if len(media_list) < 2:
        logger.debug(f"媒体组不完整（仅 {len(media_list)} 条媒体），降级为 copy_message")
        return False

    try:
        client.send_media_group(target_channel_id, media_list)  # type: ignore[unused-coroutine, arg-type]
        logger.info(f"媒体组转发成功: {len(media_list)} 条媒体")
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

    # 已处理的媒体组（避免同一媒体组在批量中重复发送）
    processed_groups: set[str] = set()

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
            # 优先使用数据库的 media_group_id 字段
            db_media_group_id = msg.get("media_group_id")
            msg["is_media_group"] = bool(db_media_group_id)

            # 媒体组：只处理一次（第一条消息），同组后续消息跳过
            if db_media_group_id and db_media_group_id in processed_groups:
                skipped += 1
                continue

            link = f"{generate_tg_link(source_channel_id, msg_id)}"

            # 获取文件大小用于进度显示（优先使用 DB 中预填充的值）
            file_size = msg.get("file_size", 0)
            size_mb = file_size / 1024 / 1024 if file_size else 0

            for target_id in target_channel_ids:
                if check_exists:
                    if message_exists_in_channel(client, target_id, msg_id):
                        print(f"[FORWARD] 跳过（已存在）: {link} -> {target_id}")
                        skipped += 1
                        continue

                try:
                    # 使用 db_media_group_id 判断媒体组
                    if db_media_group_id:
                        # 从数据库获取媒体组所有成员
                        with get_db() as conn:
                            member_ids = _get_media_group_member_ids(conn, source_channel_id, db_media_group_id)

                        if len(member_ids) >= 2:
                            # 媒体组（>=2条）：获取完整的 Message 对象，使用 send_media_group 保持结构
                            try:
                                # 批量获取媒体组的所有消息对象
                                media_group_msgs = client.get_messages(source_channel_id, member_ids)  # type: ignore[union-attr]
                                media_group_msgs = [m for m in media_group_msgs if m]  # type: ignore[assignment]

                                # 使用 send_media_group 保持媒体组结构
                                if _forward_media_group(client, source_channel_id, target_id, media_group_msgs, force):
                                    forwarded += 1
                                    total = msg.get("total", 0)
                                    views = msg.get("views", 0)
                                    stats = _build_stats_str(total, views, size_mb, is_media_group=True)
                                    print(f"[FORWARD] 转发成功: {link} -> {target_id}{stats}")
                                else:
                                    # send_media_group 失败，回退到 copy_message 逐条转发
                                    for mid in member_ids:
                                        client.copy_message(
                                            chat_id=target_id,
                                            from_chat_id=source_channel_id,
                                            message_id=mid,
                                        )
                                        time.sleep(0.3)
                                    forwarded += 1
                                    total = msg.get("total", 0)
                                    views = msg.get("views", 0)
                                    stats = _build_stats_str(total, views, size_mb, is_media_group=True)
                                    print(f"[FORWARD] 转发成功: {link} -> {target_id}{stats}")
                            except Exception as e:
                                # API 获取失败，回退到 copy_message 逐条转发
                                logger.debug(f"获取媒体组消息失败: {e}")
                                for mid in member_ids:
                                    client.copy_message(
                                        chat_id=target_id,
                                        from_chat_id=source_channel_id,
                                        message_id=mid,
                                    )
                                    time.sleep(0.3)
                                forwarded += 1
                                total = msg.get("total", 0)
                                views = msg.get("views", 0)
                                stats = _build_stats_str(total, views, size_mb, is_media_group=True)
                                print(f"[FORWARD] 转发成功: {link} -> {target_id}{stats}")
                        else:
                            # 媒体组只有1条，降级为普通转发
                            client.copy_message(
                                chat_id=target_id,
                                from_chat_id=source_channel_id,
                                message_id=msg_id,
                            )
                            forwarded += 1
                            total = msg.get("total", 0)
                            views = msg.get("views", 0)
                            stats = _build_stats_str(total, views, size_mb, is_media_group=True)
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
                        stats = _build_stats_str(total, views, size_mb)
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
                    err_msg = str(e)
                    if "CHAT_ADMIN_REQUIRED" in err_msg:
                        print(f"[FORWARD] 频道 {target_id} 需要管理员权限")
                        break
                    if "CHAT_FORWARDS_RESTRICTED" in err_msg:
                        if force:
                            # force 模式：下载后重新上传（保持媒体组结构）
                            try:
                                # 使用 db_media_group_id 从数据库获取媒体组所有成员
                                if db_media_group_id:
                                    with get_db() as conn:
                                        member_ids = _get_media_group_member_ids(conn, source_channel_id, db_media_group_id)
                                    print(f"[FORWARD] 下载完成: {link} ({len(member_ids)} 条)")
                                    if member_ids and _force_send_media_group(client, target_id, member_ids, source_channel_id=source_channel_id):
                                        forwarded += 1
                                        total = msg.get("total", 0)
                                        views = msg.get("views", 0)
                                        stats = _build_stats_str(total, views, size_mb, is_media_group=True)
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
                                        stats = _build_stats_str(total, views, size_mb)
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
                    if "Empty messages cannot be copied" in err_msg:
                        print(f"[FORWARD] 跳过空消息: {link}")
                        skipped += 1
                        continue
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

            # 媒体组处理完成后记录（无论成功失败，避免重复处理）
            if db_media_group_id:
                processed_groups.add(db_media_group_id)

    return forwarded, skipped, failed
