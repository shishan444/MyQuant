"""e2e 行情只读守护（add-e2e-isolated-harness 任务 3③）。

禁串清单从 api/routes/data.py 的 @router.(post|put|delete|patch) 机械
导出（运行时正则解析，不手抄——data 域新增写端点自动进入禁串面），
遍历 tests/e2e/*.py 源文本断言不含任何写端点路径。服务侧写面已由
FAPI_STREAM_DISABLED=1 旁路承载，本守护钉测试侧只读契约（D2）。
"""
from __future__ import annotations

import re
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent
DATA_ROUTES_PY = Path(__file__).resolve().parents[2] / "api" / "routes" / "data.py"

# 装饰器单行形制：@router.<write_verb>("path" ...——data.py 多行装饰器
# 或动词变更会使清单为空，由下方非空断言报警（守护面失效不得静默）
_WRITE_DECORATOR_RE = re.compile(
    r'@router\.(?:post|put|delete|patch)\("([^"]+)"'
)


def _extract_write_paths() -> list[str]:
    return _WRITE_DECORATOR_RE.findall(DATA_ROUTES_PY.read_text(encoding="utf-8"))


def test_e2e_sources_contain_no_data_write_endpoint() -> None:
    """tests/e2e 全部源文本不含 data 域任何写端点路径。"""
    write_paths = _extract_write_paths()
    assert write_paths, (
        "禁串清单为空——data.py 写端点解析形制漂移，守护面失效须修正正则"
    )
    offenders: dict[str, list[str]] = {}
    for test_file in sorted(E2E_DIR.glob("*.py")):
        text = test_file.read_text(encoding="utf-8")
        hits = [p for p in write_paths if p in text]
        if hits:
            offenders[test_file.name] = hits
    assert not offenders, f"e2e 源文本含行情写端点路径: {offenders}"
