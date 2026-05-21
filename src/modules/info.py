import logging
from typing import Any

from pyrogram import errors

from database import get_db
from database.query import (
    find_top_messages,
    get_forward_sources,
)
from utils.telegram_client import DEFAULT_CONFIG, get_client, get_config
from utils.telegram_link import get_channel_address

logger = logging.getLogger(__name__)


def list_all_dialogs() -> list[dict[str, Any]]:
    """获取用户所有会话列表"""
    with get_client("tg-mgr") as client:
        dialogs = []
        try:
            for dialog in client.get_dialogs():
                title = dialog.chat.title or "无名"
                address = get_channel_address(dialog.chat.id)
                dialogs.append({"name": title, "id": dialog.chat.id, "address": address})
        except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
            print("[INFO] 频道无法访问或无权限")
        except Exception as e:
            print(f"[INFO] 获取会话列表出错: {e}")
        return dialogs


def analyze_channel(channel_id: int, reaction_limit: int | None = None, views_limit: int | None = None) -> dict[str, Any]:
    """分析指定频道数据"""
    config = get_config()
    # max_source_channels 优先，兼容旧 forward_limit 配置名
    max_source_channels = config.get("max_source_channels") if config.get("max_source_channels") is not None else (config.get("forward_limit") if config.get("forward_limit") is not None else DEFAULT_CONFIG["max_source_channels"])
    reaction_limit = reaction_limit if reaction_limit is not None else (config.get("reaction_limit") if config.get("reaction_limit") is not None else DEFAULT_CONFIG["reaction_limit"])
    views_limit = views_limit if views_limit is not None else (config.get("views_limit") if config.get("views_limit") is not None else DEFAULT_CONFIG["views_limit"])

    from modules.sync import sync_channel

    sync_channel(channel_id=str(channel_id))

    with get_db() as conn:
        cursor = conn.cursor()

        # 预加载所有频道名称到缓存
        cursor.execute("SELECT id, title FROM channels")
        channel_cache = {row[0]: row[1] for row in cursor.fetchall()}

        # 获取转发来源统计
        forwarding_results = get_forward_sources(
            conn, max_source_channels if max_source_channels != 0 else 9999
        )

        forward_sources = []
        missing_ids = set()
        for row in forwarding_results:
            if row[0] in channel_cache:
                channel_name = channel_cache[row[0]]
            else:
                channel_name = None
                missing_ids.add(row[0])

            forward_sources.append(
                {
                    "name": channel_name,
                    "id": row[0],
                    "address": get_channel_address(row[0]),
                    "count": row[1],
                }
            )

        # 批量获取缺失的频道名称
        for source_id in missing_ids:
            try:
                with get_client("tg-mgr") as client:
                    chat = client.get_chat(source_id)
                    channel_name = chat.title
                    # 保存到channels表
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO channels (id, title)
                        VALUES (?, ?)
                    """,
                        (source_id, channel_name),
                    )
                    conn.commit()
                    # 更新缓存
                    channel_cache[source_id] = channel_name
            except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
                channel_cache[source_id] = "无法获取"
            except Exception as e:
                logger.warning(f"获取频道名称失败: {e}")
                channel_cache[source_id] = "无法获取"

        # 更新 forward_sources 中的名称
        for source in forward_sources:
            if source["name"] is None:
                source["name"] = channel_cache.get(source["id"], "无名")

        # 统一查询高反应+高浏览量TOP消息
        all_top_messages = find_top_messages(conn, reaction_limit=reaction_limit, views_limit=views_limit)

        # 分离高反应和高浏览量消息（保持原有分开展示逻辑）
        reactions = [msg for msg in all_top_messages if msg.get("msg_type") == "high_reaction"]
        top_views = [{"message_id": msg["message_id"], "views": msg.get("views", 0)} for msg in all_top_messages if msg.get("msg_type") == "high_views"]

    return {"forward_sources": forward_sources, "reactions": reactions, "top_views": top_views}


def main():
    """主执行流程"""
    import argparse

    from modules.sync import force_reset_database

    parser = argparse.ArgumentParser(description="Telegram 频道信息分析工具")
    parser.add_argument(
        "channel_id", nargs="?", type=int, help="频道ID（可选，不填则列出所有频道）"
    )
    parser.add_argument(
        "-R", "--reset", action="store_true", help="强制重置数据库并重新同步（获取所有历史消息）"
    )
    parser.add_argument("-l", "--limit", type=int, dest="reaction_limit", help="高反应消息数量限制（可选）")
    parser.add_argument("-v", "--views-limit", type=int, dest="views_limit", help="高浏览量消息数量限制（可选）")

    args = parser.parse_args()

    if args.channel_id is None:
        # 无参数模式
        for dialog in list_all_dialogs():
            print(f"{dialog['name']}\t{dialog['id']}\t{dialog['address']}")
    else:
        # 指定频道ID模式
        # 强制重置模式：删除数据库并重新同步
        if args.reset:
            force_reset_database()

        result = analyze_channel(args.channel_id, reaction_limit=args.reaction_limit, views_limit=args.views_limit)
        print(f"\n转发来源TOP ({len(result['forward_sources'])}):")
        for item in result["forward_sources"]:
            print(f"{item['name']}\t{item['id']}\t{item['address']}\t{item['count']}")

        print(f"\n浏览量TOP ({len(result['top_views'])}):")
        for item in result["top_views"]:
            message_address = f"{get_channel_address(args.channel_id)}/{item['message_id']}"
            print(f"{item['views']}\t{message_address}")

        print(f"\n高反应消息TOP ({len(result['reactions'])}):")
        for item in result["reactions"]:
            message_address = f"{get_channel_address(args.channel_id)}/{item['message_id']}"
            print(f"总计: {item['total']}\t{message_address}")


if __name__ == "__main__":
    main()
