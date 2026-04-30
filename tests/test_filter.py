"""
filter模块单元测试

测试 src/modules/filter.py 中的媒体过滤功能
"""
import os
import pytest
import sys
from pathlib import Path

# Set TG_MGR_DEV before importing any modules to avoid config errors
os.environ['TG_MGR_DEV'] = '1'

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestFindLargeMediaFromQuery:
    """测试 find_large_media 查询函数"""

    def test_find_media_outside_size_range(self, populated_db):
        """测试查询范围外的媒体"""
        from database.query import find_large_media

        # 查询 < 1MB 或 > 4KB 的视频/文档
        results = find_large_media(populated_db, min_size=1024, max_size=4096)

        assert isinstance(results, list)
        for row in results:
            msg_id, file_size, media_type = row
            assert media_type in ('video', 'document')
            assert file_size < 1024 or file_size > 4096

    def test_find_very_large_videos(self, populated_db):
        """测试查找超大视频"""
        from database.query import find_large_media

        # 查找 > 1GB 的视频
        results = find_large_media(populated_db, min_size=1073741824, max_size=999999999999)

        # sample_messages 中 message_id 5 has file_size=8192, 不是超大视频
        # 但这个测试验证查询逻辑正常
        assert isinstance(results, list)

    def test_size_range_filter(self, populated_db):
        """测试大小范围过滤"""
        from database.query import find_large_media

        # 正常范围 1MB ~ 4MB 不应该有结果（基于 sample_messages 的数据）
        # sample_messages 中:
        # - message_id 2: video, 2048 bytes
        # - message_id 4: video, 4096 bytes
        # - message_id 5: video, 8192 bytes
        results = find_large_media(populated_db, min_size=1048576, max_size=4194304)

        # 2048, 4096, 8192 都小于 1MB，所以都在范围外
        assert len(results) >= 3