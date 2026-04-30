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

from database import get_db_connection
from database.query import find_large_media
from utils.telegram_client import get_config
from utils.telegram_link import generate_tg_link


def main():
    parser = argparse.ArgumentParser(description='Telegram媒体文件过滤工具')
    parser.add_argument('--min-size', type=int, default=1048576,
                        help='最小文件大小(字节)，默认1MB')
    parser.add_argument('--max-size', type=int, default=1073741824,
                        help='最大文件大小(字节)，默认1GB')
    args = parser.parse_args()

    if args.min_size >= args.max_size:
        print("[ERROR] --min-size 必须小于 --max-size")
        return

    config = get_config()
    channel_id = config['channel_id']

    conn = get_db_connection()
    results = find_large_media(conn, args.min_size, args.max_size)
    conn.close()

    if not results:
        print(f"[FILTER] 未找到大小超出范围({args.min_size}~{args.max_size}字节)的媒体")
        return

    # 按媒体类型分组
    grouped = {'video': [], 'document': []}
    for msg_id, size, media_type in results:
        grouped[media_type].append((msg_id, size))

    # 输出结果
    for media_type, items in grouped.items():
        if not items:
            continue

        print(f"\n{media_type.upper()} 文件 ({len(items)} 个):")
        for msg_id, size in items:
            size_mb = size / (1024*1024)
            print(f"  - {size_mb:.2f}MB | {generate_tg_link(channel_id, msg_id)}")

    print(f"\n[FILTER] 过滤完成 (共 {len(results)} 条结果)")

if __name__ == "__main__":
    main()
