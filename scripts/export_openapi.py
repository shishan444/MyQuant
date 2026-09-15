#!/usr/bin/env python3
"""离线导出 OpenAPI 契约（前端类型生成链第一步）。

不启动 uvicorn、不触发 lifespan（后台 runner 均不运行），
直接从 FastAPI 应用实例导出 openapi.json，作为前端 openapi-typescript
的唯一事实源。
"""
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from api.app import create_app  # noqa: E402

OUT = project_root / "web" / "openapi.json"


def main() -> None:
    app = create_app()
    spec = app.openapi()
    OUT.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    print(
        f"exported {len(spec.get('paths', {}))} paths, "
        f"{len(spec.get('components', {}).get('schemas', {}))} schemas -> {OUT}"
    )


if __name__ == "__main__":
    main()
