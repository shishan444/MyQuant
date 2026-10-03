"""MyQuant API 服务入口（python -m api 形态）"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    import os

    import uvicorn

    # 消费模块级组合实例（socket.io ASGI 组合）——与 uvicorn
    # api.app:app 形态一致；此前自建 create_app() 为裸 FastAPI，
    # /socket.io 路径在该形态下不可达（F3 双形态分裂）
    from api.app import app

    # 端口 env 化（scripts/start.sh 统一入口注入 MYQUANT_API_PORT；默认 8000 不变）；
    # 绑定 127.0.0.1——本机单用户形态，无对外服务面
    port = int(os.environ.get("MYQUANT_API_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port)
