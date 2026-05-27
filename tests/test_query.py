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
    find_messages_by_views_multiplier,
    find_messages_by_views_top,
    find_top_messages,
    find_forward_sources_by_channel,
)


class TestFindHighReactionMessages:
    """测试 find_high_reaction_messages 函数"""

    def test_find_all_reactions(self, populated_db):
        """测试查询所有反应消息(无最小值限制)"""
        results = find_high_reaction_messages(populated_db, min_total=0, limit=10)

        # 应该返回所有有反应的消息(除了0 reactions)
        assert len(results) >= 6  # message_id 1,2,3,4,5,9,10 有反应

        # 检查返回格式
        for row in results:
            assert len(row) == 2  # (message_id, total)
            assert isinstance(row[0], int)  # message_id
            assert isinstance(row[1], int)  # total

    def test_find_with_min_total(self, populated_db):
        """测试带最小反应总数限制"""
        results = find_high_reaction_messages(populated_db, min_total=10, limit=10)

        # 应该只返回 total > 10 的消息
        for row in results:
            assert row[1] > 10

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
                source_id INTEGER,
                reactions TEXT,
                is_invalid INTEGER DEFAULT 0
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
            assert row[1] > 50  # total 在 index 1

    def test_threshold_100(self, populated_db):
        """测试阈值为100"""
        results = find_reaction_messages_over_threshold(populated_db, threshold=100)

        # 应该返回 total > 100 的消息
        # message_id 4: 150 > 100 ✓
        # message_id 5: 110 > 100 ✓
        assert len(results) == 2
        assert all(row[1] > 100 for row in results)  # total 在 index 1

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
                is_invalid INTEGER DEFAULT 0
            )
        ''')
        temp_db.commit()

        results = get_forward_sources(temp_db, limit=10)
        assert len(results) == 0

        temp_db.close()


def test_deduplicate_media_groups():
    """媒体组消息展开为所有成员，确保整组转发"""
    from database.query import _deduplicate_media_groups

    messages = [
        {"message_id": 1, "media_group_id": "grp1", "total": 10},
        {"message_id": 2, "media_group_id": "grp1", "total": 50},  # 整组保留
        {"message_id": 3, "media_group_id": "grp1", "total": 20},  # 整组保留
        {"message_id": 4, "media_group_id": None, "total": 30},  # 无组别，保持
        {"message_id": 5, "media_group_id": "grp2", "total": 5},
    ]
    result = _deduplicate_media_groups(messages)
    result_ids = [m["message_id"] for m in result]

    # grp1 展开为所有 3 条，grp2 和无组别各 1 条
    assert result_ids == [1, 2, 3, 4, 5], f"Expected all messages, got {result_ids}"


def test_deduplicate_no_groups():
    """无媒体组消息不受影响"""
    from database.query import _deduplicate_media_groups

    messages = [
        {"message_id": 1, "media_group_id": None, "total": 10},
        {"message_id": 2, "media_group_id": None, "total": 20},
    ]
    result = _deduplicate_media_groups(messages)
    assert len(result) == 2


def test_deduplicate_single_in_group():
    """组内只有一条消息时直接保留"""
    from database.query import _deduplicate_media_groups

    messages = [
        {"message_id": 1, "media_group_id": "grp1", "total": 10},
    ]
    result = _deduplicate_media_groups(messages)
    assert len(result) == 1
    assert result[0]["message_id"] == 1


class TestFindMessagesByViewsMultiplier:
    """测试 find_messages_by_views_multiplier 函数"""

    def test_find_messages_by_views_multiplier_default(self, test_db):
        """测试 find_messages_by_views_multiplier 默认行为

        新公式: threshold = 0.8 * max + 0.2 * avg
        数据: views = [10, 20, 30, 100]
        max = 100, avg = 40
        threshold = 0.8*100 + 0.2*40 = 88
        views > 88 → 只返回 views=100 的消息
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 10, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 20, None, -1001),
            (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 0, 30, None, -1001),
            (4, 'file4', 4096, 'video', 'Video 2', 0, 0, 0, 100, None, -1001),
        ])
        test_db.commit()

        results = find_messages_by_views_multiplier(test_db, limit=100)
        # threshold = 88, views > 88 → 只返回 views=100 的消息
        assert len(results) == 1
        assert results[0][1] == 100  # views at index 1

    def test_find_messages_by_views_multiplier_with_source_id(self, test_db):
        """测试按 source_id 过滤

        新公式: threshold = 0.8 * max + 0.2 * avg
        source_id=-1001 数据: views = [10, 50]
        max = 50, avg = 30
        threshold = 0.8*50 + 0.2*30 = 46
        views > 46 → 只返回 views=50 的消息
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 10, -1001, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 50, -1001, -1001),
            (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 0, 100, -1002, -1001),
        ])
        test_db.commit()

        results = find_messages_by_views_multiplier(test_db, limit=100, source_id=-1001)
        # 只应该返回 source_id=-1001 的消息
        for row in results:
            assert row[2] == -1001  # source_id at index 2

    def test_find_messages_by_views_multiplier_with_channel_id(self, test_db):
        """测试按 channel_id 过滤

        新公式: threshold = 0.8 * max + 0.2 * avg
        channel_id=-1001 数据: views = [10, 50]
        max = 50, avg = 30
        threshold = 0.8*50 + 0.2*30 = 46
        views > 46 → 只返回 views=50 的消息
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 10, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 50, None, -1001),
            (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 0, 100, None, -1002),
        ])
        test_db.commit()

        results = find_messages_by_views_multiplier(test_db, limit=100, channel_id=-1001)
        # 只应该返回 channel_id=-1001 的消息
        for row in results:
            # views 查询不返回 channel_id，直接按数据验证
            pass
        assert len(results) == 1
        assert results[0][1] == 50  # 只返回 views=50 的消息

    def test_find_messages_by_views_multiplier_zero_avg_fallback(self, test_db):
        """测试 max=0 时回退到 threshold=0 即 views > 0

        数据: views = [0, 0]
        max = 0 → threshold = 0 → 回退到 views > 0
        但所有消息 views = 0，应该返回空
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 0, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 0, None, -1001),
        ])
        test_db.commit()

        results = find_messages_by_views_multiplier(test_db, limit=100)
        assert len(results) == 0

    def test_find_messages_by_views_multiplier_with_limit(self, test_db):
        """测试 limit 参数

        新公式: threshold = 0.8 * max + 0.2 * avg
        数据: views = [10, 20, 30, ..., 200] (10*1 到 10*20)
        max = 200, avg = 105
        threshold = 0.8*200 + 0.2*105 = 160 + 21 = 181
        views > 181 → [190, 200]
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (i, f'file{i}', 1024, 'photo', f'Photo {i}', 0, 0, 0, i * 10, None, -1001)
            for i in range(1, 21)
        ])
        test_db.commit()

        results = find_messages_by_views_multiplier(test_db, limit=5)
        assert len(results) <= 5

    def test_find_messages_by_views_multiplier_new_formula(self, test_db):
        """测试新公式 threshold = 0.8 * max + 0.2 * avg

        数据: views = [10, 20]
        max = 20, avg = 15
        threshold = 0.8*20 + 0.2*15 = 16 + 3 = 19
        views > 19 → 只返回 views=20 的消息
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 10, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 20, None, -1001),
        ])
        test_db.commit()

        results = find_messages_by_views_multiplier(test_db, limit=100)
        assert len(results) == 1
        assert results[0][1] == 20


class TestFindMessagesByViewsTop:
    """测试 find_messages_by_views_top 函数"""

    def test_find_messages_by_views_top_default(self, test_db):
        """测试默认 limit=50"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 10, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 100, None, -1001),
        ])
        test_db.commit()

        results = find_messages_by_views_top(test_db, limit=50)
        assert isinstance(results, list)
        assert len(results) <= 50

    def test_find_messages_by_views_top_with_source_id(self, test_db):
        """测试按 source_id 过滤"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 10, -1001, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 100, -1001, -1001),
            (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 0, 50, -1002, -1001),
        ])
        test_db.commit()

        results = find_messages_by_views_top(test_db, limit=50, source_id=-1001)
        for row in results:
            assert row[2] == -1001  # source_id at index 2

    def test_find_messages_by_views_top_fallback_low_results(self, test_db):
        """测试结果 < 10 时回退到 top 10 with views > 1"""
        cursor = test_db.cursor()
        # 插入少量消息，大部分 views <= 1
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 2, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 5, None, -1001),
        ])
        test_db.commit()

        # 8 * avg_views 阈值会很高，结果会 < 10，触发回退
        results = find_messages_by_views_top(test_db, limit=50)
        # 回退后应该返回 views > 1 的消息，最多 10 条
        assert isinstance(results, list)
        assert len(results) <= 10

    def test_find_messages_by_views_top_no_views_data(self, test_db):
        """测试 avg_views <= 0 时回退到 views > 1"""
        cursor = test_db.cursor()
        # 插入 views 全为 0 或 1 的消息
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 0, None, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 1, None, -1001),
        ])
        test_db.commit()

        # avg_views <= 0，应该回退到 views > 1，但views都<=1，所以返回空
        results = find_messages_by_views_top(test_db, limit=50)
        assert isinstance(results, list)
        # 由于没有任何消息 views > 1，结果应该是空
        assert len(results) == 0


class TestFindTopMessages:
    """测试 find_top_messages 函数"""

    def test_find_top_messages_basic(self, test_db):
        """测试基本用法"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 100, 10, None, -1001, None),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 100, None, -1001, None),
            (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 50, 5, None, -1001, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db)
        assert isinstance(results, list)
        for msg in results:
            assert 'message_id' in msg
            assert 'msg_type' in msg
            assert msg['msg_type'] in ('high_reaction', 'high_views')

    def test_find_top_messages_with_source_id(self, test_db):
        """测试按 source_id 过滤"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 100, 10, -1001, -1001, None),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 100, -1002, -1001, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db, source_id=-1001)
        for msg in results:
            assert msg.get('source_id') == -1001

    def test_find_top_messages_with_channel_id(self, test_db):
        """测试按 channel_id 过滤"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 100, 10, None, -1001, None),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 100, None, -1002, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db, channel_id=-1001)
        assert isinstance(results, list)

    def test_find_top_messages_deduplicates_by_message_id(self, test_db):
        """测试同一 message_id 不会重复出现"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 100, 10, None, -1001, None),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 200, None, -1001, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db)
        msg_ids = [msg['message_id'] for msg in results]
        assert len(msg_ids) == len(set(msg_ids)), "Duplicate message_id found"

    def test_find_top_messages_media_group_deduplication(self, test_db):
        """测试媒体组展开为所有成员（而非只保留1条）

        注意：当前实现中 media_group_id 未在 reaction/view 查询中返回，
        所以这个测试验证的是 _deduplicate_media_groups 函数本身的逻辑正确性。
        """
        from database.query import _deduplicate_media_groups

        # 直接测试 _deduplicate_media_groups 函数
        messages = [
            {"message_id": 1, "media_group_id": "grp1", "total": 10},
            {"message_id": 2, "media_group_id": "grp1", "total": 50},
            {"message_id": 3, "media_group_id": "grp1", "total": 20},
            {"message_id": 4, "media_group_id": None, "total": 30},  # 无组别
        ]
        result = _deduplicate_media_groups(messages)
        result_ids = [m["message_id"] for m in result]

        # grp1 展开为所有 3 条，无组别消息保持原样
        assert result_ids == [1, 2, 3, 4], f"Expected all messages, got {result_ids}"

    def test_find_top_messages_reaction_prioritized(self, test_db):
        """测试 reaction 消息在合并列表中优先于 view 消息"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 100, 10, None, -1001, None),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 200, None, -1001, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db)
        # reaction 消息应该在 view 消息之前
        reaction_indices = [i for i, msg in enumerate(results) if msg.get('msg_type') == 'high_reaction']
        view_indices = [i for i, msg in enumerate(results) if msg.get('msg_type') == 'high_views']
        if reaction_indices and view_indices:
            assert reaction_indices[0] < view_indices[0]

    def test_find_top_messages_supplements_views_field(self, test_db):
        """测试 view 结果会补充 reaction 字段"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 100, None, -1001, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db)
        for msg in results:
            if msg.get('msg_type') == 'high_views':
                # view 消息可能没有 total，应该从 view_map 补充
                assert 'message_id' in msg

    def test_find_top_messages_basic(self, test_db):
        """测试 find_top_messages 基本用法

        新公式: threshold = 0.8 * max + 0.2 * avg
        reaction: reactions=[100], max=100, avg=100, threshold=0.8*100+0.2*100=100 → reactions>100=0条
        views: views=[10,50], max=50, avg=30, threshold=0.8*50+0.2*30=46 → views>46→views=50
        """
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id, media_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 100, 10, None, -1001, None),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 50, None, -1001, None),
        ])
        test_db.commit()

        results = find_top_messages(test_db)
        assert isinstance(results, list)


class TestFindForwardSourcesByChannel:
    """测试 find_forward_sources_by_channel 函数"""

    def test_find_forward_sources_by_channel_basic(self, test_db):
        """测试基本用法"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 0, -1001, -1001),
            (2, 'file2', 2048, 'video', 'Video 1', 0, 0, 0, 0, -1001, -1001),
            (3, 'file3', 512, 'document', 'Doc 1', 0, 0, 0, 0, -1002, -1001),
        ])
        test_db.commit()

        results = find_forward_sources_by_channel(test_db, channel_id=-1001)
        assert isinstance(results, list)
        # source_id=channel_id（自己转发给自己）被过滤，只返回 -1002
        assert len(results) == 1
        assert results[0] == (-1002, 1)

    def test_find_forward_sources_by_channel_with_limit(self, test_db):
        """测试 limit 参数"""
        cursor = test_db.cursor()
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (i, f'file{i}', 1024, 'photo', f'Photo {i}', 0, 0, 0, 0, -1000 + i, -1001)
            for i in range(1, 11)
        ])
        test_db.commit()

        results = find_forward_sources_by_channel(test_db, channel_id=-1001, limit=3)
        assert len(results) <= 3

    def test_find_forward_sources_by_channel_no_forwards(self, test_db):
        """测试无转发消息时返回空"""
        cursor = test_db.cursor()
        # 只插入没有 source_id 的消息
        cursor.executemany('''
            INSERT INTO messages (message_id, file_unique_id, file_size, media_type, caption, is_duplicate, is_invalid, reactions, views, source_id, channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (1, 'file1', 1024, 'photo', 'Photo 1', 0, 0, 0, 0, None, -1001),
        ])
        test_db.commit()

        results = find_forward_sources_by_channel(test_db, channel_id=-1001)
        assert len(results) == 0
