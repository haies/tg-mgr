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


class TestSummarizeMessagesForForward:
    """测试 summarize_messages_for_forward 函数"""

    def test_summarize_messages_for_forward_empty(self):
        """空消息列表"""
        import sqlite3
        from modules.forward import summarize_messages_for_forward

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages (message_id INTEGER, file_size INTEGER)")
        result = summarize_messages_for_forward(conn, [])
        assert result["total_count"] == 0
        assert result["media_count"] == 0
        assert result["total_size_mb"] == 0.0
        conn.close()

    def test_summarize_messages_for_forward_with_media(self):
        """有媒体的消息统计"""
        import sqlite3
        from modules.forward import summarize_messages_for_forward

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages (message_id INTEGER PRIMARY KEY, file_size INTEGER)")
        conn.execute("INSERT INTO messages VALUES (1, 10485760), (2, 20971520), (3, 0)")  # 10MB, 20MB, 0
        messages = [{"message_id": 1}, {"message_id": 2}, {"message_id": 3}]
        result = summarize_messages_for_forward(conn, messages)
        assert result["total_count"] == 3
        assert result["media_count"] == 2
        assert abs(result["total_size_mb"] - 30.0) < 0.01
        conn.close()


class TestConfirmForward:
    """测试 confirm_forward 函数"""

    def test_confirm_forward_yes(self):
        """用户输入 y 返回 True"""
        from unittest.mock import patch
        from modules.forward import confirm_forward

        messages = [{"message_id": 1}, {"message_id": 2}]
        summary = {"total_count": 2, "media_count": 2, "total_size_mb": 30.0}
        with patch("builtins.input", return_value="y"):
            result = confirm_forward(messages, summary)
        assert result is True

    def test_confirm_forward_no(self):
        """用户输入 n 返回 False"""
        from unittest.mock import patch
        from modules.forward import confirm_forward

        with patch("builtins.input", return_value="n"):
            result = confirm_forward([], {"total_count": 0})
        assert result is False

    def test_confirm_forward_empty_input(self):
        """用户直接回车视为拒绝"""
        from unittest.mock import patch
        from modules.forward import confirm_forward

        with patch("builtins.input", return_value=""):
            result = confirm_forward([], {"total_count": 0})
        assert result is False


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


