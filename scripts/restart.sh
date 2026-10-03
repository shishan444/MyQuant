#!/usr/bin/env bash
# MyQuant 重启脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色
GREEN='\033[0;32m'; NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $*"; }

info "正在停止服务..."
"$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true

sleep 2

info "正在启动服务..."
"$SCRIPT_DIR/start.sh"

echo ""
info "重启完成"
