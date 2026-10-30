#!/usr/bin/env bash
# svc_lib.sh —— 服务管理共享引擎（scripts 统一性重构，2026-10-30）
#
# 设计：组件描述表（数据）+ 四个统一函数（唯一实现）——api/mcp/web 三端
# 走同一套启动/停止/探活/指纹逻辑，防御层次天然对齐；加组件=描述表加一行。
# 一致性语义（用户裁决）：三端一体——启动全或全败，停止停不净不算停。
# 文案兼容：错误/成功话术沿用 start.sh 既有措辞（test_start_sh_guard 行为断言锚）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$APP_DIR/web"
VENV_PYTHON="$APP_DIR/venv/bin/python"
LOG_DIR="$APP_DIR/logs"

API_HOST="${MYQUANT_API_HOST:-127.0.0.1}"
export MYQUANT_API_HOST="$API_HOST"
API_PORT="${MYQUANT_API_PORT:-8000}"
MCP_PROFILE="${MYQUANT_MCP_PROFILE:-research}"
export MYQUANT_MCP_PROFILE="$MCP_PROFILE"
MCP_PORT="${MYQUANT_MCP_PORT:-8800}"
WEB_PORT="${MYQUANT_WEB_PORT:-8080}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

ensure_dirs() { mkdir -p "$LOG_DIR"; }

# ── 组件描述表（数据与逻辑分离的核心）────────────────────────
# 字段：名字|端口|工作目录|启动命令|探针形制(http|tcp)|探针路径|进程指纹(ERE)
# 进程指纹 = 三段式停止的第三层：不监听端口的孤儿进程靠它收口
# （历史实录：两个旧 api 绕过脚本拉起后不监听端口，pid/端口两层全失明）。
SVC_COMPONENTS=(
    "api|${API_PORT}|${APP_DIR}|${VENV_PYTHON} -m api|http|/api/health|venv/bin/python -m api"
    "mcp|${MCP_PORT}|${APP_DIR}|${VENV_PYTHON} -m mcpserver.server|tcp||venv/bin/python -m mcpserver"
    "web|${WEB_PORT}|${WEB_DIR}|pnpm exec vite --host --port ${WEB_PORT}|http|/|[n]ode.*vite"
)

svc_field() { local idx="$1" n="$2"; echo "${SVC_COMPONENTS[$idx]}" | cut -d'|' -f"$n"; }
svc_name()  { svc_field "$1" 1; }
svc_port()  { svc_field "$1" 2; }
svc_cwd()   { svc_field "$1" 3; }
svc_cmd()   { svc_field "$1" 4; }
svc_probe_kind() { svc_field "$1" 5; }
svc_probe_path() { svc_field "$1" 6; }
svc_fingerprint() { svc_field "$1" 7; }
svc_pid_file() { echo "$APP_DIR/.myquant_$(svc_name "$1").pid"; }
svc_log_file() { echo "$LOG_DIR/$(svc_name "$1").log"; }
svc_label()   { case "$(svc_name "$1")" in api) echo "API 服务";; mcp) echo "MCP 服务";; web) echo "Web 前端";; *) echo "$(svc_name "$1")";; esac; }

# 组件解析：名字/旧风格旗标（-a/-m/-w，兼容薄壳转发的测试注入面）→
# 依赖序索引集（空格分隔）；all 或空=全体；未知组件返回空串
svc_resolve() {
    local wants=() w i name matched out=""
    [[ $# -eq 0 ]] && set -- all
    for w in "$@"; do
        case "$w" in
            -a|--api|api) wants+=(api) ;;
            -m|--mcp|mcp) wants+=(mcp) ;;
            -w|--web|web) wants+=(web) ;;
            all)          wants+=(api mcp web) ;;
            *)            return 1 ;;
        esac
    done
    for i in "${!SVC_COMPONENTS[@]}"; do
        name="$(svc_name "$i")"
        for w in "${wants[@]}"; do
            if [[ "$w" == "$name" ]]; then out+="$i "; matched=1; break; fi
        done
    done
    if [[ -z "${matched:-}" ]]; then return 1; fi
    echo "$out"
    return 0
}

