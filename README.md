# tg-mgr - Telegram Channel Management Tool

A Telegram channel management tool based on Pyrogram, supporting message synchronization, deduplication, cleanup, filtering, and export.

## Features

- **Sync**: Incremental message synchronization with breakpoint resume
- **Deduplicate**: Detect and remove duplicate media files (window function optimization)
- **Clean**: Remove invalid/restricted messages and junk messages with signal handling
- **Filter**: Filter media by file size (e.g., >1GB or <1MB)
- **Export**: Export messages to Telegram Desktop format (JSON + HTML)
- **Info**: Analyze channel statistics (forward sources, high-reaction messages, top views)
- **Forward**: Copy high-reaction messages between channels with recursive depth and force mode
- **init**: Interactive setup wizard
- **sessions**: Session file management

## Installation

### One-click Install (Recommended)

```bash
bash install.sh
```

This installs the `tg` global command and creates default config at `~/.tg-mgr/`.

### Manual Install

```bash
# Install dependencies
uv pip install -r requirements.txt

# Install global command
uv tool install --editable .
```

To uninstall: `uv tool uninstall tg-mgr`

## Configuration

Config directory: `~/.tg-mgr/` (customizable via `TG_MGR_CONFIG_DIR` env var)

### ~/.tg-mgr/.env (API Credentials)

```bash
TG_API_ID=12345
TG_API_HASH=your_api_hash_here
TG_BOT_TOKEN=your_bot_token_here
TG_CHANNEL_ID=-1001234567890
```

Get API credentials from https://my.telegram.org

### ~/.tg-mgr/config.json (Application Config)

| Option | Default | Description |
|--------|---------|-------------|
| `forward_limit` | 10 | Top N forward sources |
| `reaction_limit` | 10 | Top N high-reaction messages |
| `download_dir` | ~/Downloads/Telegram | Media download directory |
| `max_retries` | 5 | API max retries |
| `media_types` | all types | Supported media types |

## Usage

```bash
tg <module> [args]
```

### clean - Sync & Cleanup

```bash
tg clean              # Sync only
tg clean -d           # Sync + deduplicate
tg clean -i           # Sync + cleanup invalid
tg clean -s           # Sync + cleanup junk messages
tg clean -di          # Sync + deduplicate + cleanup invalid
tg clean -dis         # Sync + deduplicate + invalid + junk
tg clean -y           # Preview mode (list only, no delete)
tg clean -f           # Force reset database
tg clean -u           # Force sync (breakpoint resume)
tg clean <channel1> <channel2>  # Multi-channel cleanup
```

**Junk Message Detection:**
- Media (photo/video) + long text (>30 Chinese or >100 chars) + file <2MB
- Plain text messages (all considered junk)
- Media group messages are excluded from cleanup

**Preview Mode (-y):** Lists all pending deletions by type with media type breakdown before actual cleanup.

### filter - Media Size Filter

```bash
tg filter                         # Default: outside 1MB~1GB
tg filter --min-size 1048576     # Files larger than 1MB
tg filter --max-size 1048576     # Files smaller than 1MB
tg filter --min-size 0 --max-size 1048576  # Smaller than 1MB
```

### export - Archive Export

```bash
tg export                           # Default channel
tg export -1001234567890            # Single channel
tg export -1001234567890 -1009876543210  # Multiple channels
tg export https://t.me/c/1234567890/100   # From message URL
```

Features: Telegram Desktop format, media download, resume support, incremental export

### info - Channel Analysis

```bash
tg info                          # List all channels
tg info -1001234567890           # Analyze specific channel
tg info -1001234567890 20        # Top 20 high-reaction messages
tg info -1001234567890 -f        # Force reset database and re-sync
```

**Analysis Output:**
- Forward sources (where messages are forwarded from)
- Top views (highest view count messages)
- High-reaction messages (likes + hearts)

### forward - Message Forwarding

```bash
tg forward -1001234567890                       # To default target
tg forward -1001234567890 -o -100555666777     # Specify target
tg forward -1001234567890 -c                   # Check mode
tg forward https://t.me/username/123            # Via username link
tg forward -1001234567890 -r 3                  # Recursive depth 3
tg forward -1001234567890 -f                    # Force mode (bypass restrictions)
```

**Features:**
- High-reaction message filtering (likes + hearts threshold)
- Media group support (bidirectional search for complete groups)
- Recursive depth forwarding with message link preservation (-r)
- Force mode for restricted channels (-f, downloads and re-uploads content)
- Direct message link forwarding (preserves original message)

### init - Interactive Setup

```bash
tg init    # Interactive configuration wizard
```

### sessions - Session Management

```bash
tg sessions    # List and manage Telegram sessions
```

---

## Database Schema

SQLite database at `~/.tg-mgr/tmp/database/messages.db`

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
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
);
```

**Indexes:** file_unique_id, media_type, is_valid, is_duplicate, timestamp, message_id, reactions, file_size

---

## Development

For detailed developer documentation, see [CLAUDE.md](CLAUDE.md).
