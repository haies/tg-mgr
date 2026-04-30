# tg-mgr - Telegram Channel Management Tool

A Telegram channel management tool based on Pyrogram, supporting message synchronization, deduplication, cleanup, filtering, and export.

## Features

- **Sync**: Incremental message synchronization with progress tracking
- **Deduplicate**: Detect and remove duplicate media files
- **Clean**: Remove invalid/restricted messages
- **Filter**: Filter media by file size (e.g., >1GB or <1MB)
- **Export**: Export messages to Telegram Desktop format (JSON + HTML)
- **Info**: Analyze channel statistics (forward sources, high-reaction messages)
- **Forward**: Copy high-reaction messages between channels

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
tg clean -diu         # Sync + deduplicate + cleanup
tg clean -f           # Force reset database
```

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
tg info                     # List all channels
tg info -1001234567890      # Analyze specific channel
tg info -1001234567890 20   # Top 20 high-reaction messages
```

### forward - Message Forwarding

```bash
tg forward -1001234567890                       # To default target
tg forward -1001234567890 -o -100555666777     # Specify target
tg forward -1001234567890 -c                   # Check mode
```

### init - Interactive Setup

```bash
tg init    # Interactive configuration wizard
```

### sessions - Session Management

```bash
tg sessions    # List and manage Telegram sessions
```

---

## Development

For detailed developer documentation, see [CLAUDE.md](CLAUDE.md).