# ── 探活与进程发现 ────────────────────────────────────────────
tcp_probe() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

probe_one() {  # 组件真实健康探活（curl 系真请求；tcp 系握手）——杜绝日志 grep 假就绪
    local idx="$1" port kind path
    port="$(svc_port "$idx")"; kind="$(svc_probe_kind "$idx")"; path="$(svc_probe_path "$idx")"
    if [[ "$kind" == "http" ]]; then
        curl -sf -o /dev/null "http://localhost:${port}${path}" 2>/dev/null
    else
        tcp_probe "$port"
    fi
}

listeners_on() { lsof -ti :"$1" -sTCP:LISTEN 2>/dev/null || true; }

# 指纹匹配进程（[x] 技巧排除 grep 自身；排除本 shell 家族防自伤）
fingerprint_pids() {
    local fp="$1"
    if [[ -z "$fp" ]]; then return 0; fi
    ps aux | grep -E "$fp" | grep -v grep | grep -v "svc_lib\|svc\.sh" | awk '{print $2}'
    return 0
}

print_pid_clues() {
    local label="$1"; shift
    for p in "$@"; do
        error "  ${label} PID $p: $(ps -p "$p" -o command= 2>/dev/null || echo '未知')"
    done
}

# ── 启动（预检 → 拉起 → 真探活 → 身份核验 → pid 落文件）──────
check_venv() {
    if [[ ! -x "$VENV_PYTHON" ]]; then
        error "虚拟环境不存在: $VENV_PYTHON"
        exit 1
    fi
}

