#!/bin/bash
set -e

# tg-mgr 一键安装脚本
# 配置目录：~/.tg-mgr（可通过环境变量 TG_MGR_CONFIG_DIR 自定义）

# Parse arguments
SKIP_CONFIRM=false
while getopts "yh" opt; do
    case $opt in
        y) SKIP_CONFIRM=true ;;
        h) echo "Usage: $0 [-y]"; exit 0 ;;
    esac
done

CONFIG_DIR="${TG_MGR_CONFIG_DIR:-$HOME/.tg-mgr}"

echo "=========================================="
echo "tg-mgr 安装脚本"
echo "=========================================="
echo ""
echo "配置目录: $CONFIG_DIR"
echo ""

if [ "$SKIP_CONFIRM" = false ]; then
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 创建配置目录
mkdir -p "$CONFIG_DIR"

# 安装全局命令
echo "[1/4] 安装 tg-mgr 全局命令..."
uv tool install --editable .

# 安装依赖
echo "[2/4] 安装项目依赖..."
uv pip install -r requirements.txt

# 复制配置文件
echo "[3/4] 配置 Telegram 凭证..."

if [ ! -f "$CONFIG_DIR/.env" ]; then
    cp src/.env.example "$CONFIG_DIR/.env"
    echo "  - 已创建 $CONFIG_DIR/.env（请编辑填入您的 API 凭证）"
else
    echo "  - .env 已存在，跳过"
fi

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp config.json "$CONFIG_DIR/config.json"
    echo "  - 已创建 $CONFIG_DIR/config.json"
else
    echo "  - config.json 已存在，跳过"
fi

echo ""
echo "[4/4] 安装完成！"
echo ""
echo "=========================================="
echo "后续步骤："
echo "1. 编辑 $CONFIG_DIR/.env 填入您的 Telegram API 凭证"
echo "   - TG_API_ID 和 TG_API_HASH 来自 https://my.telegram.org"
echo "   - TG_BOT_TOKEN 来自 @BotFather（可选）"
echo "   - TG_CHANNEL_ID 为您的目标频道 ID"
echo ""
echo "2. 运行 'tg info' 开始使用"
echo "=========================================="