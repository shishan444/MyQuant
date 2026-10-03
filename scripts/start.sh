#!/usr/bin/env bash
# MyQuant 统一启动入口：API(:8000) + MCP(:8800) + Web 前端(:8080)
# 三进程默认一同启动（单一入口原则——MCP 是应用组成部分，不游离在外）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$APP_DIR/web"
VENV_PYTHON="$APP_DIR/venv/bin/python"
API_PID_FILE="$APP_DIR/.myquant_api.pid"
MCP_PID_FILE="$APP_DIR/.myquant_mcp.pid"
WEB_PID_FILE="$APP_DIR/.myquant_web.pid"
LOG_DIR="$APP_DIR/logs"

# 默认配置（A2 收口：默认仅本机可达；显式跨机经 MYQUANT_API_HOST 开启——
# 须 export 传导给 `-m api` 子进程，此前变量未导出、设置失效）
API_HOST="${MYQUANT_API_HOST:-127.0.0.1}"
export MYQUANT_API_HOST="$API_HOST"
API_PORT="${MYQUANT_API_PORT:-8000}"
MCP_PORT="${MYQUANT_MCP_PORT:-8800}"
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

# PID 文件通用检查：在运行返回 0（带 warn），残留清理返回 1
_check_pid_running() {
    local pid_file="$1" label="$2" port="$3"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(<"$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            warn "$label 已在运行 (PID: $pid, 端口: $port)"
            return 0
        else
            warn "发现残留 $label PID 文件，清理中..."
            rm -f "$pid_file"
        fi
    fi
    return 1
}

# 创建日志目录
ensure_dirs() {
    mkdir -p "$LOG_DIR"
}

# TCP 探活（MCP streamable-http 端点仅收 POST，TCP 层探活足够）
tcp_probe() {
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# 启动 API 服务
start_api() {
    _check_pid_running "$API_PID_FILE" "API 服务" "$API_PORT" && return 0
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

# 启动 MCP server（streamable-http 常驻）
start_mcp() {
    _check_pid_running "$MCP_PID_FILE" "MCP 服务" "$MCP_PORT" && return 0
    ensure_dirs

    local log_file="$LOG_DIR/mcp.log"
    info "启动 MCP server (streamable-http)..."
    info "  端口: $MCP_PORT"
    info "  日志: $log_file"

    cd "$APP_DIR"
    MYQUANT_MCP_PORT="$MCP_PORT" PYTHONPATH="$APP_DIR" \
        nohup "$VENV_PYTHON" -m mcpserver.server \
        >> "$log_file" 2>&1 &

    local pid=$!
    echo "$pid" > "$MCP_PID_FILE"

    local retries=20
    while ((retries-- > 0)); do
        if tcp_probe "$MCP_PORT"; then
            info "MCP 服务启动成功 (PID: $pid)"
            info "MCP 端点: http://127.0.0.1:$MCP_PORT/mcp"
            return 0
        fi
        sleep 1
    done

    if kill -0 "$pid" 2>/dev/null; then
        warn "MCP 进程已启动 (PID: $pid)，但端口探活未通过，请查看日志:"
        warn "  tail -f $log_file"
    else
        error "MCP 启动失败，请查看日志:"
        error "  cat $log_file"
        rm -f "$MCP_PID_FILE"
        exit 1
    fi
}

# 启动 Web 前端
start_web() {
    _check_pid_running "$WEB_PID_FILE" "Web 前端" "$WEB_PORT" && return 0

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
MyQuant 统一启动入口（API + MCP + Web 默认一同启动）

用法: $0 [选项]

选项:
    -a, --api         仅启动 API 服务
    -m, --mcp         仅启动 MCP server
    -w, --web         仅启动 Web 前端
    -h, --help        显示此帮助信息

环境变量:
    MYQUANT_API_HOST  API 主机 (默认: 127.0.0.1；跨机访问显式开启)
    MYQUANT_API_PORT  API 端口 (默认: 8000)
    MYQUANT_MCP_PORT  MCP 端口 (默认: 8800)
    MYQUANT_WEB_PORT  Web 端口 (默认: 8080；如需 5173 开发端口:
                      MYQUANT_WEB_PORT=5173 $0)

示例:
    $0                # 启动 API + MCP + Web
    $0 --api          # 仅启动 API
    $0 --mcp          # 仅启动 MCP
EOF
}

# 主流程（启动顺序 API → MCP → Web：MCP 消费 API，API 就绪后再起）
main() {
    local mode="all"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--api)  mode="api"; shift ;;
            -m|--mcp)  mode="mcp"; shift ;;
            -w|--web)  mode="web"; shift ;;
            -h|--help) show_help; exit 0 ;;
            *)         error "未知选项: $1"; show_help; exit 1 ;;
        esac
    done

    if [[ "$mode" == "all" || "$mode" == "api" ]]; then
        check_venv
        start_api
    fi

    if [[ "$mode" == "all" || "$mode" == "mcp" ]]; then
        check_venv
        start_mcp
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
    info "MCP:    http://127.0.0.1:$MCP_PORT/mcp"
    info "Web:    http://localhost:$WEB_PORT"
    info "================================"
    info ""
    info "Agent 接入（一次性配置，任何 MCP 客户端）:"
    info "  claude mcp add --transport http myquant-research \\"
    info "      http://127.0.0.1:$MCP_PORT/mcp"
    info ""
    info "查看日志:"
    info "  tail -f $LOG_DIR/api.log"
    info "  tail -f $LOG_DIR/mcp.log"
    info "  tail -f $LOG_DIR/web.log"
    info ""
    info "停止服务:"
    info "  $SCRIPT_DIR/stop.sh"
    info ""
    info "重启服务:"
    info "  $SCRIPT_DIR/restart.sh"
}

main "$@"
