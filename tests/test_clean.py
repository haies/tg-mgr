"""
clean模块单元测试

测试 src/modules/clean.py 中的同步和清理功能
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


class TestFindDuplicates:
    """测试 find_duplicates 函数"""

    def test_find_no_duplicates(self, test_db):
        """测试无重复时返回空列表"""
        from modules.clean import find_duplicates

        duplicates = find_duplicates(test_db)
        assert duplicates == []

    def test_find_duplicates(self, populated_db):
        """测试检测重复媒体"""
        from modules.clean import find_duplicates

        duplicates = find_duplicates(populated_db)

        # sample_messages 有 file1 重复 (message_id 1 和 6)
        assert len(duplicates) >= 1

    def test_duplicates_have_correct_structure(self, populated_db):
        """测试重复组结构正确 (file_size, media_type, keep_id, delete_ids)"""
        from modules.clean import find_duplicates

        duplicates = find_duplicates(populated_db)

        if duplicates:
            file_size, media_type, keep_id, delete_ids = duplicates[0]
            assert isinstance(file_size, int)
            assert isinstance(media_type, str)
            assert isinstance(keep_id, int)
            assert isinstance(delete_ids, list)


class TestFindInvalidMessages:
    """测试 find_invalid_messages 函数"""

    def test_find_no_invalid(self, populated_db):
        """测试无无效消息时返回空列表"""
        from modules.clean import find_invalid_messages

        invalid = find_invalid_messages(populated_db)
        # populated_db 中 message_id 8 is_valid=0
        assert len(invalid) >= 1

    def test_invalid_message_structure(self, populated_db):
        """测试无效消息包含必要字段"""
        from modules.clean import find_invalid_messages

        invalid = find_invalid_messages(populated_db)

        if invalid:
            msg = invalid[0]
            assert len(msg) >= 5  # message_id, file_unique_id, file_size, media_type, timestamp


class TestInitDatabase:
    """测试 init_database 函数"""

    def test_init_creates_connection(self, tmp_path):
        """测试初始化数据库"""
        from modules.clean import init_database

        # Mock get_database_path and get_schema_path
        with patch('modules.clean.get_database_path') as mock_db_path, \
             patch('modules.clean.get_schema_path') as mock_schema_path:

            # Create temp schema file with proper columns for index creation
            schema_file = tmp_path / "schema.sql"
            schema_file.write_text("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    message_id INTEGER,
                    file_unique_id TEXT,
                    file_size INTEGER,
                    media_type TEXT,
                    caption TEXT,
                    is_duplicate INTEGER DEFAULT 0,
                    is_valid INTEGER DEFAULT 1,
                    reactions TEXT,
                    source_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL
                );
            """)

            mock_db_path.return_value = tmp_path / "test.db"
            mock_schema_path.return_value = schema_file

            conn = init_database()

            assert conn is not None
            cursor = conn.cursor()
            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert 'messages' in tables
            conn.close()


class TestRunDeduplicate:
    """测试 run_deduplicate 函数"""

    def test_deduplicate_no_duplicates(self, tmp_path, capsys):
        """测试无重复时不执行删除"""
        from modules.clean import run_deduplicate

        with patch('modules.clean.get_config') as mock_config, \
             patch('modules.clean.init_database') as mock_init_db, \
             patch('modules.clean.find_duplicates') as mock_find:

            mock_config.return_value = {
                'api_id': 12345,
                'api_hash': 'test_hash',
                'channel_id': -1001234567890
            }

            # Create a mock connection
            mock_conn = MagicMock()
            mock_init_db.return_value = mock_conn
            mock_find.return_value = []  # No duplicates

            run_deduplicate(delete=False)

            # Should print no duplicates found
            captured = capsys.readouterr()
            assert "未检测到重复媒体" in captured.out or "检测到 0 组重复" in captured.out

    def test_deduplicate_with_duplicates_no_delete(self, tmp_path, capsys):
        """测试检测模式（不删除）"""
        from modules.clean import run_deduplicate

        with patch('modules.clean.get_config') as mock_config, \
             patch('modules.clean.init_database') as mock_init_db, \
             patch('modules.clean.find_duplicates') as mock_find:

            mock_config.return_value = {
                'api_id': 12345,
                'api_hash': 'test_hash',
                'channel_id': -1001234567890
            }

            mock_conn = MagicMock()
            mock_init_db.return_value = mock_conn

            # Return one duplicate group: (file_size, media_type, keep_id, delete_ids)
            mock_find.return_value = [(1024, 'photo', 1, [2, 3])]

            run_deduplicate(delete=False)

            captured = capsys.readouterr()
            assert "检测到" in captured.out


class TestRunDeinvalid:
    """测试 run_deinvalid 函数"""

    def test_deinvalid_no_invalid(self, tmp_path, capsys):
        """测试无无效消息时的情况"""
        from modules.clean import run_deinvalid

        with patch('modules.clean.get_config') as mock_config, \
             patch('modules.clean.init_database') as mock_init_db, \
             patch('modules.clean.find_invalid_messages') as mock_find:

            mock_config.return_value = {
                'api_id': 12345,
                'api_hash': 'test_hash',
                'channel_id': -1001234567890
            }

            mock_conn = MagicMock()
            mock_init_db.return_value = mock_conn
            mock_find.return_value = []

            run_deinvalid(delete=False)

            captured = capsys.readouterr()
            assert "未检测到无效消息" in captured.out


class TestCheckRestricted:
    """测试 check_restricted 函数"""

    def test_check_restricted_empty_message(self):
        """测试空消息检查"""
        from modules.clean import check_restricted

        result = check_restricted(None)
        assert result == "message is empty"

    def test_check_restricted_valid_message(self):
        """测试正常消息返回空字符串"""
        from modules.clean import check_restricted

        mock_message = MagicMock()
        mock_message.empty = False
        mock_message.restrictions = None
        mock_message.media = None

        result = check_restricted(mock_message)
        assert result == ""