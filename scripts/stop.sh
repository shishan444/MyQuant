#!/usr/bin/env bash
# MyQuant 应用停止脚本
# 同时停止 API 后端和 Web 前端
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
API_PID_FILE="$APP_DIR/.myquant_api.pid"
WEB_PID_FILE="$APP_DIR/.myquant_web.pid"
API_PORT="${MYQUANT_API_PORT:-8000}"
WEB_PORT="${MYQUANT_WEB_PORT:-8080}"

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 停止 API 服务
stop_api() {
    local stopped=false

    if [[ -f "$API_PID_FILE" ]]; then
        local pid
        pid=$(<"$API_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            info "停止 API 服务 (PID: $pid)..."
            kill "$pid"
            local retries=10
            while ((retries-- > 0)) && kill -0 "$pid" 2>/dev/null; do
                sleep 1
            done
            if kill -0 "$pid" 2>/dev/null; then
                warn "API 进程未响应，强制终止..."
                kill -9 "$pid" 2>/dev/null || true
            fi
            rm -f "$API_PID_FILE"
            info "API 服务已停止"
            stopped=true
        else
            rm -f "$API_PID_FILE"
        fi
    fi

    if [[ "$stopped" == "false" ]]; then
        local pids
        pids=$(lsof -ti :"$API_PORT" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            info "通过端口 $API_PORT 发现 API 进程: $pids"
            for pid in $pids; do
                kill "$pid" 2>/dev/null || true
            done
            sleep 2
            info "API 服务已停止"
        fi
    fi
}

# 停止 Web 前端
stop_web() {
    local stopped=false

    if [[ -f "$WEB_PID_FILE" ]]; then
        local pid
        pid=$(<"$WEB_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            info "停止 Web 前端 (PID: $pid)..."
            kill "$pid" 2>/dev/null || true
            rm -f "$WEB_PID_FILE"
            info "Web 前端已停止"
            stopped=true
        else
            rm -f "$WEB_PID_FILE"
        fi
    fi

    if [[ "$stopped" == "false" ]]; then
        local pids
        pids=$(lsof -ti :"$WEB_PORT" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            info "通过端口 $WEB_PORT 发现 Web 进程: $pids"
            for pid in $pids; do
                kill "$pid" 2>/dev/null || true
            done
            sleep 1
            info "Web 前端已停止"
        fi
    fi

    # 兜底: 通过进程名查找残留 vite 进程
    local stale_pids
    stale_pids=$(ps aux | grep '[n]ode.*vite' | awk '{print $2}' || true)
    if [[ -n "$stale_pids" ]]; then
        info "发现残留 vite 进程: $stale_pids"
        for pid in $stale_pids; do
            kill "$pid" 2>/dev/null || true
        done
        sleep 1
        info "残留 vite 进程已清理"
    fi
}

# 主流程
main() {
    stop_web
    stop_api
    info "MyQuant 所有服务已停止"
}

main "$@"
