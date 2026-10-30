#!/usr/bin/env bash
# svc.sh —— MyQuant 服务统一管理入口（scripts 统一性重构，2026-10-30）
#
# 三端一体语义（用户裁决）：日常操作不带组件参数——启动=api/mcp/web
# 全或全败（任一失败回滚已起的并非零退出）；停止=三端全清+终态断言
# （停不净不算停）。可选组件参数（-a/-m/-w 或名字）保留为测试注入与
# 排障后门，不是日常入口。
#
# 用法:
#   scripts/svc.sh start              # 三端启动（全或全败）
#   scripts/svc.sh stop               # 三端停止（终态断言）
#   scripts/svc.sh restart            # 停到零再全起
#   scripts/svc.sh status             # 三端一致性视图
#   scripts/svc.sh start -a           # 仅 api（注入/排障后门）
#
# 旧入口 start.sh/stop.sh/restart.sh 为本入口的兼容薄壳。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/svc_lib.sh
source "$SCRIPT_DIR/svc_lib.sh"

cmd_start() {
    check_venv
    local idxs
    idxs="$(svc_resolve "$@" || true)"
    if [[ -z "$idxs" ]]; then
        error "未知组件: $*（支持 all|api|mcp|web）"
        exit 1
    fi
    local idx pc started=()
    # 全预检先行：任一端口/环境障碍整体拒绝（不产生半启动状态）
    local blocked=0
    for idx in $idxs; do
        set +e; precheck_one "$idx"; pc=$?; set -e
        if [[ $pc -eq 1 ]]; then blocked=1; fi
    done
    if [[ $blocked -eq 1 ]]; then
        error "预检未过：整体拒绝启动（三端一体——不产生半启动状态）"
        exit 1
    fi
    # 依序拉起：api → mcp → web（依赖序）；全或全败
    for idx in $idxs; do
        if start_one "$idx"; then
            started+=("$idx")
        else
            error "$(svc_label "$idx") 启动失败——回滚已启动的服务（全或全败）"
            local s
            for s in "${started[@]:-}"; do
                if [[ -n "$s" ]]; then stop_one "$s" || true; fi
            done
            error "已启动的其他服务可用 scripts/stop.sh 统一清理"
            exit 1
        fi
    done
    echo ""
    info "================================"
    info "MyQuant 服务已启动"
    info "================================"
    info "API:    http://localhost:${API_PORT}"
    info "文档:   http://localhost:${API_PORT}/docs"
    info "MCP:    http://127.0.0.1:${MCP_PORT}/mcp   [档位: ${MCP_PROFILE}]"
    info "Web:    http://localhost:${WEB_PORT}"
    info "================================"
    info ""
    info "Agent 接入（一次性配置，任何 MCP 客户端）:"
    info "  claude mcp add --transport http myquant-research \\"
    info "      http://127.0.0.1:${MCP_PORT}/mcp"
    info ""
    info "日志: tail -f $LOG_DIR/{api,mcp,web}.log   状态: scripts/svc.sh status"
    info "停止: scripts/stop.sh    重启: scripts/restart.sh"
}

cmd_stop() {
    local idxs
    idxs="$(svc_resolve "$@" || true)"
    if [[ -z "$idxs" ]]; then
        error "未知组件: $*（支持 all|api|mcp|web）"
        exit 1
    fi
    local idx fail=0
    # 逆序停止（消费方先停；字符串收集反转——空数组在 set -u 下展开报 unbound）
    local ridxs=""
    for idx in $idxs; do ridxs="$idx $ridxs"; done
    for idx in $ridxs; do
        stop_one "$idx" || fail=1
    done
    # 终态断言：目标组件端口零监听 + 指纹零匹配（停不净不算停）
    if ! assert_stopped "$idxs"; then
        error "存在停止失败的组件——整体状态不干净，请人工核查"
        exit 1
    fi
    if [[ $fail -eq 1 ]]; then exit 1; fi
    info "MyQuant 所有服务已停止（三端口零监听、零进程残留）"
}

cmd_restart() {
    info "停止现有服务..."
    cmd_stop
    sleep 1
    info "启动服务..."
    cmd_start
    echo ""
    info "重启完成"
}

cmd_status() { status_all; }

main() {
    if [[ $# -lt 1 ]]; then
        echo "用法: $0 start|stop|restart|status [组件...]" >&2
        exit 1
    fi
    local action="$1"; shift
    case "$action" in
        start)   cmd_start "$@" ;;
        stop)    cmd_stop "$@" ;;
        restart) cmd_restart ;;
        status)  cmd_status ;;
        *)       echo "未知动作: $action（支持 start|stop|restart|status）" >&2; exit 1 ;;
    esac
}

main "$@"