precheck_one() {  # 返回 0=可启动；1=有障碍（已打印原因）
    local idx="$1" pid_file pid port label
    label="$(svc_label "$idx")"; port="$(svc_port "$idx")"; pid_file="$(svc_pid_file "$idx")"
    if [[ -f "$pid_file" ]]; then
        pid=$(<"$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            # pid 身份核验：活着且指纹匹配才算本服务（防 pid 复用误判）
            local fp; fp="$(svc_fingerprint "$idx")"
            if ps -p "$pid" -o command= 2>/dev/null | grep -qE "$fp"; then
                warn "$label 已在运行 (PID: $pid, 端口: $port)"
                return 2  # 已在运行（幂等成功）
            fi
            warn "PID 文件指向无关进程（$pid 非本服务指纹），按残留清理"
        else
            warn "发现残留 $label PID 文件，清理中..."
        fi
        rm -f "$pid_file"
    fi
    # 指纹残留孤儿：不监听端口但进程在——预检阶段报出（处置权在操作者）
    local orphan; orphan="$(fingerprint_pids "$(svc_fingerprint "$idx")")"
    if [[ -n "$orphan" ]]; then
        warn "$label 发现指纹孤儿进程（不监听端口、pid 文件未管理）:"
        print_pid_clues "孤儿" $orphan
        warn "启动继续；停止时 svc stop 的指纹兜底层将收口此类进程"
    fi
    local pids; pids=$(listeners_on "$port")
    if [[ -n "$pids" ]]; then
        error "$label 无法启动：端口 $port 已被以下进程监听"
        print_pid_clues "占用进程" $pids
        error "本脚本不自动杀占用进程（处置权在操作者）：确认后可用 scripts/stop.sh 清理或手工处置，然后重跑"
        return 1
    fi
    return 0
}

start_one() {  # 返回 0=就绪；2=已在运行；1=失败（exit 由调用方定夺——全或全败在入口层）
    local idx="$1" label port pid_file log_file pid pc
    label="$(svc_label "$idx")"; port="$(svc_port "$idx")"; pid_file="$(svc_pid_file "$idx")"
    log_file="$(svc_log_file "$idx")"
    set +e; precheck_one "$idx"; pc=$?; set -e
    if [[ $pc -eq 2 ]]; then return 2; fi
    if [[ $pc -eq 1 ]]; then return 1; fi
    ensure_dirs
    info "启动 $label ..."
    info "  端口: $port  日志: $log_file"
    local dir; dir="$(svc_cwd "$idx")"; local name; name="$(svc_name "$idx")"
    # 拉起形制注意：cd 须同步执行（分号分隔）——`cd x && cmd &` 会把整个
    # 列表后台化，包装子 shell 持有命令替换管道写端且存活等子进程，外层
    # $() 永远等不到 EOF（实测卡死实录 2026-10-30）。& 只作用于 nohup 单命令。
    if [[ "$name" == "api" ]]; then
        pid=$( cd "$dir"; PYTHONPATH="$APP_DIR" nohup $(svc_cmd "$idx") >> "$log_file" 2>&1 & echo $! )
    elif [[ "$name" == "mcp" ]]; then
        pid=$( cd "$dir"; MYQUANT_MCP_PORT="$port" MYQUANT_MCP_PROFILE="$MCP_PROFILE" \
            PYTHONPATH="$APP_DIR" nohup $(svc_cmd "$idx") >> "$log_file" 2>&1 & echo $! )
    else
        pid=$( cd "$dir"; nohup $(svc_cmd "$idx") >> "$log_file" 2>&1 & echo $! )
    fi
    echo "$pid" > "$pid_file"

    local retries=40 spawned=$pid
    while ((retries-- > 0)); do
        if probe_one "$idx"; then
            # 应答者身份核验（健康→核验→打印，顺序铁律）：监听者必须是本次进程
            # 或其子进程（web 的监听者是 pnpm 的 vite 子进程）
            local listeners; listeners=$(listeners_on "$port")
            local ok=0
            for lpid in $listeners; do
                if [[ "$lpid" == "$pid" ]] || pgrep -P "$pid" 2>/dev/null | grep -qx "$lpid"; then
                    ok=1; spawned="$lpid"; break
                fi
            done
            if [[ $ok -ne 1 ]]; then
                error "$label 启动失败：健康检查有应答，但端口 $port 的监听者不是本次进程 (PID: $pid)"
                print_pid_clues "监听进程" $listeners
                error "本次进程仍在运行（可用 scripts/stop.sh 统一清理）；请处置监听进程后重跑"
                return 1
            fi
            # web：pid 文件记真实 vite 子进程（pnpm 包装层死了 vite 不死的历史形态）
            # 成功文案连写（「API 服务启动成功」）——guard 测试行为断言锚此措辞
            if [[ "$(svc_name "$idx")" == "web" ]] && [[ "$spawned" != "$pid" ]]; then
                echo "$spawned" > "$pid_file"
                info "${label}启动成功 (PID: $spawned, 包装层: $pid)"
            else
                info "${label}启动成功 (PID: $pid)"
            fi
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            error "$label 启动失败（进程已退出），请查看日志: tail -f $log_file"
            rm -f "$pid_file"
            return 1
        fi
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        warn "$label 进程已启动 (PID: $pid)，但健康检查未通过，请查看日志:"
        warn "  tail -f $log_file"
        return 1
    else
        error "$label 启动失败，请查看日志: cat $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

# ── 停止（统一三段式：pid 文件 → 端口 → 指纹兜底）────────────
kill_and_wait() {  # kill → 轮询等待 → 超时强杀；返回 0=确认退出
    local pid="$1" label="$2" retries=10
    kill "$pid" 2>/dev/null || true
    while ((retries-- > 0)) && kill -0 "$pid" 2>/dev/null; do sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
        warn "$label 进程未响应，强制终止..."
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
    fi
    ! kill -0 "$pid" 2>/dev/null
}

stop_one() {  # 返回 0=该组件已清零
    local idx="$1" label port pid_file pid stopped=false
    label="$(svc_label "$idx")"; port="$(svc_port "$idx")"; pid_file="$(svc_pid_file "$idx")"
    # 第一段：pid 文件
    if [[ -f "$pid_file" ]]; then
        pid=$(<"$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            info "停止 $label (PID: $pid)..."
            if kill_and_wait "$pid" "$label"; then stopped=true; fi
        fi
        rm -f "$pid_file"
    fi
    # 第二段：端口监听者（-sTCP:LISTEN 只认监听者——不带过滤会把连向该端口的
    # 客户端连接进程〔如 Surge 代理〕当服务误杀，2026-10-30 实录；客户端连接
    # 随服务停止自然断开，无需也不应触碰）
    local pids; pids=$(lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)
    if [[ -n "$pids" ]]; then
        info "通过端口 $port 发现 $label 监听进程: $(echo $pids | tr '\n' ' ')"
        for pid in $pids; do
            if kill_and_wait "$pid" "$label"; then stopped=true; fi
        done
    fi
    # 第三段：进程指纹兜底（孤儿层——历史实录：不监听端口的旧 api 持 db 句柄漏网）
    local orphans; orphans="$(fingerprint_pids "$(svc_fingerprint "$idx")")"
    if [[ -n "$orphans" ]]; then
        info "$label 指纹兜底：发现 $port 端口外残留进程，收口中..."
        for pid in $orphans; do
            if kill_and_wait "$pid" "$label"; then stopped=true; fi
        done
    fi
    # 组件级终态断言
    local remain; remain="$(fingerprint_pids "$(svc_fingerprint "$idx")")$(listeners_on "$port")"
    if [[ -n "$remain" ]]; then
        error "$label 停止后仍有残留:"
        print_pid_clues "残留" $(echo "$remain" | sort -u)
        return 1
    fi
    info "$label 已停止"
    return 0
}

# ── 整体终态断言（三端一体：停不净不算停）────────────────────
assert_stopped() {  # 参数：索引集（空=全体）
    local idxs="$*" fail=0 port fp leftovers=""
    [[ -z "$idxs" ]] && idxs="$(seq 0 $((${#SVC_COMPONENTS[@]}-1)))"
    for idx in $idxs; do
        port="$(svc_port "$idx")"; fp="$(svc_fingerprint "$idx")"
        leftovers+="${fp:+$(fingerprint_pids "$fp") }$(listeners_on "$port")"
    done
    if [[ -n "$(echo "$leftovers" | tr -d ' \n')" ]]; then
        error "终态断言未过：仍有进程/监听残留——"
        print_pid_clues "残留" $(echo "$leftovers" | tr ' ' '\n' | sort -u)
        error "请人工核查上述进程（可能为绕过脚本启动的进程）"
        return 1
    fi
    return 0
}

# ── 状态视图 ──────────────────────────────────────────────────
status_all() {
    local idx name port pid state health since
    printf "%-6s %-10s %-8s %-8s %-12s %s\n" "组件" "状态" "PID" "端口" "健康" "启动时刻"
    for idx in "${!SVC_COMPONENTS[@]}"; do
        name="$(svc_name "$idx")"; port="$(svc_port "$idx")"
        pid="-"; since="-"
        if [[ -f "$(svc_pid_file "$idx")" ]]; then pid=$(<"$(svc_pid_file "$idx")"); fi
        if [[ "$pid" != "-" ]] && kill -0 "$pid" 2>/dev/null; then
            state="运行"
            since="$(ps -p "$pid" -o lstart= 2>/dev/null | cut -c5-16)"
        else
            local fp_pids; fp_pids="$(fingerprint_pids "$(svc_fingerprint "$idx")")"
            state="停止"; [[ -n "$fp_pids" ]] && state="孤儿!"
        fi
        if probe_one "$idx" >/dev/null 2>&1; then health="正常"; else health="-"; fi
        [[ "$state" == "停止" ]] && health="-"
        printf "%-6s %-10s %-8s %-8s %-12s %s\n" "$name" "$state" "$pid" "$port" "$health" "$since"
    done
}
