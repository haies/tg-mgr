# CLAUDE.md - tg-mgr Developer Guide

## Project Overview

**tg-mgr** is a Telegram channel management CLI tool. It syncs messages, deduplicates content, exports chats, and forwards high-reaction messages between channels.

**Tech Stack:** Python 3.11+, Pyrogram 2.0.106 (MTProto), SQLite3, python-dotenv

**Entry Point:** `tg <module>` where module is clean, export, forward, filter, info, init, or sessions.

## Architecture

### Module Structure

- `src/tg_mgr/` - CLI entry point (dispatcher with lazy module loading)
- `src/modules/` - Feature modules (clean, export, filter, forward, info, **sync**)
- `src/utils/` - Utilities (telegram_client, telegram_link, file_sanitizer)
- `src/database/` - DB schema (`schema.sql`), connection management, and **messages.py** (messages table operations)
- `tests/` - Test suite with fixtures in `conftest.py`

### Configuration

- `~/.tg-mgr/.env` - API credentials (api_id, api_hash, bot_token)
- `~/.tg-mgr/config.json` - App settings
- Config priority: `TG_MGR_CONFIG_DIR` > `TG_MGR_DEV=1` > `~/.tg-mgr`

**config.json structure:**
```json
{
  "forward_limit": 10,
  "reaction_limit": 10,
  "views_limit": 50,
  "download_dir": "~/Downloads/Telegram",
  "max_retries": 5,
  "retry_delay_base": 1,
  "media_types": ["photo", "video", "document", "audio", "animation", "text", "video_note"]
}
```

### Database

- SQLite at `~/.tg-mgr/tmp/database/messages.db`
- Schema: `messages` table with key indexes on `reactions`, `file_unique_id`, `file_size`, `message_id`

### Messages Table Schema

| Field | Type | Description |
|-------|------|-------------|
| message_id | INTEGER | Telegram message ID (unique) |
| file_unique_id | TEXT | File unique identifier |
| file_size | INTEGER | File size in bytes |
| media_type | TEXT | Media type |
| caption | TEXT | Message text |
| is_duplicate | BOOLEAN | Is duplicate |
| is_valid | BOOLEAN | Is valid (0=restricted) |
| reactions | TEXT | JSON reaction stats `{"positive": 0, "heart": 0}` |
| source_id | INTEGER | Forward source channel ID |

## Key Commands

```bash
tg clean           # Sync messages
tg clean -diu     # Sync + deduplicate + cleanup invalid
tg export <id>    # Export channel to HTML/JSON
tg forward <src> -o <dst>  # Forward high-reaction messages
tg filter         # Find media by size
tg info           # List channels
tg init           # Interactive setup (NEW)
tg sessions       # Session cleanup (NEW)
```

## Development

### Setup

```bash
# Install via uv
uv tool install --editable .

# Run tests
uv run pytest tests/ -v

# Type check
uv run mypy src/

# Lint
uv run ruff check src/
```

### Testing Philosophy

- Each feature module should have corresponding `tests/test_<module>.py`
- Use fixtures from `tests/conftest.py`: `test_db`, `sample_messages`, `populated_db`
- Mock Telegram API calls - don't make real API requests in tests
- Run type checking: `mypy src/`

### Error Handling

- Use `TGMgrError` base exception pattern
- FloodWait: use exponential backoff via `retry_with_backoff()`
- Database errors: wrap in `DatabaseError`

## Common Patterns

### Adding a New Module

1. Create `src/modules/newmodule.py` (or `src/tg_mgr/newmodule.py` for internal modules)
2. Add to `src/tg_mgr/__init__.py` `_LAZY_IMPORTS` dict
3. Create `tests/test_newmodule.py`
4. Document in README.md

### Lazy Module Loading

Modules use lazy loading via `_LAZY_IMPORTS` in `src/tg_mgr/__init__.py`:

```python
_LAZY_IMPORTS = {
    "clean": ("modules", "clean"),      # external module
    "init": ("tg_mgr", "init"),          # internal module
}
```

### Adding a New CLI Flag

1. Add argument parser in the module's `add_cli_args()` function
2. Add test for the new flag
3. Update README usage examples

## Key Utilities

### Telegram Client (utils/telegram_client.py)

```python
from utils.telegram_client import get_client

client = get_client()
with client:
    # use client
```

### Link Generation (utils/telegram_link.py)

```python
from utils.telegram_link import generate_tg_link

link = generate_tg_link("-1001234567890", 12345)
# -> "https://t.me/c/1234567890/12345"
```

### File Sanitization (utils/file_sanitizer.py)

```python
from utils.file_sanitizer import sanitize_filename

clean_name = sanitize_filename("file<>name.txt")
# -> "file__name.txt"
```

## FloodWait Handling

All Pyrogram API calls must handle FloodWait:

```python
from pyrogram import errors
import time

try:
    client.delete_messages(CHANNEL_ID, message_id)
except errors.FloodWait as e:
    wait_time = max(e.value, 5)
    time.sleep(wait_time)
```

## Query Module (database/query.py)

Shared query functions for other modules:

```python
from database.query import (
    find_high_reaction_messages,      # High reaction messages
    find_reaction_messages_over_threshold,  # Messages above threshold
    find_large_media,                 # Large file queries
    get_forward_sources,               # Forward source statistics
)
```

## Sync Module (modules/sync.py)

Unified sync functionality for other modules:

```python
from modules.sync import sync_channel

sync_channel(channel_id="-1001234567890")  # Sync to temp DB first
```

## Messages Module (database/messages.py)

Messages table operations for database layer:

```python
from database.messages import (
    init_database,              # Initialize DB schema
    get_last_processed_id,      # Get last synced message ID
    insert_messages,             # Batch insert messages
    find_duplicates,             # Find duplicate messages
    find_invalid_messages,       # Find invalid messages
    update_message_duplicate,    # Mark message as duplicate
    get_message_stats,           # Get message statistics
    get_existing_files,          # Get existing file IDs for dedup
)
```

## Database Context Manager

Use `get_db()` for database connections (singleton pattern):

```python
from database import get_db

with get_db() as db:
    # db is a sqlite3.Connection
    cursor = db.execute("SELECT ...")
```

## Development Guidelines

1. **All feature changes must include tests and documentation updates**
2. **Session files and .env are in .gitignore** - sensitive info never committed
3. **Database operations use transactions** (`BEGIN IMMEDIATE`)
4. **All SQL queries must be parameterized** (SQL injection prevention)
5. **FloodWait exceptions must be handled** with exponential backoff retry

## Installation

```bash
# Using uv (recommended)
uv tool install --editable .

# Or using pip (legacy)
pip install -e .
```

## Dependency Management (uv)

```bash
# Add runtime dependency
uv add pyrogram

# Add dev dependency
uv add --dev pytest black mypy ruff

# Sync dependencies
uv sync

# Update lock file
uv lock
```

## Note on tgcrypto

`tgcrypto` is a C extension that may need separate installation on some platforms:

```bash
uv pip install tgcrypto
```
