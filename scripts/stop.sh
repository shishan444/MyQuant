#!/usr/bin/env bash
# 兼容薄壳：转发统一入口（实现收敛至 svc_lib.sh——scripts 统一性重构 2026-10-30）
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/svc.sh" stop "$@"
