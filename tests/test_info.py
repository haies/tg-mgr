"""
info模块单元测试

测试 src/modules/info.py 中的频道信息功能
"""
import os
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Set TG_MGR_DEV before importing any modules to avoid config errors
os.environ['TG_MGR_DEV'] = '1'

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestGetChannelAddress:
    """测试 get_channel_address 函数"""

    def test_channel_address_with_100_prefix(self):
        """测试带 -100 前缀的频道地址"""
        from modules.info import get_channel_address

        address = get_channel_address(-1001234567890)
        assert 't.me/c/' in address

    def test_channel_address_without_100_prefix(self):
        """测试不带 -100 前缀的频道地址"""
        from modules.info import get_channel_address

        address = get_channel_address(-1234567890)
        assert 't.me/c/' in address


class TestListAllDialogs:
    """测试 list_all_dialogs 函数"""

    def test_list_all_dialogs_returns_list(self):
        """测试返回对话列表"""
        from modules.info import list_all_dialogs

        mock_client = MagicMock()
        mock_dialog = MagicMock()
        mock_dialog.chat.id = -1001234567890
        mock_dialog.chat.title = "Test Channel"

        mock_client.get_dialogs.return_value = [mock_dialog]

        with patch('modules.info.get_client') as mock_get_client:
            mock_get_client.return_value.__enter__.return_value = mock_client
            mock_get_client.return_value.__exit__.return_value = None

            result = list_all_dialogs()

        assert isinstance(result, list)

    def test_dialog_structure(self):
        """测试对话结构包含必要字段"""
        from modules.info import list_all_dialogs

        mock_client = MagicMock()
        mock_dialog = MagicMock()
        mock_dialog.chat.id = -1001234567890
        mock_dialog.chat.title = "Test Channel"

        mock_client.get_dialogs.return_value = [mock_dialog]

        with patch('modules.info.get_client') as mock_get_client:
            mock_get_client.return_value.__enter__.return_value = mock_client
            mock_get_client.return_value.__exit__.return_value = None

            result = list_all_dialogs()

        if result:
            assert 'name' in result[0]
            assert 'id' in result[0]
            assert 'address' in result[0]


class TestAnalyzeChannel:
    """测试 analyze_channel 函数"""

    def test_analyze_channel_returns_dict(self):
        """测试分析频道返回字典"""
        from modules.info import analyze_channel

        with patch('modules.info.get_config') as mock_config, \
             patch('modules.sync.sync_channel') as mock_sync, \
             patch('modules.info.get_db') as mock_get_db, \
             patch('modules.info.find_forward_sources_by_channel') as mock_sources, \
             patch('modules.info.find_top_messages') as mock_top:

            mock_config.return_value = {
                'forward_limit': 10,
                'reaction_limit': 10
            }

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = (0,)

            mock_sources.return_value = []
            mock_top.return_value = []

            result = analyze_channel(-1001234567890)

        assert isinstance(result, dict)
        assert 'forward_sources' in result
        assert 'reactions' in result

    def test_analyze_channel_forward_sources_structure(self):
        """测试转发来源结构"""
        from modules.info import analyze_channel

        with patch('modules.info.get_config') as mock_config, \
             patch('modules.sync.sync_channel') as mock_sync, \
             patch('modules.info.get_db') as mock_get_db, \
             patch('modules.info.find_forward_sources_by_channel') as mock_sources, \
             patch('modules.info.find_top_messages') as mock_top:

            mock_config.return_value = {
                'forward_limit': 10,
                'reaction_limit': 10
            }

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = (0,)

            # Mock return: [(source_id, count)]
            mock_sources.return_value = [(-1001234567890, 5)]
            mock_top.return_value = []

            result = analyze_channel(-1001234567890)

        assert len(result['forward_sources']) == 1
        source = result['forward_sources'][0]
        assert 'name' in source
        assert 'id' in source
        assert 'address' in source
        assert 'count' in source

    def test_analyze_channel_reactions_structure(self):
        """测试高反应消息结构"""
        from modules.info import analyze_channel

        with patch('modules.info.get_config') as mock_config, \
             patch('modules.sync.sync_channel') as mock_sync, \
             patch('modules.info.get_db') as mock_get_db, \
             patch('modules.info.find_forward_sources_by_channel') as mock_sources, \
             patch('modules.info.find_top_messages') as mock_top:

            mock_config.return_value = {
                'forward_limit': 10,
                'reaction_limit': 10
            }

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = (0,)

            mock_sources.return_value = []
            # Mock return: high reaction messages
            mock_top.return_value = [{"message_id": 123, "positive": 50, "heart": 30, "total": 80, "msg_type": "high_reaction"}]

            result = analyze_channel(-1001234567890)

        assert len(result['reactions']) == 1
        reaction = result['reactions'][0]
        assert 'message_id' in reaction
        assert 'positive' in reaction
        assert 'heart' in reaction
        assert 'total' in reaction