"""清理模块核心逻辑"""
from modules.clean.cli import parse_args
from modules.clean.deduplicate import run_deduplicate
from modules.clean.cleanup import run_deinvalid, run_dejunk
from modules.sync import force_reset_database, sync_channel
from utils.telegram_client import get_config


def print_cleanup_stats(stats_by_type: dict[str, dict[str, int]], dry_run: bool) -> None:
    """打印清理统计信息

    Args:
        stats_by_type: 按类型统计的结果 {"deduplicate": {...}, "deinvalid": {...}, "dejunk": {...}}
        dry_run: 是否为预览模式（-y）
    """
    if not stats_by_type:
        return

    print(f"\n{'='*50}")
    print("[CLEAN] 待清理消息分类统计:")
    print(f"{'='*50}")

    total_all = 0

    type_labels = {
        "deduplicate": "重复媒体",
        "deinvalid": "无效消息",
        "dejunk": "垃圾消息",
    }

    for op_type, stats in stats_by_type.items():
        if not stats:
            continue
        label = type_labels.get(op_type, op_type)
        type_total = sum(stats.values())
        total_all += type_total
        print(f"\n  {label}:")
        for media_type, count in sorted(stats.items()):
            print(f"    {media_type}: {count} 条")
        print(f"    小计: {type_total} 条")

    print(f"\n  总计: {total_all} 条")
    if dry_run:
        print("\n  (以上为预览模式，实际删除请去掉 -y 参数)")


def main() -> None:
    """主执行流程"""
    args = parse_args()

    # 确定要处理的频道列表
    if args.channels:
        channels = args.channels
    else:
        config = get_config()
        channel_id = config.get("channel_id")
        channels = [channel_id] if channel_id else []

    if not channels:
        print("[CLEAN] 错误：未指定频道ID，且配置文件中也未设置频道ID")
        return

    # 有任何清理参数时默认同步
    should_sync = args.d or args.i or args.s

    # dry_run 模式（-y）影响所有删除操作
    dry_run = args.y

    # 收集所有清理类型的统计
    stats_by_type: dict[str, dict[str, int]] = {}

    # 对每个频道分别执行操作
    for channel in channels:
        print(f"\n{'='*50}")
        print(f"[CLEAN] 开始清理频道: {channel}")
        print(f"{'='*50}")

        # 只有在同步或强制重置时才清空数据库
        if should_sync or args.R:
            force_reset_database()
            sync_channel(channel_id=channel)

        # 执行各项清理操作
        if args.d:
            stats = run_deduplicate(delete=not dry_run, channel_id=channel)
            if stats:
                stats_by_type["deduplicate"] = stats
        if args.i:
            stats = run_deinvalid(delete=not dry_run, channel_id=channel)
            if stats:
                stats_by_type["deinvalid"] = stats
        if args.s:
            stats = run_dejunk(delete=not dry_run, channel_id=channel)
            if stats:
                stats_by_type["dejunk"] = stats

        # 无清理参数时，只检测不删除
        if not args.d and not args.i and not args.s:
            dedup_stats = run_deduplicate(delete=False, channel_id=channel)
            invalid_stats = run_deinvalid(delete=False, channel_id=channel)
            junk_stats = run_dejunk(delete=False, channel_id=channel)

            # 汇总统计
            if dedup_stats:
                stats_by_type["deduplicate"] = dedup_stats
            if invalid_stats:
                stats_by_type["deinvalid"] = invalid_stats
            if junk_stats:
                stats_by_type["dejunk"] = junk_stats

    # -y 模式下打印汇总统计
    if dry_run:
        print_cleanup_stats(stats_by_type, dry_run)


if __name__ == "__main__":
    main()