#!/bin/bash
# MyQuant 统一启动脚本
# 启动后端 API (8000)

cd "$(dirname "$0")"

# 加载日志函数库
if [ -f "scripts/lib/logger.sh" ]; then
    source scripts/lib/logger.sh
    init_log_dir
else
    log_info() { echo "[INFO] $@"; }
    log_warn() { echo "[WARN] $@"; }
    log_error() { echo "[ERROR] $@"; }
    log_start() { echo "[START] $@"; }
fi

GREEN='\033[0;32m'
NC='\033[0m'

log_start "Starting MyQuant" "port_api=8000"

# 启动后端 API (端口 8000)
log_info "START" "Starting Backend API on port 8000..."
./start_api.sh >> logs/start/api_$(date +%Y%m%d).log 2>&1 &
API_PID=$!
echo $API_PID > .myquant_api.pid
log_info "START" "Backend API started" "pid=$API_PID port=8000"

# 启动前端 dev server (端口 8080)
log_info "START" "Starting Frontend dev server on port 8080..."
(cd web && pnpm exec vite --host 0.0.0.0 --port 8080) >> logs/start/web_$(date +%Y%m%d).log 2>&1 &
WEB_PID=$!
echo $WEB_PID > .myquant_web.pid
log_info "START" "Frontend dev server started" "pid=$WEB_PID port=8080"

echo ""
echo -e "${GREEN}MyQuant is ready!${NC}"
echo "  Frontend:    http://localhost:8080"
echo "  Backend API: http://localhost:8000"
echo "  API Docs:    http://localhost:8000/docs"
echo ""

log_start "MyQuant is ready" "api=http://localhost:8000 web=http://localhost:8080"
echo "Press Ctrl+C to stop all services"

wait
