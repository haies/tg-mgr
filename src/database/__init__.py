"""
数据库模块

提供数据库路径管理和连接功能
"""
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

# 将 src/ 添加到 path 以便导入 utils
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# 复用 telegram_client 中的 get_config_dir 函数
from utils.telegram_client import get_config_dir  # noqa: E402


def get_project_tmp_dir() -> Path:
    """获取项目 tmp 目录路径"""
    return get_config_dir() / 'tmp'


def get_database_dir() -> Path:
    """获取数据库目录路径"""
    db_dir = get_project_tmp_dir() / 'database'
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


def get_database_path() -> Path:
    """获取数据库文件路径"""
    return get_database_dir() / 'messages.db'


def get_schema_path() -> Path:
    """获取 schema 文件路径"""
    return Path(__file__).parent / 'schema.sql'


def get_db_connection() -> sqlite3.Connection:
    """创建并返回数据库连接"""
    return sqlite3.connect(str(get_database_path()))


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
