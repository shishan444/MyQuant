#!/usr/bin/env bash
# MyQuant 应用启动脚本
# 启动 FastAPI 后端服务 + Web 前端
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$APP_DIR/web"
VENV_PYTHON="$APP_DIR/venv/bin/python"
API_PID_FILE="$APP_DIR/.myquant_api.pid"
WEB_PID_FILE="$APP_DIR/.myquant_web.pid"
LOG_DIR="$APP_DIR/logs"

# 默认配置
API_HOST="${MYQUANT_API_HOST:-0.0.0.0}"
API_PORT="${MYQUANT_API_PORT:-8000}"
WEB_PORT="${MYQUANT_WEB_PORT:-8080}"

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 检查虚拟环境
check_venv() {
    if [[ ! -x "$VENV_PYTHON" ]]; then
        error "虚拟环境不存在: $VENV_PYTHON"
        echo "请先执行: python3 -m venv $APP_DIR/venv && source $APP_DIR/venv/bin/activate && pip install -r $APP_DIR/requirements.txt"
        exit 1
    fi
}

# 检查 API 是否已在运行
check_api_running() {
    if [[ -f "$API_PID_FILE" ]]; then
        local pid
        pid=$(<"$API_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            warn "API 服务已在运行 (PID: $pid, 端口: $API_PORT)"
            return 0
        else
            warn "发现残留 API PID 文件，清理中..."
            rm -f "$API_PID_FILE"
        fi
    fi
    return 1
}

# 创建日志目录
ensure_dirs() {
    mkdir -p "$LOG_DIR"
}

# 启动 API 服务
start_api() {
    check_api_running && return 0
    ensure_dirs

    local log_file="$LOG_DIR/api.log"
    info "启动 FastAPI 服务..."
    info "  主机: $API_HOST"
    info "  端口: $API_PORT"
    info "  日志: $log_file"

    cd "$APP_DIR"
    PYTHONPATH="$APP_DIR" nohup "$VENV_PYTHON" -m api \
        >> "$log_file" 2>&1 &

    local pid=$!
    echo "$pid" > "$API_PID_FILE"

    local retries=40
    while ((retries-- > 0)); do
        if curl -sf "http://localhost:$API_PORT/api/health" >/dev/null 2>&1; then
            info "API 服务启动成功 (PID: $pid)"
            info "API 地址: http://localhost:$API_PORT"
            info "API 文档: http://localhost:$API_PORT/docs"
            return 0
        fi
        sleep 1
    done

    if kill -0 "$pid" 2>/dev/null; then
        info "API 进程已启动 (PID: $pid)，但健康检查未通过，请查看日志:"
        info "  tail -f $log_file"
    else
        error "API 启动失败，请查看日志:"
        error "  cat $log_file"
        rm -f "$API_PID_FILE"
        exit 1
    fi
}

# 检查 Web 前端是否已在运行
check_web_running() {
    if [[ -f "$WEB_PID_FILE" ]]; then
        local pid
        pid=$(<"$WEB_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            warn "Web 前端已在运行 (PID: $pid, 端口: $WEB_PORT)"
            return 0
        else
            warn "发现残留 Web PID 文件，清理中..."
            rm -f "$WEB_PID_FILE"
        fi
    fi
    return 1
}

# 启动 Web 前端
start_web() {
    check_web_running && return 0

    if [[ ! -d "$WEB_DIR" ]] || [[ ! -f "$WEB_DIR/package.json" ]]; then
        warn "未找到 Web 前端目录: $WEB_DIR，跳过前端启动"
        return 0
    fi

    if [[ ! -d "$WEB_DIR/node_modules" ]]; then
        warn "未找到 node_modules，正在安装依赖..."
        (cd "$WEB_DIR" && pnpm install)
    fi

    local log_file="$LOG_DIR/web.log"
    info "启动 Web 前端..."
    info "  端口: $WEB_PORT"
    info "  日志: $log_file"

    cd "$WEB_DIR"
    pnpm exec vite --host --port "$WEB_PORT" >> "$log_file" 2>&1 &
    local pid=$!
    echo "$pid" > "$WEB_PID_FILE"
    cd "$APP_DIR"

    local retries=15
    while ((retries-- > 0)); do
        if grep -q "Local:" "$log_file" 2>/dev/null; then
            info "Web 前端启动成功 (PID: $pid)"
            info "Web 地址: http://localhost:$WEB_PORT"
            return 0
        fi
        sleep 1
    done

    if kill -0 "$pid" 2>/dev/null; then
        warn "Web 前端进程已启动 (PID: $pid)，但未检测到就绪日志，请查看:"
        warn "  tail -f $log_file"
    else
        error "Web 前端启动失败，请查看日志:"
        error "  cat $log_file"
        rm -f "$WEB_PID_FILE"
    fi
}

# 显示帮助
show_help() {
    cat << EOF
MyQuant 启动脚本

用法: $0 [选项]

选项:
    -a, --api         仅启动 API 服务
    -w, --web         仅启动 Web 前端
    -h, --help        显示此帮助信息

环境变量:
    MYQUANT_API_HOST  API 主机 (默认: 0.0.0.0)
    MYQUANT_API_PORT  API 端口 (默认: 8000)
    MYQUANT_WEB_PORT  Web 端口 (默认: 8080)

示例:
    $0                # 启动 API + Web
    $0 --api          # 仅启动 API
    $0 --web          # 仅启动 Web
EOF
}

# 主流程
main() {
    local mode="all"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--api)  mode="api"; shift ;;
            -w|--web)  mode="web"; shift ;;
            -h|--help) show_help; exit 0 ;;
            *)         error "未知选项: $1"; show_help; exit 1 ;;
        esac
    done

    if [[ "$mode" == "all" || "$mode" == "api" ]]; then
        check_venv
        start_api
    fi

    if [[ "$mode" == "all" || "$mode" == "web" ]]; then
        start_web
    fi

    echo ""
    info "================================"
    info "MyQuant 服务已启动"
    info "================================"
    info "API:    http://localhost:$API_PORT"
    info "文档:   http://localhost:$API_PORT/docs"
    info "Web:    http://localhost:$WEB_PORT"
    info "================================"
    info ""
    info "查看日志:"
    info "  tail -f $LOG_DIR/api.log"
    info "  tail -f $LOG_DIR/web.log"
    info ""
    info "停止服务:"
    info "  $SCRIPT_DIR/stop.sh"
    info ""
    info "重启服务:"
    info "  $SCRIPT_DIR/restart.sh"
}

main "$@"
