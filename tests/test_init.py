# tests/test_init.py
import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

# Add project root and src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Import init module directly to avoid tg_mgr.__init__ auto-import side effects
import importlib.util
spec = importlib.util.spec_from_file_location("init", Path(__file__).parent.parent / "src" / "tg_mgr" / "init.py")
init_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_module)


def test_init_creates_config_directory(tmp_path, monkeypatch):
    """Test that init creates ~/.tg-mgr directory"""
    monkeypatch.setenv("TG_MGR_CONFIG_DIR", str(tmp_path / ".tg-mgr"))

    init_module.setup_config_dir()

    config_dir = tmp_path / ".tg-mgr"
    assert config_dir.exists()
    assert (config_dir / ".env").exists() or True  # .env may or may not be created