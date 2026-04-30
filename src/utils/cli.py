"""
命令行工具模块

提供共享的 argparse 配置和 CLI 工具函数：
1. 通用参数解析
2. 配置加载辅助函数
"""
import argparse
from typing import Callable

from utils.telegram_client import get_config


def add_config_args(parser: argparse.ArgumentParser) -> None:
    """
    添加通用配置参数到 parser

    Args:
        parser: argparse.ArgumentParser 实例
    """
    parser.add_argument(
        '--api-id',
        type=int,
        help='Telegram API ID（也可通过 TG_API_ID 环境变量设置）'
    )
    parser.add_argument(
        '--api-hash',
        type=str,
        help='Telegram API Hash（也可通过 TG_API_HASH 环境变量设置）'
    )


def get_config_with_overrides(
    api_id: int | None = None,
    api_hash: str | None = None
) -> dict:
    """
    获取配置，支持命令行参数覆盖

    Args:
        api_id: 命令行传入的 API ID（优先于环境变量）
        api_hash: 命令行传入的 API Hash（优先于环境变量）

    Returns:
        配置字典
    """
    config = get_config()

    if api_id is not None:
        config['api_id'] = api_id
    if api_hash is not None:
        config['api_hash'] = api_hash

    return config


def require_config_channel_id(config: dict) -> int:
    """
    确保配置中有 channel_id

    Args:
        config: 配置字典

    Returns:
        channel_id

    Raises:
        ValueError: 如果没有配置 channel_id
    """
    channel_id = config.get('channel_id')
    if not channel_id:
        raise ValueError("未指定目标频道，且config.json中未配置channel_id")
    return channel_id


def parse_channel_ids(channel_args: list[str]) -> list[str]:
    """
    解析频道 ID 列表

    Args:
        channel_args: 原始频道参数列表

    Returns:
        标准化后的频道 ID 列表
    """
    channel_ids = []
    for arg in channel_args:
        # 处理带 -100 前缀的 ID
        if arg.startswith('-100'):
            channel_ids.append(arg)
        # 处理纯数字 ID
        elif arg.lstrip('-').isdigit():
            channel_ids.append(arg)
        # 处理 t.me 链接
        elif 't.me' in arg:
            import re
            match = re.search(r'-?\d+', arg)
            if match:
                channel_ids.append(match.group())
    return channel_ids


class ConfigAction(argparse.Action):
    """argparse Action：从环境变量或默认值获取配置值"""

    def __init__(self, option_strings, dest, config_key, **kwargs):
        self.config_key = config_key
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        config = get_config()
        setattr(namespace, self.dest, config.get(self.config_key))
