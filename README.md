# tg-mgr - Telegram Channel Management Tool

A Telegram channel management CLI tool based on Pyrogram, supporting message synchronization, deduplication, cleanup, filtering, export, and high-reaction message forwarding.

## Features

- **clean**: Sync messages + detect/remove duplicates + cleanup invalid/junk
- **filter**: Find media by file size (>1GB or <1MB)
- **export**: Export channel to Telegram Desktop format (JSON + HTML with media)
- **info**: Analyze channel stats (forward sources, high-reaction messages, top views)
- **forward**: Copy high-reaction messages between channels with recursive forwarding
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

| `channel_id` | null | Default channel ID |
| `reaction_limit` | 200 | High-reaction result count limit |
| `views_limit` | 100 | High-views result count limit |
| `max_source_channels` | 10 | Max source channels for recursive forwarding |
| `download_dir` | ~/Downloads/Telegram | Media download directory |
| `max_retries` | 5 | API max retries |
| `retry_delay_base` | 1 | Base delay for exponential backoff (seconds) |
| `recursion_depth` | null | Default recursion depth (null=disabled, >0=depth) |
| `media_types` | all types | Supported media types |

## Usage

```bash
tg <module> [channels] [options]
```

All modules use `channels` as positional parameter. Use `tg <module> --help` for details.

---

### clean - Sync & Cleanup

```bash
tg clean                          # Sync channel from TG_CHANNEL_ID env var
tg clean -d                       # Sync + deduplicate
tg clean -i                       # Sync + cleanup invalid
tg clean -s                       # Sync + cleanup junk
tg clean -di                      # Sync + deduplicate + cleanup invalid
tg clean -dis                     # Sync + deduplicate + invalid + junk
tg clean -y                       # Preview mode (list only, no delete)
tg clean -R                       # Force reset database and re-sync
tg clean <channel1> <channel2>    # Multi-channel cleanup
```

**Channel Source:** When no channel is provided, reads from `TG_CHANNEL_ID` environment variable (or `channel_id` in config.json).

**Junk Message Detection:**
- Media (photo/video) + long text (>30 Chinese or >100 chars) + file <2MB
- Plain text messages (all considered junk)
- Media group messages are excluded from cleanup

**Preview Mode (-y):** Lists all pending deletions by type with media type breakdown before actual cleanup.

---

### filter - Media Size Filter

```bash
tg filter                        # Use channel_id from TG_CHANNEL_ID env or config
tg filter <channel_id>           # Specify channel
tg filter -m 1048576             # Min size: larger than 1MB
tg filter -M 1073741824          # Max size: smaller than 1GB
tg filter -m 0 -M 1048576        # Smaller than 1MB
```

---

### export - Archive Export

```bash
tg export                                  # Default channel
tg export <channel_id>                     # Single channel
tg export <channel1> <channel2>           # Multiple channels
tg export https://t.me/c/1234567890/100    # From message URL
tg export <channel_id> -p                  # Preview and confirm before download
tg export <channel_id> -l 50              # Only download files under 50MB
```

**Features:**
- Telegram Desktop format (JSON + HTML with media)
- Incremental export: repeat runs find the same directory and skip existing files
- Media deduplication based on actual filesystem (not state cache)
- New messages append to existing JSON/HTML, existing entries preserved
- Resume support: interrupted exports can continue from where they left off
- Preview mode (-p): show stats and confirm before downloading
- File size filter (-l): only download files under specified size

**Incremental Behavior:**
- Directory naming: `ChannelTitle/` (no timestamp) — repeat runs reuse same directory
- Media skip: checks if file already exists on disk before downloading
- JSON merge: new messages append to existing file, duplicates by message ID

---

### info - Channel Analysis

```bash
tg info                          # List all accessible channels
tg info <channel_id>             # Analyze specific channel
tg info <channel_id> -R          # Force reset and re-sync
tg info <channel_id> -l 20        # Top 20 high-reaction messages
tg info <channel_id> -v 50       # Top 50 high-views messages
```

**Analysis Output:**
- Forward sources: where messages are forwarded from (top N by message count)
- High-views: messages with views > (0.8 × max + 0.2 × avg) OR views > 8 × avg (for views > 0)
- High-reaction: messages with reactions > (0.8 × max + 0.2 × avg) OR reactions > 50 (for reactions ≥ 1)

---

### forward - Message Forwarding

```bash
# Basic forwarding (single source channel)
tg forward <channel_id> -o <target_channel>

# Multiple source channels
tg forward <channel1> <channel2> -o <target_channel>

# Recursive forwarding with depth
tg forward <channel_id> -o <target_channel> -r 3

# Force mode (bypass restrictions via download/re-upload)
tg forward <channel_id> -o <target_channel> -f

# Custom limits (override config.json)
tg forward <channel_id> -o <target_channel> -l 20 -v 100

# Check before forward
tg forward <channel_id> -o <target_channel> -c

# Combine options
tg forward <channel_id> -o <target_channel> -r 3 -f -c -l 20 -v 100

# Direct message link forwarding (single message, no recursion)
tg forward https://t.me/c/1234567890/100 -o <target_channel>
```

**Parameters:**

| Flag | Description | Default |
|------|-------------|---------|
| `channels` | Source channel ID(s) or message links | Required |
| `-o, --target` | Target channel ID (falls back to config's channel_id) | From config |
| `-c, --check` | Check if message exists before forwarding | False |
| `-r, --depth` | Recursion depth (None/0=disabled, >0=N layers) | From config (None) |
| `-f, --force` | Force forward (download & re-upload) | False |
| `-l, --limit` | High-reaction message limit | From config (200) |
| `-v, --views-limit` | High-views message limit | From config (100) |

**Recursion Depth Semantics:**
- `-r` not specified → No recursion (process only source channel)
- `-r 0` → Explicitly disabled
- `-r N` (N>0) → Recurse N layers, discovering source channels at each level

**Features:**
- High-reaction filtering: reactions > (0.8 × max + 0.2 × avg) OR reactions > 50 (for reactions ≥ 1)
- High-views filtering: views > (0.8 × max + 0.2 × avg) OR views > 8 × avg (for views > 0)
- Media group support (bidirectional search)
- Recursive depth forwarding (-r discovers source channels)
- Force mode (-f downloads and re-uploads to bypass restrictions)
- Direct message link forwarding (single message, no recursion)

---

### init - Interactive Setup

```bash
tg init    # Interactive configuration wizard
```

---

### sessions - Session Management

```bash
tg sessions    # List and manage Telegram sessions
```

---

## Database Schema

SQLite database at `~/.tg-mgr/tmp/database/messages.db`

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    file_unique_id TEXT NOT NULL,
    file_size INTEGER,
    media_type TEXT,
    caption TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_duplicate BOOLEAN DEFAULT 0,
    is_invalid BOOLEAN DEFAULT 0,
    is_junk BOOLEAN DEFAULT 0,
    source_id INTEGER,
    views INTEGER DEFAULT 0,
    media_group_id TEXT,
    channel_id INTEGER,
    media_group_size INTEGER DEFAULT 0,
    reactions INTEGER DEFAULT 0,
    UNIQUE(message_id, channel_id)
);
```

**Indexes:** idx_reactions, idx_file_size, idx_file_unique_id, idx_message_id, idx_timestamp, idx_channel_id, idx_media_group_id, idx_media_type, idx_is_invalid, idx_is_duplicate

---

## Development

For detailed developer documentation, see [CLAUDE.md](CLAUDE.md).