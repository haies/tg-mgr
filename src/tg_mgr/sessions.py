"""Session management and cleanup."""
import os
from datetime import datetime, timedelta
from pathlib import Path


def get_sessions_dir():
    """Get sessions directory path."""
    config_dir = Path(os.environ.get("TG_MGR_CONFIG_DIR", "~/.tg-mgr")).expanduser()
    return config_dir / "sessions"


def cleanup_sessions(days=30, dry_run=False):
    """Remove session files older than `days` days."""
    sessions_dir = get_sessions_dir()
    if not sessions_dir.exists():
        return {"removed": [], "kept": []}

    cutoff = datetime.now() - timedelta(days=days)
    removed = []
    kept = []

    for session_file in sessions_dir.glob("*.session"):
        mtime = datetime.fromtimestamp(session_file.stat().st_mtime)
        if mtime < cutoff:
            if not dry_run:
                session_file.unlink()
            removed.append(session_file.name)
        else:
            kept.append(session_file.name)

    return {"removed": removed, "kept": kept}


def run_sessions_cleanup(args):
    """CLI entry point for session cleanup."""
    result = cleanup_sessions(days=args.days, dry_run=args.dry_run)
    print(f"Removed: {len(result['removed'])} sessions")
    print(f"Kept: {len(result['kept'])} sessions")
    if result["removed"]:
        print(f"Files: {', '.join(result['removed'])}")
    return result
