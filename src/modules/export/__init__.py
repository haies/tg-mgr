"""导出模块入口"""
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from modules.export.export_core import (
    ExportState,
    find_existing_export_dir,
    download_media_from_message,
)
from modules.export.cli import parse_export_args
from modules.export.export_core import main

# Re-export get_config for backward compatibility with tests
from utils.telegram_client import get_config

__all__ = [
    "ExportState",
    "parse_export_args",
    "find_existing_export_dir",
    "download_media_from_message",
    "get_config",
    "main",
]