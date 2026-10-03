#!/usr/bin/env bash
# MyQuant 开发模式脚本
# 同时启动 API 后端和 Web 前端
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$APP_DIR/web"
LOG_DIR="$APP_DIR/logs"

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }

# 子进程 PID
API_PID=""
WEB_PID=""

# 清理函数
cleanup() {
    echo ""
    info "正在停止服务..."
    if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
        kill "$WEB_PID" 2>/dev/null || true
    fi
    "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true
    rm -f "$LOG_DIR"/*.log
    info "已停止"
    exit 0
}

trap cleanup SIGINT SIGTERM

mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR"/*.log

info "================================"
info "MyQuant 开发模式"
info "================================"
echo ""

# 1. 启动 API 后端
info "启动 API 服务..."
"$SCRIPT_DIR/start.sh"
echo ""

# 2. 启动 Web 前端
if [[ -d "$WEB_DIR" ]] && [[ -f "$WEB_DIR/package.json" ]]; then
    info "启动 Web 前端..."
    cd "$WEB_DIR"

    if [[ ! -d "node_modules" ]]; then
        warn "未找到 node_modules，正在安装依赖..."
        pnpm install
    fi

    pnpm exec vite --host > "$LOG_DIR/web.log" 2>&1 &
    WEB_PID=$!
    info "Web 前端启动中 (PID: $WEB_PID)..."

    # 等待 vite 就绪
    retries=10
    while ((retries-- > 0)); do
        if grep -q "Local:" "$LOG_DIR/web.log" 2>/dev/null; then
            break
        fi
        sleep 1
    done
else
    warn "未找到 Web 前端目录: $WEB_DIR"
    warn "仅启动 API 服务"
fi

echo ""
info "================================"
info "服务已启动，按 Ctrl+C 停止"
info "================================"
echo ""
info "访问地址:"
info "  API:  ${BLUE}http://localhost:8000${NC}"
info "  文档: ${BLUE}http://localhost:8000/docs${NC}"
if [[ -n "$WEB_PID" ]]; then
    info "  Web:  ${BLUE}http://localhost:5173${NC}"
fi
echo ""

info "实时日志 (Ctrl+C 退出):"
echo ""

# 日志前缀
info "${BLUE}--- API 日志 ---${NC}"
tail -f "$LOG_DIR/api.log" 2>/dev/null &
API_PID=$!

if [[ -n "$WEB_PID" ]]; then
    info "${BLUE}--- Web 日志 ---${NC}"
    tail -f "$LOG_DIR/web.log" 2>/dev/null &
fi

wait
