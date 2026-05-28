"""
数据库模块

提供数据库路径管理和连接功能
"""

import sqlite3
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# 将 src/ 添加到 path 以便导入 utils
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# 复用 telegram_client 中的 get_config_dir 函数
from utils.telegram_client import get_config_dir  # noqa: E402

# 重新导出数据库 messages 模块的核心函数（统一模块边界）
from database.messages import (  # noqa: E402
    check_message_restricted,
    init_database,
    get_last_processed_id,
    insert_messages,
    find_duplicates,
    find_invalid_messages,
    get_message_stats,
    get_existing_files,
    update_message_duplicate,
)

# 导出数据库错误类
from database.errors import (  # noqa: E402
    DatabaseError,
    DatabaseConnectionError,
    DatabaseQueryError,
    DatabaseLockError,
)


__all__ = [
    "get_db",
    "get_database_path",
    "get_schema_path",
    "DatabaseError",
    "DatabaseConnectionError",
    "DatabaseQueryError",
    "DatabaseLockError",
]


def get_project_tmp_dir() -> Path:
    """获取项目 tmp 目录路径"""
    return get_config_dir() / "tmp"


def get_database_dir() -> Path:
    """获取数据库目录路径"""
    db_dir = get_project_tmp_dir() / "database"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


def get_database_path() -> Path:
    """获取数据库文件路径"""
    return get_database_dir() / "messages.db"


def get_schema_path() -> Path:
    """获取 schema 文件路径"""
    return Path(__file__).parent / "schema.sql"


def get_db_connection() -> sqlite3.Connection:
    """创建并返回数据库连接"""
    conn = sqlite3.connect(str(get_database_path()))
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    数据库上下文管理器

    使用示例:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM messages")
            ...

    连接会在 with 块退出时自动关闭
    """
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
