"""
工具模块单元测试

测试 utils 目录下的辅助函数
"""
import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from utils.telegram_link import generate_tg_link
from utils.file_sanitizer import sanitize_filename


class TestGenerateTgLink:
    """测试 generate_tg_link 函数"""

    def test_standard_channel_id(self):
        """测试标准频道ID格式 (-100xxxxxxxxxx)"""
        link = generate_tg_link('-1001234567890', 123)
        assert link == 'https://t.me/c/1234567890/123'

    def test_channel_id_without_prefix(self):
        """测试不带-100前缀的ID"""
        link = generate_tg_link('1234567890', 456)
        assert link == 'https://t.me/c/1234567890/456'

    def test_negative_channel_id(self):
        """测试负数频道ID"""
        link = generate_tg_link(-1009876543210, 789)
        assert link == 'https://t.me/c/9876543210/789'

    def test_numeric_string(self):
        """测试数字字符串"""
        link = generate_tg_link('1001234567890', 100)
        assert link == 'https://t.me/c/1001234567890/100'


class TestSanitizeFilename:
    """测试 sanitize_filename 函数"""

    def test_remove_illegal_chars(self):
        """测试移除非法字符"""
        result = sanitize_filename('file:name*test?.txt')
        assert ':' not in result
        assert '*' not in result
        assert '?' not in result
        assert '|' not in result

    def test_replace_spaces(self):
        """测试替换空格"""
        result = sanitize_filename('file name test.txt')
        assert ' ' not in result
        assert '_' in result

    def test_preserve_extension(self):
        """测试保留扩展名"""
        result = sanitize_filename('document.pdf')
        assert result.endswith('.pdf')

    def test_empty_filename(self):
        """测试空文件名"""
        result = sanitize_filename('.pdf')
        assert 'file' in result

    def test_only_extension(self):
        """测试只有扩展名的情况"""
        result = sanitize_filename('.hidden')
        assert result.startswith('file')


class TestGetConfig:
    """测试 get_config 函数"""

    @pytest.fixture(autouse=True)
    def setup_test_config(self, monkeypatch, tmp_path):
        """设置测试配置"""
        import os

        # 检查是否有有效配置
        has_env_vars = os.environ.get('TG_API_ID') and os.environ.get('TG_API_HASH')
        is_valid_env = False
        if has_env_vars:
            try:
                int(os.environ.get('TG_API_ID'))
                is_valid_env = True
            except ValueError:
                pass

        # 如果没有有效配置，跳过测试
        if not is_valid_env:
            pytest.skip("需要有效 TG_API_ID/TG_API_HASH 环境变量")

        # 设置临时配置目录（用于非敏感配置）
        test_config_dir = str(tmp_path / '.tg-mgr')
        monkeypatch.setenv('TG_MGR_CONFIG_DIR', test_config_dir)
        os.makedirs(test_config_dir, exist_ok=True)

        # 创建测试用 config.json（仅包含非敏感配置）
        import json
        config_file = os.path.join(test_config_dir, 'config.json')
        with open(config_file, 'w') as f:
            json.dump({'media_types': ['photo', 'video']}, f)

    def test_config_loads(self):
        """测试配置加载功能"""
        from utils.telegram_client import get_config

        config = get_config()
        assert 'api_id' in config
        assert 'api_hash' in config

    def test_config_values(self):
        """测试配置值类型"""
        from utils.telegram_client import get_config

        config = get_config()
        assert isinstance(config['api_id'], int)
        assert isinstance(config['api_hash'], str)


class TestIsInteractive:
    """测试 is_interactive 函数"""

    def test_is_interactive_function(self):
        """测试 is_interactive 函数存在"""
        from utils.telegram_client import is_interactive
        assert callable(is_interactive)

    def test_is_interactive_returns_bool(self):
        """测试返回布尔值"""
        from utils.telegram_client import is_interactive
        result = is_interactive()
        assert isinstance(result, bool)