class TestForceConfirmationNonRecursive:
    """测试 -f 参数在非递归模式下的确认逻辑"""

    def test_confirm_forward_called_when_force_and_messages_exist(self, populated_db):
        """当 -f 且有消息时，确认函数被调用"""
        from unittest.mock import patch, MagicMock
        import sys
        from pathlib import Path

        # Import after path is set up
        from modules.forward import main as forward_main

        # Mock the dependencies
        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward'), \
             patch('modules.forward.get_channel_temp_db_path') as mock_temp_db_path, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_messages_batch') as mock_forward, \
             patch('modules.forward.cleanup_channel_temp_dbs') as mock_cleanup, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 0}):

            # Setup mocks
            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            # Mock temp db path to return a real temp db with proper schema
            import tempfile
            import sqlite3
            temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
            temp_db.close()
            # Create the messages table with proper schema
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    file_size INTEGER,
                    media_type TEXT,
                    caption TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_duplicate BOOLEAN DEFAULT 0,
                    is_valid BOOLEAN DEFAULT 1,
                    reactions TEXT DEFAULT '{"positive": 0, "heart": 0}',
                    source_id INTEGER,
                    views INTEGER DEFAULT 0,
                    media_group_id TEXT,
                    UNIQUE(message_id)
                )
            """)
            conn.close()
            mock_temp_db_path.return_value = Path(temp_db.name)

            mock_find.return_value = [
                {"message_id": 1, "positive": 10, "heart": 5, "views": 100},
                {"message_id": 2, "positive": 20, "heart": 10, "views": 200},
            ]
            mock_summarize.return_value = {"total_count": 2, "media_count": 1, "total_size_mb": 15.0}
            mock_confirm.return_value = True
            mock_forward.return_value = (2, 0, 0)

            # Patch argparse (positional sources, -o for target, -f for force)
            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-f']):
                forward_main()

            # 确认 confirm_forward 被调用
            mock_confirm.assert_called_once()

    def test_confirm_forward_not_called_when_no_force(self, populated_db):
        """当没有 -f 时，确认函数不应被调用"""
        from unittest.mock import patch, MagicMock

        from modules.forward import main as forward_main

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward'), \
             patch('modules.forward.get_channel_temp_db_path') as mock_temp_db_path, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_messages_batch') as mock_forward, \
             patch('modules.forward.cleanup_channel_temp_dbs') as mock_cleanup, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 0}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            import tempfile
            import sqlite3
            temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
            temp_db.close()
            # Create the messages table with proper schema
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    file_size INTEGER,
                    media_type TEXT,
                    caption TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_duplicate BOOLEAN DEFAULT 0,
                    is_valid BOOLEAN DEFAULT 1,
                    reactions TEXT DEFAULT '{"positive": 0, "heart": 0}',
                    source_id INTEGER,
                    views INTEGER DEFAULT 0,
                    media_group_id TEXT,
                    UNIQUE(message_id)
                )
            """)
            conn.close()
            mock_temp_db_path.return_value = Path(temp_db.name)

            mock_find.return_value = [
                {"message_id": 1, "positive": 10, "heart": 5, "views": 100},
            ]
            mock_forward.return_value = (1, 0, 0)

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001']):
                forward_main()

            # -f 未使用时，confirm_forward 不应被调用
            mock_confirm.assert_not_called()

    def test_returns_early_when_confirm_rejected(self, populated_db):
        """用户拒绝确认时应提前返回，不执行转发"""
        from unittest.mock import patch, MagicMock

        from modules.forward import main as forward_main

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward'), \
             patch('modules.forward.get_channel_temp_db_path') as mock_temp_db_path, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_messages_batch') as mock_forward, \
             patch('modules.forward.cleanup_channel_temp_dbs') as mock_cleanup, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 0}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            import tempfile
            import sqlite3
            temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
            temp_db.close()
            # Create the messages table with proper schema
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    file_size INTEGER,
                    media_type TEXT,
                    caption TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_duplicate BOOLEAN DEFAULT 0,
                    is_valid BOOLEAN DEFAULT 1,
                    reactions TEXT DEFAULT '{"positive": 0, "heart": 0}',
                    source_id INTEGER,
                    views INTEGER DEFAULT 0,
                    media_group_id TEXT,
                    UNIQUE(message_id)
                )
            """)
            conn.close()
            mock_temp_db_path.return_value = Path(temp_db.name)

            mock_find.return_value = [
                {"message_id": 1, "positive": 10, "heart": 5, "views": 100},
            ]
            mock_summarize.return_value = {"total_count": 1, "media_count": 0, "total_size_mb": 0.0}
            mock_confirm.return_value = False  # 用户拒绝

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-f']):
                forward_main()

            # 确认被调用但转发未被调用
            mock_confirm.assert_called_once()
            mock_forward.assert_not_called()


class TestForceConfirmationRecursive:
    """测试 -f 参数在递归模式下的确认逻辑"""

    def test_confirm_called_before_recursion_when_force_used(self, populated_db):
        """当 -f 用于递归模式时，确认在递归前被调用"""
        from unittest.mock import patch, MagicMock
        import sys
        from pathlib import Path

        from modules.forward import main as forward_main

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward') as mock_sync, \
             patch('modules.forward.get_db_connection') as mock_get_db, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_with_recursion') as mock_recursive, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 10}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            mock_find.return_value = [
                {"message_id": 1, "positive": 10, "heart": 5, "views": 100},
                {"message_id": 2, "positive": 20, "heart": 10, "views": 200},
            ]
            mock_summarize.return_value = {"total_count": 2, "media_count": 0, "total_size_mb": 0.0}
            mock_confirm.return_value = True
            mock_recursive.return_value = (1, 0, 0)

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-f', '-r', '3']):
                forward_main()

            # 确认在递归前被调用
            mock_confirm.assert_called_once()
            # 递归函数应该被调用（因为确认通过）
            mock_recursive.assert_called_once()

    def test_confirm_not_called_when_force_not_used_recursive(self, populated_db):
        """递归模式但没有 -f 时，确认不应被调用"""
        from unittest.mock import patch, MagicMock
        import sys
        from pathlib import Path

        from modules.forward import main as forward_main

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward') as mock_sync, \
             patch('modules.forward.get_db_connection') as mock_get_db, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_with_recursion') as mock_recursive, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 10}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            mock_recursive.return_value = (1, 0, 0)

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-r', '3']):
                forward_main()

            # -f 未使用时，confirm_forward 不应被调用
            mock_confirm.assert_not_called()
            # 递归函数应该被调用
            mock_recursive.assert_called_once()

    def test_recursion_returns_early_when_confirm_rejected(self, populated_db):
        """递归模式且用户拒绝确认时应提前返回"""
        from unittest.mock import patch, MagicMock
        import sys
        from pathlib import Path

        from modules.forward import main as forward_main

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward') as mock_sync, \
             patch('modules.forward.get_db_connection') as mock_get_db, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_with_recursion') as mock_recursive, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 10}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            mock_find.return_value = [
                {"message_id": 1, "positive": 10, "heart": 5, "views": 100},
            ]
            mock_summarize.return_value = {"total_count": 1, "media_count": 0, "total_size_mb": 0.0}
            mock_confirm.return_value = False  # 用户拒绝

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-f', '-r', '3']):
                forward_main()

            # 确认被调用
            mock_confirm.assert_called_once()
            # 但递归函数不应被调用（因为确认未通过）
            mock_recursive.assert_not_called()

    def test_recursion_returns_early_when_no_messages(self, populated_db):
        """递归模式且所有频道无消息时应提前返回"""
        from unittest.mock import patch, MagicMock
        import sys
        from pathlib import Path

        from modules.forward import main as forward_main

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward') as mock_sync, \
             patch('modules.forward.get_db_connection') as mock_get_db, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.confirm_forward') as mock_confirm, \
             patch('modules.forward.forward_with_recursion') as mock_recursive, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 10}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            mock_find.return_value = []  # 无消息
            mock_summarize.return_value = {"total_count": 0, "media_count": 0, "total_size_mb": 0.0}

            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-f', '-r', '3']):
                forward_main()

            # 确认不应被调用（因为没有消息）
            mock_confirm.assert_not_called()
            # 递归函数不应被调用
            mock_recursive.assert_not_called()


class TestForwardForceFlagConfirmation:
    """测试 -f 参数触发的确认提示包含 MB 和条数信息"""

    def test_forward_force_flag_shows_confirmation(self, populated_db, monkeypatch):
        """-f 参数触发统计确认，提示应包含 MB 和条数信息"""
        from unittest.mock import patch, MagicMock

        from modules.forward import main as forward_main

        captured = {}
        def mock_input(prompt):
            captured["prompt"] = prompt
            return "y"
        monkeypatch.setattr("builtins.input", mock_input)

        with patch('modules.forward.get_client') as mock_get_client, \
             patch('modules.forward.is_channel_forwarding_allowed', return_value=True), \
             patch('modules.forward.sync_channel_for_forward'), \
             patch('modules.forward.get_channel_temp_db_path') as mock_temp_db_path, \
             patch('modules.forward.find_messages_to_forward') as mock_find, \
             patch('modules.forward.summarize_messages_for_forward') as mock_summarize, \
             patch('modules.forward.forward_messages_batch') as mock_forward, \
             patch('modules.forward.cleanup_channel_temp_dbs') as mock_cleanup, \
             patch('modules.forward.get_config', return_value={"recursion_depth": 0}):

            mock_client = MagicMock()
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)

            import tempfile
            import sqlite3
            temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
            temp_db.close()
            # Create the messages table with proper schema
            conn = sqlite3.connect(temp_db.name)
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    file_size INTEGER,
                    media_type TEXT,
                    caption TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_duplicate BOOLEAN DEFAULT 0,
                    is_valid BOOLEAN DEFAULT 1,
                    reactions TEXT DEFAULT '{"positive": 0, "heart": 0}',
                    source_id INTEGER,
                    views INTEGER DEFAULT 0,
                    media_group_id TEXT,
                    UNIQUE(message_id)
                )
            """)
            conn.close()
            mock_temp_db_path.return_value = Path(temp_db.name)

            mock_find.return_value = [
                {"message_id": 1, "positive": 10, "heart": 5, "views": 100},
                {"message_id": 2, "positive": 20, "heart": 10, "views": 200},
            ]
            mock_summarize.return_value = {"total_count": 2, "media_count": 1, "total_size_mb": 15.0}
            mock_forward.return_value = (2, 0, 0)

            with patch('sys.argv', ['tg', 'forward', '123', '-o', '-1001', '-f']):
                forward_main()

            # 确认 prompt 包含 MB 和条数信息
            # MB 在 input prompt 中，条数信息在打印的统计中
            prompt = captured.get("prompt", "")
            assert "MB" in prompt, f"Expected 'MB' in prompt, got: {prompt}"
