"""命令行参数解析"""
import argparse


def add_cli_args(parser: argparse.ArgumentParser) -> None:
    """添加 clean 模块的命令行参数"""
    parser.add_argument("-d", action="store_true", help="去重（检测并删除重复媒体消息）")
    parser.add_argument("-u", action="store_true", help="强制同步消息到数据库（断点续传）")
    parser.add_argument("-i", action="store_true", help="清理无效消息（受限制无法显示的消息）")
    parser.add_argument("-s", action="store_true", help="清理垃圾消息（长文字媒体或推广引流纯文字）")
    parser.add_argument("-y", action="store_true", help="仅列出待删除消息，不实际删除")
    parser.add_argument("-f", action="store_true", help="强制重置数据库（清空后重新同步）")
    parser.add_argument("channels", nargs="*", default=None, help="指定要清理的频道ID（可选）")


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="Telegram 清理工具")
    add_cli_args(parser)
    return parser.parse_args()