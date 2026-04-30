import os
import pytest
from pathlib import Path
import sys

# Add project root and src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Import sessions module directly to avoid tg_mgr.__init__ auto-import side effects
import importlib.util
spec = importlib.util.spec_from_file_location("sessions", Path(__file__).parent.parent / "src" / "tg_mgr" / "sessions.py")
sessions_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sessions_module)

from datetime import datetime, timedelta


def test_cleanup_removes_old_sessions(tmp_path, monkeypatch):
    """Test session cleanup removes .session files older than 30 days."""
    monkeypatch.setenv("TG_MGR_CONFIG_DIR", str(tmp_path))

    # sessions_module is already imported above via importlib
    sessions = sessions_module

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    old_session = session_dir / "old.session"
    old_session.touch()
    old_mtime = (datetime.now() - timedelta(days=60)).timestamp()
    os.utime(old_session, (old_mtime, old_mtime))

    new_session = session_dir / "new.session"
    new_session.touch()

    result = sessions.cleanup_sessions(days=30)

    assert not old_session.exists()
    assert new_session.exists()
    assert "old.session" in result["removed"]