#!/usr/bin/env python3
"""MyQuant API 启动脚本"""
import os
import subprocess
import sys
from pathlib import Path

# 设置环境
project_root = Path(__file__).parent.parent
env = os.environ.copy()
env["PYTHONPATH"] = str(project_root)

# 启动 uvicorn（绑定 127.0.0.1：A2 收口，MYQUANT_API_HOST 可显式覆盖）
cmd = [
    sys.executable,
    "-m", "uvicorn",
    "api.app:app",
    "--host", os.environ.get("MYQUANT_API_HOST", "127.0.0.1"),
    "--port", "8000",
]

subprocess.run(cmd, env=env, cwd=project_root)
