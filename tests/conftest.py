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
            is_invalid INTEGER DEFAULT 0,
            is_junk INTEGER DEFAULT 0,
            source_id INTEGER,
            views INTEGER DEFAULT 0,
            reactions INTEGER DEFAULT 0,
            media_group_id TEXT,
            channel_id INTEGER,
            media_group_size INTEGER DEFAULT 0,
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_invalid ON messages(is_invalid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_duplicate ON messages(is_duplicate)")

    conn.commit()

    yield conn

    conn.close()
    os.unlink(db_path)


@pytest.fixture
def sample_messages(test_db):
    """向测试数据库添加示例消息"""
    cursor = test_db.cursor()

    # 新版 reactions 是 INTEGER（正向表情累计 + 付费*20）
    # 旧版 JSON: {"positive": X, "heart": Y} -> total = X + Y
    messages = [
        # 正常消息
        (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 15, None),       # 10+5
        (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 70, None),       # 50+20
        (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 4, None),        # 3+1
        # 高反应消息 (>50)
        (4, 'file4', 4096, 'video', 'Video 2', 0, 0, 150, None),      # 100+50
        (5, 'file5', 8192, 'video', 'Video 3', 0, 0, 110, None),      # 80+30
        # 重复消息
        (6, 'file1', 1024, 'photo', 'Photo 1 dup', 1, 0, 0, None),
        # 无反应消息
        (7, 'file6', 256, 'text', 'Text 1', 0, 0, 0, None),
        # 无效消息
        (8, 'file7', 0, 'video', 'Video invalid', 0, 1, 1, None),
        # 转发消息
        (9, 'file8', 1024, 'photo', 'Photo forwarded', 0, 0, 7, -1001234567890),   # 5+2
        (10, 'file9', 2048, 'video', 'Video forwarded', 0, 0, 25, -1001234567890),  # 15+10
    ]

    cursor.executemany('''
        INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, source_id)
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
