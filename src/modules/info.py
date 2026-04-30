
import logging
from typing import Any, Optional

from pyrogram import errors

from database import get_db
from database.query import find_high_reaction_messages, get_forward_sources
from utils.media import row_to_reaction_dict
from utils.telegram_client import get_client, get_config
from utils.telegram_link import get_channel_address

logger = logging.getLogger(__name__)

def list_all_dialogs() -> list[dict[str, Any]]:
    """获取用户所有会话列表"""
    with get_client('tg-mgr') as client:
        dialogs = []
        try:
            for dialog in client.get_dialogs():
                title = dialog.chat.title or "无名"
                address = get_channel_address(dialog.chat.id)
                dialogs.append({
                    "name": title,
                    "id": dialog.chat.id,
                    "address": address
                })
        except (errors.ChannelPrivate, errors.ChannelInvalid, errors.ChatForbidden):
            print("[INFO] 频道无法访问或无权限")
        except Exception as e:
            print(f"[INFO] 获取会话列表出错: {e}")
        return dialogs

def analyze_channel(channel_id: int, reaction_limit: Optional[int] = None) -> dict[str, Any]:
    """分析指定频道数据"""
    config = get_config()
    forward_limit = config.get('forward_limit', 10)
    reaction_limit = reaction_limit if reaction_limit is not None else config.get('reaction_limit', 10)

    from modules.clean import run_sync as sync_channel
    sync_channel(channel_id=str(channel_id))

    with get_db() as conn:
        cursor = conn.cursor()

        # 预加载所有频道名称到缓存
        cursor.execute('SELECT id, title FROM channels')
        channel_cache = {row[0]: row[1] for row in cursor.fetchall()}

        # 获取转发来源统计
        forwarding_results = get_forward_sources(conn, forward_limit if forward_limit != 0 else 9999)

        forward_sources = []
        missing_ids = []
        for row in forwarding_results:
            if row[0] in channel_cache:
                channel_name = channel_cache[row[0]]
            else:
                channel_name = None
                missing_ids.append(row[0])

            forward_sources.append({
                "name": channel_name,
                "id": row[0],
                "address": get_channel_address(row[0]),
                "count": row[1]
            })

        # 批量获取缺失的频道名称
        for source_id in missing_ids:
            try:
                with get_client('tg-mgr') as client:
                    chat = client.get_chat(source_id)
                    channel_name = chat.title
                    # 保存到channels表
                    cursor.execute('''
                        INSERT OR REPLACE INTO channels (id, title)
                        VALUES (?, ?)
                    ''', (source_id, channel_name))
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

        # 获取高反应消息
        reaction_results = find_high_reaction_messages(conn, min_total=0, limit=reaction_limit)

        reactions = [row_to_reaction_dict(row) for row in reaction_results]

    return {
        "forward_sources": forward_sources,
        "reactions": reactions
    }


def main():
    """主执行流程"""
    import argparse

    parser = argparse.ArgumentParser(description='Telegram 频道信息分析工具')
    parser.add_argument('channel_id', nargs='?', type=int, help='频道ID（可选，不填则列出所有频道）')
    parser.add_argument('reaction_limit', nargs='?', type=int, help='高反应消息数量限制（可选）')

    args = parser.parse_args()

    if args.channel_id is None:
        # 无参数模式
        for dialog in list_all_dialogs():
            print(f"{dialog['name']}\t{dialog['id']}\t{dialog['address']}")
    else:
        # 指定频道ID模式
        result = analyze_channel(args.channel_id, reaction_limit=args.reaction_limit)
        print("\n转发来源TOP:")
        for item in result['forward_sources']:
            print(f"{item['name']}\t{item['id']}\t{item['address']}\t{item['count']}")

        print("\n高反应消息TOP:")
        for item in result['reactions']:
            message_address = f"{get_channel_address(args.channel_id)}/{item['message_id']}"
            print(f"总计: {item['total']}\t{message_address}")


if __name__ == "__main__":
    main()
