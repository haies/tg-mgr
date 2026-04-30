"""Interactive setup command."""
import os
from pathlib import Path


def setup_config_dir():
    """Create config directory at TG_MGR_CONFIG_DIR."""
    config_dir = Path(os.environ.get("TG_MGR_CONFIG_DIR", "~/.tg-mgr")).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

def run_init():
    """Interactive initialization wizard."""
    config_dir = setup_config_dir()
    env_file = config_dir / ".env"
    config_file = config_dir / "config.json"

    print("=== tg-mgr Setup Wizard ===")
    print(f"Config directory: {config_dir}")

    api_id = input("Enter TG_API_ID: ").strip()
    api_hash = input("Enter TG_API_HASH: ").strip()
    bot_token = input("Enter TG_BOT_TOKEN (optional, press Enter to skip): ").strip()

    with open(env_file, "w") as f:
        f.write(f"TG_API_ID={api_id}\n")
        f.write(f"TG_API_HASH={api_hash}\n")
        if bot_token:
            f.write(f"TG_BOT_TOKEN={bot_token}\n")

    if not config_file.exists():
        with open(config_file, "w") as f:
            f.write('{\n  "forward_limit": 10,\n  "reaction_limit": 10\n}\n')

    print(f"\nConfig created at {config_dir}")
    print("Edit .env to add your API credentials, then run 'tg clean' to start.")


def main():
    """CLI entry point for tg init command."""
    run_init()
