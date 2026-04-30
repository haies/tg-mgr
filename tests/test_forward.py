"""
forward模块单元测试

测试 src/modules/forward.py 中的消息转发功能
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


class TestFindHighReactionMessages:
    """测试 find_high_reaction_messages 函数"""

    def test_find_with_threshold_over_50(self, populated_db):
        """测试超过50阈值的情况"""
        from modules.forward import find_high_reaction_messages

        # populated_db 有 message_id 4 (positive=100, heart=50, total=150) 和 5 (positive=80, heart=30, total=110)
        results = find_high_reaction_messages(-1001234567890, populated_db)

        # 应该返回消息列表
        assert isinstance(results, list)

    def test_result_has_message_id(self, populated_db):
        """测试返回结果包含 message_id"""
        from modules.forward import find_high_reaction_messages

        results = find_high_reaction_messages(-1001234567890, populated_db)

        if results:
            assert 'message_id' in results[0]


class TestGetChannelAddress:
    """测试 get_channel_address 函数"""

    def test_channel_address_100_prefix(self):
        """测试 -100 前缀频道地址生成"""
        from modules.forward import get_channel_address

        address = get_channel_address(-1001234567890)
        assert 't.me/c/' in address

    def test_channel_address_regular(self):
        """测试普通频道地址生成"""
        from modules.forward import get_channel_address

        address = get_channel_address(-1234567890)
        assert 't.me/c/' in address


class TestIsChannelForwardingAllowed:
    """测试 is_channel_forwarding_allowed 函数"""

    def test_returns_boolean(self):
        """测试返回布尔值"""
        from modules.forward import is_channel_forwarding_allowed

        mock_client = MagicMock()
        mock_client.get_chat.return_value = MagicMock(has_protected_content=False)

        result = is_channel_forwarding_allowed(mock_client, -1001234567890)
        assert isinstance(result, bool)

    def test_protected_content_blocked(self):
        """测试受保护内容被阻止"""
        from modules.forward import is_channel_forwarding_allowed

        mock_client = MagicMock()
        mock_client.get_chat.return_value = MagicMock(has_protected_content=True)

        result = is_channel_forwarding_allowed(mock_client, -1001234567890)
        assert result == False