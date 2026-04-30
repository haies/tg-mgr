"""
query模块单元测试

测试 database/query.py 中的共享查询函数
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from database.query import (
    find_high_reaction_messages,
    find_reaction_messages_over_threshold,
    find_large_media,
    get_forward_sources,
)


class TestFindHighReactionMessages:
    """测试 find_high_reaction_messages 函数"""

    def test_find_all_reactions(self, populated_db):
        """测试查询所有反应消息(无最小值限制)"""
        results = find_high_reaction_messages(populated_db, min_total=0, limit=10)

        # 应该返回所有有反应的消息(除了null reactions)
        assert len(results) >= 6  # message_id 1,2,3,4,5,9,10 有反应

        # 检查返回格式
        for row in results:
            assert len(row) == 4
            assert isinstance(row[0], int)  # message_id
            assert isinstance(row[3], int)  # total

    def test_find_with_min_total(self, populated_db):
        """测试带最小反应总数限制"""
        results = find_high_reaction_messages(populated_db, min_total=10, limit=10)

        # 应该只返回 total > 10 的消息
        for row in results:
            assert row[3] > 10

    def test_find_top_n(self, populated_db):
        """测试限制返回数量"""
        results = find_high_reaction_messages(populated_db, min_total=0, limit=3)
        assert len(results) <= 3

    def test_find_no_messages(self, populated_db):
        """测试无消息时返回空"""
        # 创建空数据库
        import sqlite3
        temp_db = sqlite3.connect(':memory:')
        temp_db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER,
                reactions TEXT,
                is_valid INTEGER DEFAULT 1
            )
        ''')
        temp_db.commit()

        results = find_high_reaction_messages(temp_db, min_total=0, limit=10)
        assert len(results) == 0

        temp_db.close()


class TestFindReactionMessagesOverThreshold:
    """测试 find_reaction_messages_over_threshold 函数"""

    def test_threshold_50(self, populated_db):
        """测试阈值为50"""
        results = find_reaction_messages_over_threshold(populated_db, threshold=50)

        # 应该返回 total > 50 的消息 (message_id 4: 150, message_id 5: 110)
        assert len(results) >= 2
        for row in results:
            assert row[3] > 50

    def test_threshold_100(self, populated_db):
        """测试阈值为100"""
        results = find_reaction_messages_over_threshold(populated_db, threshold=100)

        # 应该返回 total > 100 的消息
        # message_id 4: 100+50=150 > 100 ✓
        # message_id 5: 80+30=110 > 100 ✓
        assert len(results) == 2
        assert all(row[3] > 100 for row in results)

    def test_with_limit(self, populated_db):
        """测试带limit参数"""
        results = find_reaction_messages_over_threshold(populated_db, threshold=50, limit=1)
        assert len(results) <= 1


class TestFindLargeMedia:
    """测试 find_large_media 函数"""

    def test_find_large_media(self, populated_db):
        """测试查询大文件媒体"""
        results = find_large_media(populated_db, min_size=1024, max_size=4096)

        # 应该返回 file_size < 1KB 或 > 4MB 的视频/文档
        for row in results:
            msg_id, file_size, media_type = row
            assert media_type in ('video', 'document')
            assert file_size < 1024 or file_size > 4096

    def test_default_size_range(self, populated_db):
        """测试默认大小范围(1MB ~ 1GB)"""
        results = find_large_media(populated_db)

        # 默认范围: min_size=1048576, max_size=1073741824
        for row in results:
            file_size = row[1]
            assert file_size < 1048576 or file_size > 1073741824

    def test_no_results(self, populated_db):
        """测试无匹配结果"""
        # 设置一个所有文件都在正常范围内的查询
        results = find_large_media(populated_db, min_size=256, max_size=8192)
        # message_id 7 (text) 不会匹配因为它不是 video/document
        # 结果应该是 file_size < 256 或 > 8192 的 video/document
        for row in results:
            file_size = row[1]
            assert file_size < 256 or file_size > 8192


class TestGetForwardSources:
    """测试 get_forward_sources 函数"""

    def test_get_forward_sources(self, populated_db):
        """测试获取转发来源"""
        results = get_forward_sources(populated_db, limit=10)

        # 应该返回有 source_id 的转发消息统计
        # -1001234567890 有 2 条消息 (message_id 9, 10)
        found = False
        for row in results:
            if row[0] == -1001234567890:
                assert row[1] == 2
                found = True
        assert found, "转发来源 -1001234567890 未找到"

    def test_with_limit(self, populated_db):
        """测试limit参数"""
        results = get_forward_sources(populated_db, limit=1)
        assert len(results) <= 1

    def test_no_forwarded(self, populated_db):
        """测试无转发消息"""
        import sqlite3
        temp_db = sqlite3.connect(':memory:')
        temp_db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER,
                source_id INTEGER,
                is_valid INTEGER DEFAULT 1
            )
        ''')
        temp_db.commit()

        results = get_forward_sources(temp_db, limit=10)
        assert len(results) == 0

        temp_db.close()
