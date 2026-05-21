"""
视频/文件过滤模块

功能：
1. 查询大于1GB或小于1MB的媒体消息
2. 按媒体类型分类输出结果
3. 生成可点击的Telegram消息链接

使用：
python modules/filter.py --min-size 1048576 --max-size 1073741824

参数：
--min-size: 最小文件大小(字节)，默认1MB
--max-size: 最大文件大小(字节)，默认1GB
"""

import argparse

from database import get_db
from database.query import find_large_media
from utils.telegram_client import DEFAULT_CONFIG, get_config
from utils.telegram_link import generate_tg_link


def main():
    parser = argparse.ArgumentParser(description="Telegram媒体文件过滤工具")
    parser.add_argument("channel", nargs="?", type=int, help="频道ID（可选，不填则使用配置文件中的默认值）")
    parser.add_argument("-m", "--min-size", type=int, default=None, help="最小文件大小(字节)")
    parser.add_argument(
        "-M", "--max-size", type=int, default=None, help="最大文件大小(字节)"
    )
    args = parser.parse_args()

    config = get_config()

    # 从config读取默认值（CLI参数可覆盖）
    default_min_size = config.get("filter_min_size") if config.get("filter_min_size") is not None else DEFAULT_CONFIG["filter_min_size"]
    default_max_size = config.get("filter_max_size") if config.get("filter_max_size") is not None else DEFAULT_CONFIG["filter_max_size"]

    min_size = args.min_size if args.min_size is not None else default_min_size
    max_size = args.max_size if args.max_size is not None else default_max_size

    if min_size >= max_size:
        print("[ERROR] --min-size 必须小于 --max-size")
        return

    # 优先使用CLI参数，其次使用config中的值
    channel_id = args.channel if args.channel is not None else config.get("channel_id")

    if not channel_id:
        print("[ERROR] 未配置默认频道 channel_id")
        return

    with get_db() as conn:
        results = find_large_media(conn, min_size, max_size)

    if not results:
        print(f"[FILTER] 未找到大小超出范围({min_size}~{max_size}字节)的媒体")
        return

    # 按媒体类型分组
    grouped = {"video": [], "document": []}
    for msg_id, size, media_type in results:
        grouped[media_type].append((msg_id, size))

    # 输出结果
    for media_type, items in grouped.items():
        if not items:
            continue

        print(f"\n{media_type.upper()} 文件 ({len(items)} 个):")
        for msg_id, size in items:
            size_mb = size / (1024 * 1024)
            print(f"  - {size_mb:.2f}MB | {generate_tg_link(channel_id, msg_id)}")

    print(f"\n[FILTER] 过滤完成 (共 {len(results)} 条结果)")


if __name__ == "__main__":
    main()
