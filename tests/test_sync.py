"""
sync模块单元测试

测试 src/modules/sync.py 中的同步功能
"""
import os
import pytest
import sys
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Set TG_MGR_DEV before importing any modules to avoid config errors
os.environ['TG_MGR_DEV'] = '1'

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestForceResetDatabase:
    """测试 force_reset_database 函数"""

    def test_force_reset_database_exists(self, tmp_path):
        """测试当数据库文件存在时应该被删除"""
        from modules.sync import force_reset_database
        from database import get_database_path

        # Create a temporary database file
        db_file = tmp_path / "test_database.db"
        db_file.write_text("dummy content")

        with patch('modules.sync.get_database_path', return_value=db_file):
            force_reset_database()

        # DB file should be deleted
        assert not db_file.exists()

    def test_force_reset_database_not_exists(self, tmp_path):
        """测试当数据库文件不存在时应该优雅处理（不抛出异常）"""
        from modules.sync import force_reset_database

        db_file = tmp_path / "nonexistent_database.db"

        with patch('modules.sync.get_database_path', return_value=db_file):
            # Should not raise any exception
            force_reset_database()


class TestTryJoinChannel:
    """测试 _try_join_channel 函数"""

    def test_try_join_channel_success_direct(self):
        """测试直接加入成功（join_chat 首次调用即成功）"""
        from modules.sync import _try_join_channel

        mock_client = MagicMock()
        mock_client.join_chat.return_value = MagicMock()  # Success, no exception

        result = _try_join_channel(mock_client, -1001234567890)

        assert result is True
        mock_client.join_chat.assert_called_once_with(-1001234567890)

    def test_try_join_channel_success_via_username(self):
        """测试通过用户名加入成功（首次 join_chat 失败，get_chat 获取到 username，第二次 join 成功）"""
        from modules.sync import _try_join_channel

        mock_client = MagicMock()

        # First join_chat fails
        mock_client.join_chat.side_effect = [Exception("Flood wait"), MagicMock()]

        # get_chat returns a chat with username
        mock_chat = MagicMock()
        mock_chat.username = "test_channel"
        mock_client.get_chat.return_value = mock_chat

        result = _try_join_channel(mock_client, -1001234567890)

        assert result is True
        assert mock_client.join_chat.call_count == 2
        mock_client.get_chat.assert_called_once_with(-1001234567890)
        mock_client.join_chat.assert_called_with("https://t.me/test_channel")

    def test_try_join_channel_failure(self):
        """测试加入失败（两次 join_chat 和 get_chat 都失败）"""
        from modules.sync import _try_join_channel

        mock_client = MagicMock()

        # All attempts fail
        mock_client.join_chat.side_effect = Exception("Flood wait")
        mock_client.get_chat.side_effect = Exception("Not found")

        result = _try_join_channel(mock_client, -1001234567890)

        assert result is False
        assert mock_client.join_chat.call_count == 1  # Only first attempt
        mock_client.get_chat.assert_called_once()

    def test_try_join_channel_no_username(self):
        """测试获取到 chat 但没有 username 的情况"""
        from modules.sync import _try_join_channel

        mock_client = MagicMock()

        # First join_chat fails
        mock_client.join_chat.side_effect = [Exception("Flood wait"), MagicMock()]

        # get_chat returns a chat without username
        mock_chat = MagicMock()
        mock_chat.username = None
        mock_client.get_chat.return_value = mock_chat

        # Second join_chat with username also fails
        mock_client.join_chat.side_effect = [Exception("Flood wait"), Exception("Failed again")]

        result = _try_join_channel(mock_client, -1001234567890)

        assert result is False


