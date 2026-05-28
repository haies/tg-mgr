"""
cleanup模块单元测试

测试 modules/clean/cleanup.py 中的垃圾消息判定逻辑（简化版：只判断媒体大小）
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from modules.clean.cleanup import (
    is_junk_message,
    find_junk_messages,
    JUNK_TINY_MEDIA_GROUP_THRESHOLD,
)


class TestIsJunkMessage:
    """测试 is_junk_message 函数（简化版：只检查媒体大小）"""

    def test_is_junk_message_photo_small_file(self):
        """photo + <100KB → 是垃圾"""
        assert is_junk_message("短文字", file_size=50 * 1024, media_type="photo") is True

    def test_is_junk_message_video_small_file(self):
        """video + <100KB → 是垃圾"""
        assert is_junk_message("任意文字内容", file_size=80 * 1024, media_type="video") is True

    def test_is_junk_message_photo_large_file(self):
        """photo + >=100KB → 不是垃圾"""
        assert is_junk_message("任意文字内容", file_size=100 * 1024, media_type="photo") is False

    def test_is_junk_message_video_large_file(self):
        """video + >=100KB → 不是垃圾"""
        assert is_junk_message("任意文字内容", file_size=200 * 1024, media_type="video") is False

    def test_is_junk_message_document_not_junk(self):
        """document类型不是垃圾（规则只检查photo/video）"""
        assert is_junk_message("任意文字内容", file_size=1024, media_type="document") is False

    def test_is_junk_message_audio_not_junk(self):
        """audio类型不是垃圾"""
        assert is_junk_message("任意文字内容", file_size=1024, media_type="audio") is False

    def test_is_junk_message_no_file_size_small(self):
        """photo + file_size=None（无文件大小）+ <100KB默认处理 → 是垃圾"""
        assert is_junk_message("短文字", file_size=None, media_type="photo") is True

    def test_is_junk_message_exactly_100kb(self):
        """photo + file_size=100KB → 不是垃圾（>=阈值）"""
        assert is_junk_message("任意文字", file_size=100 * 1024, media_type="photo") is False


class TestFindJunkMessages:
    """测试 find_junk_messages 函数"""

    def test_find_junk_messages_empty_db(self, test_db):
        """空数据库返回空列表"""
        results = find_junk_messages(test_db)
        assert results == []

    def test_find_junk_messages_no_filter(self, populated_db):
        """无过滤条件返回媒体垃圾"""
        results = find_junk_messages(populated_db)
        for msg in results:
            msg_id, file_unique_id, file_size, media_type, caption, timestamp = msg
            assert isinstance(msg_id, int)
            assert isinstance(caption, str)

    def test_find_junk_messages_with_channel_id(self, test_db):
        """按channel_id过滤"""
        cursor = test_db.cursor()
        # 插入测试消息（<100KB + photo = 垃圾）
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 50 * 1024, 'photo', '短文字', -1001234567890))
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 50 * 1024, 'photo', '短文字', -1009876543210))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 1  # message_id

    def test_find_junk_messages_photo_video_only(self, test_db):
        """photo和video类型才可能被判定为媒体垃圾"""
        cursor = test_db.cursor()
        # 插入document类型（不应被检测）
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 50 * 1024, 'document', '任意文字', -1001234567890))
        # 插入photo类型 + <100KB → 垃圾
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 50 * 1024, 'photo', '任意文字', -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 2  # photo message

    def test_find_junk_messages_threshold_100kb(self, test_db):
        """文件>=100KB不被判定为垃圾"""
        cursor = test_db.cursor()
        # 插入>=100KB的文件
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 100 * 1024, 'photo', '任意文字', -1001234567890))
        # 插入<100KB的文件
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 50 * 1024, 'photo', '任意文字', -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 2  # message_id

    def test_find_junk_messages_text_type_ignored(self, test_db):
        """纯文字消息不再被检测为垃圾（已移除文字判断）"""
        cursor = test_db.cursor()
        # 插入纯文字消息
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, None, None, 'text', '这是一段中文文字', -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        # 纯文字消息不再被检测
        assert len(results) == 0
