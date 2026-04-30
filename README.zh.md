# tg-mgr - Telegram 频道管理工具

基于 Pyrogram 的 Telegram 频道管理工具，支持消息同步、去重、清理、过滤和导出。

## 功能特点

- **同步 (Sync)**: 增量同步消息，实时显示进度
- **去重 (Deduplicate)**: 检测并删除重复媒体文件
- **清理 (Clean)**: 移除无效/受限消息
- **过滤 (Filter)**: 按文件大小过滤媒体（如 >1GB 或 <1MB）
- **导出 (Export)**: 导出为 Telegram Desktop 格式（JSON + HTML）
- **信息 (Info)**: 分析频道统计（转发来源、高反应消息）
- **转发 (Forward)**: 在频道间复制高反应消息

## 安装

### 一键安装（推荐）

```bash
bash install.sh
```

自动安装 `tg` 全局命令并创建默认配置目录 `~/.tg-mgr/`。

### 手动安装

```bash
# 安装依赖
uv pip install -r requirements.txt

# 安装全局命令
uv tool install --editable .
```

卸载：`uv tool uninstall tg-mgr`

## 配置

配置目录：`~/.tg-mgr/`（可通过环境变量 `TG_MGR_CONFIG_DIR` 自定义）

### ~/.tg-mgr/.env（API 凭证）

```bash
TG_API_ID=12345
TG_API_HASH=your_api_hash_here
TG_BOT_TOKEN=your_bot_token_here
TG_CHANNEL_ID=-1001234567890
```

从 https://my.telegram.org 获取 API 凭证

### ~/.tg-mgr/config.json（应用配置）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `forward_limit` | 10 | 转发来源 TOP 数量 |
| `reaction_limit` | 10 | 高反应消息 TOP 数量 |
| `download_dir` | ~/Downloads/Telegram | 媒体下载目录 |
| `max_retries` | 5 | API 最大重试次数 |
| `media_types` | 全部类型 | 支持的媒体类型 |

## 使用方法

```bash
tg <module> [参数]
```

### clean - 同步与清理

```bash
tg clean              # 仅同步
tg clean -d           # 同步 + 去重
tg clean -i           # 同步 + 清理无效
tg clean -diu         # 同步 + 去重 + 清理
tg clean -f           # 强制重置数据库
```

### filter - 媒体过滤

```bash
tg filter                          # 默认：1MB~1GB 范围外
tg filter --min-size 1048576       # 大于 1MB 的文件
tg filter --max-size 1048576       # 小于 1MB 的文件
tg filter --min-size 0 --max-size 1048576  # 小于 1MB
```

### export - 归档导出

```bash
tg export                            # 默认频道
tg export -1001234567890             # 单个频道
tg export -1001234567890 -1009876543210  # 多个频道
tg export https://t.me/c/1234567890/100   # 从消息链接
```

功能特性：Telegram Desktop 格式、媒体下载、断点续传、增量导出

### info - 频道信息分析

```bash
tg info                      # 列出所有频道
tg info -1001234567890       # 分析指定频道
tg info -1001234567890 20    # 高反应消息 TOP 20
```

### forward - 消息转发

```bash
tg forward -1001234567890                       # 转发到默认目标
tg forward -1001234567890 -o -100555666777      # 指定目标频道
tg forward -1001234567890 -c                    # 检查模式
```

### init - 交互式设置

```bash
tg init    # 交互式配置向导
```

### sessions - 会话管理

```bash
tg sessions    # 列出和管理 Telegram 会话
```

---

## 开发

详细开发文档请参阅 [CLAUDE.md](CLAUDE.md)（英文）。
