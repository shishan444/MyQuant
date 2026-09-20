"""MyQuant API 服务入口"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    import uvicorn
    # 直接导入 app 对象
    from api.app import create_app

    app = create_app()
    # 端口 env 化（run.py 总入口注入 MYQUANT_API_PORT；默认 8000 不变）；
    # 绑定收敛到 127.0.0.1——本机单用户形态，无对外服务面
    port = int(__import__("os").environ.get("MYQUANT_API_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port)
