#!/usr/bin/env bash
# svc.sh —— MyQuant 服务管理（三端一体：api + mcp + web）
#
# 定位（add-e2e-isolated-harness H4）：常驻服务组为用户日常观察运行面
# 专用——固定端口 8000/8800/8080、单份 pid 文件与日志（并发实例互踢）。
# 任何测试套件不得依赖或操作本服务组；e2e 测试一律走 tests/e2e 的
# 自管 fixture（随机端口+临时业务库，见 tests/e2e/harness.py）。
#
# 用法: scripts/svc.sh start|stop|restart|status
# 旧入口 start.sh / stop.sh / restart.sh 为转发薄壳。
#
# 设计取舍（用户裁决 2026-10-30 简化版）：单用户单机无调度需求——
# 直白优先，失败即报错退出由人工处置；不做回滚/身份核验/断言语句化。
# 保留四件实事：真探活判就绪（杜绝日志 grep 假成功）、stop 只杀端口
# 监听者（-sTCP:LISTEN——不带过滤会误杀连着端口的无关应用，Surge 实录）、
# stop 末尾指纹循环收孤儿（绕过脚本拉起的进程实录）、三端一体
# （start 三端齐起、stop 三端全停）。

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$APP_DIR/venv/bin/python"
LOG_DIR="$APP_DIR/logs"
API_PORT="${MYQUANT_API_PORT:-8000}"
MCP_PORT="${MYQUANT_MCP_PORT:-8800}"
WEB_PORT="${MYQUANT_WEB_PORT:-8080}"

info()  { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; }

# 拉起一个服务并真探活（curl/tcp 握手——不看日志，杜绝追加残留假成功）
# 注①：cd 后分号分隔再后台化——`cd x && cmd &` 的列表后台化会让 $() 死锁
# 注②：环境变量经 env 传（`nohup VAR=x cmd` 会把 VAR=x 当命令名执行）
start_one() {  # $1=名称 $2=端口 $3=工作目录 $4=探活命令 $5=启动命令 $6=日志
    local name="$1" port="$2" dir="$3" probe="$4" cmd="$5" log="$6" pid tries=40
    if lsof -ti :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        error "$name 端口 $port 已被占用——确认后用 scripts/svc.sh stop 清理再重跑"
        return 1
    fi
    pid=$( cd "$dir"; nohup $cmd >> "$log" 2>&1 & echo $! )
    echo "$pid" > "$APP_DIR/.myquant_${name}.pid"
    while ((tries-- > 0)); do
        if eval "$probe" >/dev/null 2>&1; then
            info "$name 已启动 (PID: $pid)"
            return 0
        fi
        kill -0 "$pid" 2>/dev/null || { error "$name 启动失败（进程退出），看日志: tail $log"; rm -f "$APP_DIR/.myquant_${name}.pid"; return 1; }
        sleep 1
    done
    error "$name 起了但探活未过，看日志: tail $log"
    return 1
}

# 停一个服务：pid 文件 → 端口监听者 → 指纹孤儿（三路收口）+等待退出
stop_one() {  # $1=名称 $2=端口 $3=进程指纹
    local name="$1" port="$2" fp="$3" pid w
    # 注：pidfile 引用 name 须独立声明——bash 3.2 的 local 同语句前向引用
    # 在 set -u 下报 unbound（最小复现在案）
    local pidfile="$APP_DIR/.myquant_${name}.pid"
    if [[ -f "$pidfile" ]]; then
        kill "$(cat "$pidfile")" 2>/dev/null || true
        rm -f "$pidfile"
    fi
    for pid in $(lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null || true); do
        kill "$pid" 2>/dev/null || true
    done
    for pid in $(ps aux | grep -E "$fp" | grep -v grep | awk '{print $2}'); do
        kill "$pid" 2>/dev/null || true
    done
    # 等待退出（TERM 优雅退出需数秒——不等则端口不释放，下一轮 start 会拒绝）
    for w in 1 2 3 4 5 6 7 8 9 10; do
        if ! lsof -ti :"$port" -sTCP:LISTEN >/dev/null 2>&1 \
           && ! ps aux | grep -E "$fp" | grep -v grep >/dev/null; then
            break
        fi
        sleep 1
    done
    info "$name 已停止"
}

cmd_start() {
    mkdir -p "$LOG_DIR"
    start_one api "$API_PORT" "$APP_DIR" "curl -sf http://localhost:$API_PORT/api/health" \
        "env PYTHONPATH=$APP_DIR $VENV_PY -m api" "$LOG_DIR/api.log" || exit 1
    start_one mcp "$MCP_PORT" "$APP_DIR" "(exec 3<>/dev/tcp/127.0.0.1/$MCP_PORT)" \
        "env MYQUANT_MCP_PROFILE=${MYQUANT_MCP_PROFILE:-research} MYQUANT_MCP_PORT=$MCP_PORT PYTHONPATH=$APP_DIR $VENV_PY -m mcpserver.server" "$LOG_DIR/mcp.log" || exit 1
    start_one web "$WEB_PORT" "$APP_DIR/web" "curl -sf http://localhost:$WEB_PORT/" \
        "pnpm exec vite --host --port $WEB_PORT" "$LOG_DIR/web.log" || exit 1
    info "三端已就绪: api=$API_PORT mcp=$MCP_PORT web=${WEB_PORT}（日志目录: ${LOG_DIR}/）"
}

cmd_stop() {
    stop_one web "$WEB_PORT" '[n]ode.*vite'
    stop_one mcp "$MCP_PORT" "$VENV_PY -m mcpserver"
    stop_one api "$API_PORT" "$VENV_PY -m api"
    info "三端已停止"
}

cmd_status() {
    for spec in "api $API_PORT" "mcp $MCP_PORT" "web $WEB_PORT"; do
        # shellcheck disable=SC2086
        set -- $spec
        if lsof -ti :"$2" -sTCP:LISTEN >/dev/null 2>&1; then
            echo "$1: 运行中 (端口 $2)"
        else
            echo "$1: 已停 (端口 $2)"
        fi
    done
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; sleep 1; cmd_start ;;
    status)  cmd_status ;;
    *)       echo "用法: $0 start|stop|restart|status" >&2; exit 1 ;;
esac
