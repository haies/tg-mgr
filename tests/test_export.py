"""
导出模块单元测试

测试 export 模块的功能：
1. 解析命令行参数（支持频道ID和消息地址）
2. 断点续传状态管理
3. 增量导出逻辑
4. 异常处理
"""
import pytest
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from modules.export import ExportState, parse_export_args


class TestExportState:
    """测试 ExportState 状态管理"""

    def test_init_creates_empty_state(self, tmp_path):
        """测试初始化空状态"""
        state = ExportState(tmp_path)
        assert state.state['processed_messages'] == []
        assert state.state['downloaded_files'] == {}
        assert state.state['last_update'] is None

    def test_init_loads_existing_state(self, tmp_path):
        """测试加载已存在的状态文件"""
        state_file = tmp_path / 'export_state.json'
        existing_state = {
            'processed_messages': [1, 2, 3],
            'downloaded_files': {'file1': 'photos/photo1.jpg'},
            'last_update': '2026-04-29T10:00:00'
        }
        with open(state_file, 'w') as f:
            json.dump(existing_state, f)

        state = ExportState(tmp_path)
        assert state.state['processed_messages'] == [1, 2, 3]
        assert state.state['downloaded_files'] == {'file1': 'photos/photo1.jpg'}

    def test_save_writes_state_file(self, tmp_path):
        """测试保存状态到文件"""
        state = ExportState(tmp_path)
        state.state['processed_messages'] = [1, 2]
        state.save()

        state_file = tmp_path / 'export_state.json'
        assert state_file.exists()
        with open(state_file, 'r') as f:
            loaded = json.load(f)
        assert loaded['processed_messages'] == [1, 2]
        assert loaded['last_update'] is not None

    def test_is_message_processed(self, tmp_path):
        """测试消息处理状态检查"""
        state = ExportState(tmp_path)
        state.state['processed_messages'] = [1, 2, 3]

        assert state.is_message_processed(1) is True
        assert state.is_message_processed(5) is False

    def test_mark_message_processed(self, tmp_path):
        """测试标记消息已处理"""
        state = ExportState(tmp_path)
        state.mark_message_processed(1)
        state.mark_message_processed(2)

        assert state.is_message_processed(1) is True
        assert state.is_message_processed(2) is True

    def test_mark_message_processed_no_duplicate(self, tmp_path):
        """测试标记不会重复添加"""
        state = ExportState(tmp_path)
        state.mark_message_processed(1)
        state.mark_message_processed(1)

        assert len(state.state['processed_messages']) == 1


class TestParseExportArgs:
    """测试命令行参数解析"""

    def test_empty_args_uses_config_default(self):
        """测试空参数使用配置文件默认值"""
        with patch('modules.export.get_config') as mock_config:
            mock_config.return_value = {'channel_id': '-1001234567890'}
            args = parse_export_args([])
            assert args.channel_ids == ['-1001234567890']

    def test_single_channel_id(self):
        """测试单个频道ID"""
        args = parse_export_args(['-1001234567890'])
        assert args.channel_ids == ['-1001234567890']

    def test_multiple_channel_ids(self):
        """测试多个频道ID"""
        args = parse_export_args(['-1001234567890', '-1009876543210'])
        assert args.channel_ids == ['-1001234567890', '-1009876543210']

    def test_message_address_single(self):
        """测试单个消息地址"""
        args = parse_export_args(['https://t.me/c/1234567890/100'])
        assert args.channel_ids == ['-1001234567890']
        assert args.message_ids == [100]

    def test_message_address_multiple(self):
        """测试多个消息地址"""
        args = parse_export_args([
            'https://t.me/c/1234567890/100',
            'https://t.me/c/1234567890/200'
        ])
        assert args.channel_ids == ['-1001234567890', '-1001234567890']
        assert args.message_ids == [100, 200]

    def test_mixed_channel_ids_and_addresses(self):
        """测试混合频道ID和消息地址"""
        args = parse_export_args([
            '-1001234567890',
            'https://t.me/c/9876543210/300'
        ])
        assert args.channel_ids == ['-1001234567890', '-1009876543210']
        assert args.message_ids == [300]

    def test_channel_id_without_prefix(self):
        """测试不带-100前缀的频道ID"""
        args = parse_export_args(['1234567890'])
        assert args.channel_ids == ['1234567890']

    def test_message_id_extracted_from_address(self):
        """测试从消息地址提取消息ID"""
        args = parse_export_args(['https://t.me/c/1234567890/456'])
        assert args.message_ids == [456]


class TestExportIncremental:
    """测试增量导出逻辑"""

    def test_finds_existing_export_dir(self, tmp_path):
        """测试查找已存在的导出目录"""
        from modules.export import find_existing_export_dir
        from utils.file_sanitizer import sanitize_filename

        # sanitize_filename 会将空格替换为下划线
        channel_title = "Test Channel"
        sanitized_title = sanitize_filename(channel_title)
        existing_dir = tmp_path / f"{sanitized_title}_2026-04-29_10-00-00"
        existing_dir.mkdir(parents=True)

        found = find_existing_export_dir(tmp_path, channel_title)
        assert found == existing_dir

    def test_no_existing_dir_returns_none(self, tmp_path):
        """测试没有已存在目录时返回None"""
        from modules.export import find_existing_export_dir

        found = find_existing_export_dir(tmp_path, "Non Existent")
        assert found is None

    def test_export_state_merges_with_existing(self, tmp_path):
        """测试导出状态与已有状态合并"""
        state_file = tmp_path / 'export_state.json'
        existing_state = {
            'processed_messages': [1, 2, 3],
            'downloaded_files': {'file1': 'photos/p1.jpg'},
            'last_update': '2026-04-29T10:00:00'
        }
        with open(state_file, 'w') as f:
            json.dump(existing_state, f)

        state = ExportState(tmp_path)
        state.mark_message_processed(4)

        # 模拟合并后的状态
        all_messages = set(state.state['processed_messages'])
        assert 1 in all_messages
        assert 4 in all_messages


class TestErrorHandling:
    """测试异常处理"""

    def test_download_error_caught_and_logged(self, tmp_path):
        """测试下载错误被捕获并记录"""
        from modules.export import download_media_from_message

        # Mock client and message
        mock_client = Mock()
        mock_message = Mock()
        mock_message.photo = Mock()
        mock_message.photo.file_unique_id = 'test_file_id'

        # Mock download to raise error
        mock_client.download_media = Mock(side_effect=Exception("Download failed"))

        state = ExportState(tmp_path)
        result = download_media_from_message(
            mock_client, mock_message, 'photos', tmp_path, state
        )

        # 应该返回None而不是抛出异常
        assert result is None

    def test_flood_wait_error_handled(self, tmp_path):
        """测试FloodWait错误处理"""
        from pyrogram import errors

        # 这个测试需要mock client来触发FloodWait
        pass  # 需要完整的client mock


class TestExportArgsNamespace:
    """测试 parse_export_args 返回的对象结构"""

    def test_namespace_has_channel_ids(self):
        """测试返回对象包含channel_ids"""
        args = parse_export_args(['-1001234567890'])
        assert hasattr(args, 'channel_ids')
        assert isinstance(args.channel_ids, list)

    def test_namespace_has_message_ids(self):
        """测试返回对象包含message_ids"""
        args = parse_export_args(['https://t.me/c/1234567890/100'])
        assert hasattr(args, 'message_ids')
        assert isinstance(args.message_ids, list)