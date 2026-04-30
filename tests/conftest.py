"""
pytest配置文件

提供测试夹具和共享测试设施
"""
import os
import sys
import sqlite3
import tempfile
import shutil
from pathlib import Path

import pytest

# Add project root and src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


@pytest.fixture
def test_db():
    """创建临时测试数据库"""
    temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    db_path = temp_db.name
    temp_db.close()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建 messages 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            file_unique_id TEXT,
            file_size INTEGER,
            media_type TEXT,
            caption TEXT,
            is_duplicate INTEGER DEFAULT 0,
            is_valid INTEGER DEFAULT 1,
            reactions TEXT,
            source_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 创建 channels 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    ''')

    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_unique_id ON messages(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON messages(media_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_valid ON messages(is_valid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_duplicate ON messages(is_duplicate)")

    conn.commit()

    yield conn

    conn.close()
    os.unlink(db_path)


@pytest.fixture
def sample_messages(test_db):
    """向测试数据库添加示例消息"""
    cursor = test_db.cursor()

    messages = [
        # 正常消息
        (1, 'file1', 1024, 'photo', 'Photo 1', 0, 1, '{"positive": 10, "heart": 5}', None),
        (2, 'file2', 2048, 'video', 'Video 1', 0, 1, '{"positive": 50, "heart": 20}', None),
        (3, 'file3', 512, 'document', 'Doc 1', 0, 1, '{"positive": 3, "heart": 1}', None),
        # 高反应消息 (>50)
        (4, 'file4', 4096, 'video', 'Video 2', 0, 1, '{"positive": 100, "heart": 50}', None),
        (5, 'file5', 8192, 'video', 'Video 3', 0, 1, '{"positive": 80, "heart": 30}', None),
        # 重复消息
        (6, 'file1', 1024, 'photo', 'Photo 1 dup', 1, 1, '{"positive": 0, "heart": 0}', None),
        # 无反应消息
        (7, 'file6', 256, 'text', 'Text 1', 0, 1, None, None),
        # 无效消息
        (8, 'file7', 0, 'video', 'Video invalid', 0, 0, '{"positive": 1, "heart": 0}', None),
        # 转发消息
        (9, 'file8', 1024, 'photo', 'Photo forwarded', 0, 1, '{"positive": 5, "heart": 2}', -1001234567890),
        (10, 'file9', 2048, 'video', 'Video forwarded', 0, 1, '{"positive": 15, "heart": 10}', -1001234567890),
    ]

    cursor.executemany('''
        INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_valid, reactions, source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', messages)

    test_db.commit()
    return test_db


@pytest.fixture
def sample_channels(test_db):
    """添加示例频道数据"""
    cursor = test_db.cursor()
    channels = [
        (-1001234567890, 'Source Channel'),
        (-1009876543210, 'Target Channel'),
    ]
    cursor.executemany('INSERT OR REPLACE INTO channels (id, title) VALUES (?, ?)', channels)
    test_db.commit()
    return test_db


@pytest.fixture
def populated_db(sample_messages, sample_channels):
    """同时包含消息和频道的测试数据库"""
    return sample_messages