class TestSyncImpl:
    """测试 _sync_impl 函数（重点测试 join 逻辑）"""

    def test_sync_impl_channel_forbidden_tries_join(self, tmp_path):
        """测试当获取历史失败（ChannelForbidden）时尝试加入频道"""
        from modules.sync import _sync_impl
        from database import get_schema_path
        from pyrogram import errors

        # Create a temp DB file path
        db_file = tmp_path / "test_sync.db"

        mock_client = MagicMock()

        # Pre-fetch call (limit=1) returns a mock message
        mock_msg = MagicMock()
        mock_msg.id = 100
        # First call to get_chat_history raises ChatForbidden (in while loop)
        # After join succeeds, second call returns empty list (no more messages)
        mock_client.get_chat_history.side_effect = [
            iter([mock_msg]),  # Pre-fetch for latest message check
            errors.ChatForbidden(420),  # First batch in while loop
            iter([])  # Empty iterator after join
        ]
        mock_client.join_chat.return_value = MagicMock()  # Join succeeds

        with patch('modules.sync.get_schema_path', return_value=get_schema_path()), \
             patch('modules.sync.get_client') as mock_get_client, \
             patch('modules.sync.get_database_path', return_value=db_file), \
             patch('modules.sync.init_database'), \
             patch('modules.sync.get_last_processed_id', return_value=0), \
             patch('modules.sync.get_existing_files', return_value=set()), \
             patch('modules.sync.insert_messages', return_value=(0, 0, 0)), \
             patch('modules.sync.get_message_stats', return_value=[]):

            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            joined_channels = set()
            result = _sync_impl("-1001234567890", joined_channels=joined_channels)

            # Join should have been called and channel added to joined_channels
            assert -1001234567890 in joined_channels
            assert result is True

    def test_sync_impl_join_failure_propagates(self, tmp_path):
        """测试当加入失败时 sync_success 应该为 False"""
        from modules.sync import _sync_impl
        from database import get_schema_path
        from pyrogram import errors

        # Create a temp DB file path
        db_file = tmp_path / "test_sync.db"

        mock_client = MagicMock()

        # Pre-fetch returns a mock message; then ChatForbidden in while loop
        mock_msg = MagicMock()
        mock_msg.id = 100
        mock_client.get_chat_history.side_effect = [
            iter([mock_msg]),  # Pre-fetch for latest message check
            errors.ChatForbidden(420),  # First batch in while loop
        ]
        mock_client.join_chat.side_effect = Exception("Join failed")
        mock_client.get_chat.side_effect = Exception("Chat not found")

        with patch('modules.sync.get_schema_path', return_value=get_schema_path()), \
             patch('modules.sync.get_client') as mock_get_client, \
             patch('modules.sync.get_database_path', return_value=db_file), \
             patch('modules.sync.init_database'), \
             patch('modules.sync.get_last_processed_id', return_value=0), \
             patch('modules.sync.get_existing_files', return_value=set()):

            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            joined_channels = set()
            result = _sync_impl("-1001234567890", joined_channels=joined_channels)

            # Since join failed, sync_success should be False
            assert result is False
            # Channel should NOT be in joined_channels since join failed
            assert -1001234567890 not in joined_channels

    def test_sync_impl_channel_private_tries_join(self, tmp_path):
        """测试当获取历史失败（ChannelPrivate）时尝试加入频道"""
        from modules.sync import _sync_impl
        from database import get_schema_path
        from pyrogram import errors

        db_file = tmp_path / "test_sync.db"

        mock_client = MagicMock()

        # Pre-fetch returns a mock message; then ChannelPrivate in while loop
        mock_msg = MagicMock()
        mock_msg.id = 100
        # First call raises ChannelPrivate
        # After join succeeds, second call returns empty iterator
        mock_client.get_chat_history.side_effect = [
            iter([mock_msg]),  # Pre-fetch for latest message check
            errors.ChannelPrivate(400),  # First batch in while loop
            iter([])  # Empty after join succeeds
        ]
        mock_client.join_chat.return_value = MagicMock()

        with patch('modules.sync.get_schema_path', return_value=get_schema_path()), \
             patch('modules.sync.get_client') as mock_get_client, \
             patch('modules.sync.get_database_path', return_value=db_file), \
             patch('modules.sync.init_database'), \
             patch('modules.sync.get_last_processed_id', return_value=0), \
             patch('modules.sync.get_existing_files', return_value=set()), \
             patch('modules.sync.insert_messages', return_value=(0, 0, 0)), \
             patch('modules.sync.get_message_stats', return_value=[]):

            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            joined_channels = set()
            result = _sync_impl("-1001234567890", joined_channels=joined_channels)

            assert result is True
            assert -1001234567890 in joined_channels

    def test_sync_impl_channel_invalid_tries_join(self, tmp_path):
        """测试当获取历史失败（ChannelInvalid）时尝试加入频道"""
        from modules.sync import _sync_impl
        from database import get_schema_path
        from pyrogram import errors

        db_file = tmp_path / "test_sync.db"

        mock_client = MagicMock()

        # Pre-fetch returns a mock message; then ChannelInvalid in while loop
        mock_msg = MagicMock()
        mock_msg.id = 100
        # First call raises ChannelInvalid
        # After join succeeds, second call returns empty iterator
        mock_client.get_chat_history.side_effect = [
            iter([mock_msg]),  # Pre-fetch for latest message check
            errors.ChannelInvalid(400),  # First batch in while loop
            iter([])  # Empty after join succeeds
        ]
        mock_client.join_chat.return_value = MagicMock()

        with patch('modules.sync.get_schema_path', return_value=get_schema_path()), \
             patch('modules.sync.get_client') as mock_get_client, \
             patch('modules.sync.get_database_path', return_value=db_file), \
             patch('modules.sync.init_database'), \
             patch('modules.sync.get_last_processed_id', return_value=0), \
             patch('modules.sync.get_existing_files', return_value=set()), \
             patch('modules.sync.insert_messages', return_value=(0, 0, 0)), \
             patch('modules.sync.get_message_stats', return_value=[]):

            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            joined_channels = set()
            result = _sync_impl("-1001234567890", joined_channels=joined_channels)

            assert result is True
            assert -1001234567890 in joined_channels