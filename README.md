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
# Install dependencies via uv
uv sync

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
| `views_limit` | 50 | Top N high-views messages (views > 8x avg) |
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
tg info -1001234567890 20 -v 50  # Custom limits: reaction=20, views=50
```

**Analysis Output:**
- Forward sources (where messages are forwarded from)
- Top views (messages with views > 8x average, max 50 by default)
- High-reaction messages (likes + hearts, max 10 by default)

**Parameters:**
- `reaction_limit` positional arg: high-reaction message count limit
- `-v, --views-limit`: high-views message count limit (overrides config)

### forward - Message Forwarding

```bash
# Basic forwarding
tg forward -1001234567890 -o -100555666777              # Single channel to target
tg forward -1001234567890 -1009876543210 -o -100555666777  # Multiple sources

# Recursive forwarding (-r 0 disables recursion)
tg forward -1001234567890 -o -100555666777 -r 3       # Depth 3, discovers source channels

# Force mode (bypass restrictions via download/re-upload)
tg forward -1001234567890 -o -100555666777 -f          # Shows preview first, then forwards

# Custom limits (override config.json defaults)
tg forward -1001234567890 -o -100555666777 -l 20 -v 100  # reaction=20, views=100

# Check before forward (skip existing messages)
tg forward -1001234567890 -o -100555666777 -c

# Combine all options
tg forward -1001234567890 -o -100555666777 -r 3 -f -c -l 20 -v 100

# Direct message link forwarding (single message, no recursion)
tg forward https://t.me/c/1234567890/100 -o -100555666777
tg forward https://t.me/c/1234567890/100 -o -100555666777 -f
```

**Parameters:**
| Flag | Description | Default |
|------|-------------|---------|
| `-o, --target` | Target channel ID | From config.json |
| `-c, --check` | Check if message exists before forwarding | False |
| `-r, --depth` | Recursion depth (0=disabled) | 5 |
| `-f, --force` | Force forward (download & re-upload) | False |
| `-l, --limit` | High-reaction message limit | From config (10) |
| `-v, --views-limit` | High-views message limit | From config (50) |

**Features:**
- High-reaction message filtering (likes + hearts)
- High-views message filtering (views > 8x average)
- Media group support (bidirectional search for complete groups)
- Recursive depth forwarding (-r discovers source channels at each level)
- Force mode (-f downloads content and re-uploads to bypass restrictions)
- Direct message link forwarding (single message, no recursion)

**Force Mode Flow:**
1. Shows preview with message count, media size, and message list
2. User confirms with 'y'
3. Downloads media to temp directory
4. Re-uploads to target channel
5. Cleans up temp files

**Config Precedence:** CLI argument > config.json > hard-coded default

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
