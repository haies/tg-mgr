"""清理模块入口"""
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from modules.clean.clean_core import main
from modules.clean.deduplicate import (
    run_deduplicate,
    exponential_backoff,
    delete_message_safely,
)
from modules.clean.cleanup import (
    find_invalid_messages,
    find_junk_messages,
    run_deinvalid,
    run_dejunk,
)
from database.messages import find_duplicates

__all__ = [
    'main',
    'run_deduplicate',
    'run_deinvalid',
    'run_dejunk',
    'find_duplicates',
    'find_invalid_messages',
    'find_junk_messages',
    'exponential_backoff',
    'delete_message_safely',
]