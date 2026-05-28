"""
cleanup模块单元测试

测试 modules/clean/cleanup.py 中的垃圾消息判定逻辑（复合规则）：
1. 单媒体（photo/video）+ 文件<100KB（不看文字）
2. 媒体消息（photo/video）+ 长文字（≥30中文 或 ≥100字符）+ 文件<2MB + 非媒体组
3. 纯文字消息（去除链接后非空）
4. 媒体组总大小<100KB
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from modules.clean.cleanup import (
    is_junk_message,
    is_spam_text,
    is_tiny_media_junk,
    find_junk_messages,
    JUNK_TINY_MEDIA_GROUP_THRESHOLD,
    JUNK_MEDIA_SIZE_THRESHOLD,
    count_chinese_chars,
)


class TestCountChineseChars:
    """测试中文字符统计"""

    def test_count_chinese_chars(self):
        assert count_chinese_chars("你好世界") == 4
        assert count_chinese_chars("hello") == 0
        assert count_chinese_chars("hello你好world") == 2


class TestIsTinyMediaJunk:
    """测试 is_tiny_media_junk 函数（简化版：只看文件大小）"""

    def test_is_tiny_media_junk_photo_small(self):
        """photo + <100KB → 是垃圾"""
        assert is_tiny_media_junk(50 * 1024, "photo") is True

    def test_is_tiny_media_junk_video_small(self):
        """video + <100KB → 是垃圾"""
        assert is_tiny_media_junk(80 * 1024, "video") is True

    def test_is_tiny_media_junk_photo_large(self):
        """photo + >=180KB → 不是垃圾"""
        assert is_tiny_media_junk(180 * 1024, "photo") is False

    def test_is_tiny_media_junk_video_large(self):
        """video + >=100KB → 不是垃圾"""
        assert is_tiny_media_junk(200 * 1024, "video") is False

    def test_is_tiny_media_junk_document(self):
        """document类型 → 不是垃圾"""
        assert is_tiny_media_junk(1024, "document") is False

    def test_is_tiny_media_junk_audio(self):
        """audio类型 → 不是垃圾"""
        assert is_tiny_media_junk(1024, "audio") is False

    def test_is_tiny_media_junk_no_file_size(self):
        """photo + file_size=None → 是垃圾（无大小信息默认通过）"""
        assert is_tiny_media_junk(None, "photo") is True

    def test_is_tiny_media_junk_exactly_180kb(self):
        """photo + file_size=180KB → 不是垃圾（>=阈值）"""
        assert is_tiny_media_junk(180 * 1024, "photo") is False


class TestIsJunkMessage:
    """测试 is_junk_message 函数（旧版规则：长文字+小文件）"""

    def test_is_junk_message_photo_long_chinese(self):
        """photo + ≥30中文字 + <2MB → 是垃圾"""
        text = "这" * 30  # 30个中文字
        assert is_junk_message(text, file_size=1024 * 1024, media_type="photo") is True

    def test_is_junk_message_video_long_chinese(self):
        """video + ≥30中文字 + <2MB → 是垃圾"""
        text = "测试" * 20  # 40个中文字
        assert is_junk_message(text, file_size=512 * 1024, media_type="video") is True

    def test_is_junk_message_photo_long_english(self):
        """photo + ≥100字符英文 + <2MB → 是垃圾"""
        text = "a" * 100
        assert is_junk_message(text, file_size=1024 * 1024, media_type="photo") is True

    def test_is_junk_message_photo_short_text(self):
        """photo + 文字<30中文字且<100字符 → 不是垃圾"""
        assert is_junk_message("短文字", file_size=1024, media_type="photo") is False

    def test_is_junk_message_photo_large_file(self):
        """photo + ≥2MB → 不是垃圾"""
        text = "这" * 30
        assert is_junk_message(text, file_size=2 * 1024 * 1024, media_type="photo") is False

    def test_is_junk_message_document_not_junk(self):
        """document类型不是垃圾"""
        text = "这" * 30
        assert is_junk_message(text, file_size=1024, media_type="document") is False

    def test_is_junk_message_audio_not_junk(self):
        """audio类型不是垃圾"""
        text = "这" * 30
        assert is_junk_message(text, file_size=1024, media_type="audio") is False

    def test_is_junk_message_no_text(self):
        """无文字 → 不是垃圾"""
        assert is_junk_message("", file_size=1024, media_type="photo") is False
        assert is_junk_message(None, file_size=1024, media_type="photo") is False

    def test_is_junk_message_links_only(self):
        """仅包含链接的文字 → 不是垃圾"""
        text = "https://t.me/c/123/456 https://t.me/abc/789"
        assert is_junk_message(text, file_size=1024, media_type="photo") is False

    def test_is_junk_message_mixed_links_and_text(self):
        """链接+长文字 → 是垃圾"""
        text = "https://t.me/c/123/456 " + "这" * 30
        assert is_junk_message(text, file_size=1024, media_type="photo") is True

    def test_is_junk_message_exactly_2mb(self):
        """photo + file_size=2MB → 不是垃圾（>=阈值）"""
        text = "这" * 30
        assert is_junk_message(text, file_size=2 * 1024 * 1024, media_type="photo") is False

    def test_is_junk_message_no_file_size(self):
        """photo + file_size=None + 长文字 → 是垃圾（无大小信息默认通过）"""
        text = "这" * 30
        assert is_junk_message(text, file_size=None, media_type="photo") is True


class TestIsSpamText:
    """测试 is_spam_text 函数"""

    def test_is_spam_text_normal_text(self):
        """普通文字 → 是垃圾"""
        assert is_spam_text("这是一段文字") is True

    def test_spam_text_empty(self):
        """空文字 → 不是垃圾"""
        assert is_spam_text("") is False
        assert is_spam_text(None) is False

    def test_is_spam_text_links_only(self):
        """仅包含链接 → 不是垃圾"""
        text = "https://t.me/c/123/456"
        assert is_spam_text(text) is False

    def test_is_spam_text_mixed(self):
        """链接+文字 → 是垃圾"""
        text = "https://t.me/c/123 真实内容"
        assert is_spam_text(text) is True


class TestFindJunkMessages:
    """测试 find_junk_messages 函数"""

    def test_find_junk_messages_empty_db(self, test_db):
        """空数据库返回空列表"""
        results = find_junk_messages(test_db)
        assert results == []

    def test_find_junk_messages_type1_tiny_media(self, test_db):
        """类型1：photo/video + <100KB（不看文字）"""
        cursor = test_db.cursor()
        # 小图 + 无文字 → 类型1垃圾
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 50 * 1024, 'photo', None, -1001234567890))
        # 小图 + 短文字 → 类型1垃圾 (>=180KB不算类型1)
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 150 * 1024, 'video', '短', -1001234567890))
        # 大图 + 长文字 → 类型2（is_junk_message判定）
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (3, 'file3', 2 * 1024 * 1024, 'photo', '这' * 30, -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 2
        result_ids = [r[0] for r in results]
        assert 1 in result_ids  # 小图+无文字
        assert 2 in result_ids  # 小图+短文字

    def test_find_junk_messages_type2_long_text_small_file(self, test_db):
        """类型2：photo/video + 长文字 + <2MB + 非媒体组"""
        cursor = test_db.cursor()
        # 长文字 + 小文件 → 类型2垃圾
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 1024 * 1024, 'photo', '这' * 30, -1001234567890))
        # 长文字但文件>=2MB → 不是类型2
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 2 * 1024 * 1024, 'photo', '这' * 30, -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 1

    def test_find_junk_messages_type2_excludes_media_group(self, test_db):
        """类型2：媒体组成员不参与判定（走类型4媒体组判断）"""
        cursor = test_db.cursor()
        # 媒体组消息 + 长文字 + 小文件 → 不算类型2
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 1024 * 1024, 'photo', '这' * 30, -1001234567890, 'group_abc'))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        # 类型1不匹配（file_size=1MB > 100KB），类型2被排除，类型4需要多个媒体
        type1_results = [r for r in results if len(r) == 6 and r[0] == 1]
        assert len(type1_results) == 0

    def test_find_junk_messages_type3_text_spam(self, test_db):
        """类型3：纯文字消息"""
        cursor = test_db.cursor()
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, None, None, 'text', '这是一段中文文字', -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 1

    def test_find_junk_messages_photo_video_only(self, test_db):
        """只有photo和video参与类型1判定"""
        cursor = test_db.cursor()
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 50 * 1024, 'document', '任意文字', -1001234567890))
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 50 * 1024, 'photo', '任意文字', -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 2

    def test_find_junk_messages_large_file_threshold(self, test_db):
        """类型1：文件>=180KB不被判定"""
        cursor = test_db.cursor()
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (1, 'file1', 180 * 1024, 'photo', '任意文字', -1001234567890))
        cursor.execute('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, channel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (2, 'file2', 150 * 1024, 'photo', '任意文字', -1001234567890))
        test_db.commit()

        results = find_junk_messages(test_db, channel_id=-1001234567890)
        assert len(results) == 1
        assert results[0][0] == 2