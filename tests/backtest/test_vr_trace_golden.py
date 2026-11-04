"""vr-signal-trace golden 基线（组 0 任务 0.2——改动前行为锚）。

三面锚定（设计卡 §3①）：metrics 全键+equity+trade_returns——序列化
为字符串后字节级比较（禁 round；sort_keys 保证键序无关，值域全量
保真）。golden 文件产生于引擎改动前（VR_TRACE_GOLDEN=1 生成模式），
此后引擎任何行为漂移在本测试逐位显红——「纯增写通道」的可复验证明
（A6 守卫①golden 逐位锚，T1-S1）。

manifest（tests/fixtures/vr_trace_golden/manifest.json）随 golden 同批
产出：样本 spec sha256（防样本定义静默篡改）+golden 文件 sha256（防
golden 本身被改）+环境锚（python/numpy/pandas/numba/llvmlite/
vectorbt 版本——数值仿真跨版本漂移的判别依据；环境升级须显式重生成
基线：VR_TRACE_GOLDEN=1 pytest tests/backtest/test_vr_trace_golden.py）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from core.backtest.engine import BacktestEngine
from tests.fixtures.vr_trace_gen import SAMPLES, build_sample, sample_names

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "vr_trace_golden"
GENERATE = os.environ.get("VR_TRACE_GOLDEN") == "1"


def _faces_repr(result) -> dict:
    """BacktestResult → 三面字节级序列化字符串（禁 round）。"""
    metrics = json.dumps(
        result.metrics_dict, sort_keys=True, ensure_ascii=True,
        default=repr, allow_nan=True)
    equity = json.dumps(
        [repr(float(v)) for v in result.equity_curve.values],
        ensure_ascii=True)
    trade_returns = json.dumps(
        ([] if result.trade_returns is None
         else [repr(float(v)) for v in result.trade_returns]),
        ensure_ascii=True)
    return {"metrics": metrics, "equity": equity,
            "trade_returns": trade_returns}


def _run_sample(name: str) -> dict:
    s = build_sample(name)
    eng = BacktestEngine(
        init_cash=s["init_cash"], fee=s["fee"], slippage=s["slippage"])
    faces = _faces_repr(eng.run(
        s["dna"], s["df"], dfs_by_timeframe=s["dfs_by_timeframe"]))
    out = {"single": faces}
    if "batch_dnas" in s:
        results = eng.batch_run(s["batch_dnas"], s["df"])
        out["batch"] = [_faces_repr(r) for r in results]
    return out


def _env_anchor() -> dict:
    import numpy
    import pandas
    import vectorbt
    try:
        import numba
        import llvmlite
        numba_v, llvmlite_v = numba.__version__, llvmlite.__version__
    except Exception:  # pragma: no cover - numba 恒在依赖面（防御披露）
        numba_v = llvmlite_v = "unavailable"
    return {
        "python": sys.version.split()[0],
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "numba": numba_v,
        "llvmlite": llvmlite_v,
        "vectorbt": vectorbt.__version__,
    }


def _write_golden() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "env": _env_anchor(),
        "samples": {
            name: hashlib.sha256(
                json.dumps(spec, sort_keys=True).encode()).hexdigest()
            for name, spec in SAMPLES.items()
        },
        "golden": {},
    }
    for name in sample_names():
        faces = _run_sample(name)
        text = json.dumps(faces, ensure_ascii=True, sort_keys=True,
                          indent=1)
        (GOLDEN_DIR / f"{name}.json").write_text(text, encoding="utf-8")
        manifest["golden"][name] = hashlib.sha256(
            text.encode()).hexdigest()
    (GOLDEN_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=1),
        encoding="utf-8")


@pytest.mark.parametrize("name", sample_names())
def test_golden_faces(name: str) -> None:
    """三面字节级锚（生成模式写 golden；断言模式逐位比较）。"""
    golden_path = GOLDEN_DIR / f"{name}.json"
    if GENERATE:
        return  # 生成由下方 test_generate_golden 单点执行（参数化避免重复写）
    if not golden_path.exists():  # pragma: no cover
        pytest.fail(
            f"golden 文件缺失: {golden_path}——先以 VR_TRACE_GOLDEN=1 "
            "生成基线（改动前锚定是纯增写证明的前提）")
    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    actual = _run_sample(name)
    for face in ("metrics", "equity", "trade_returns"):
        assert actual["single"][face] == expected["single"][face], (
            f"{name}.single.{face} 字节级不一致——引擎行为漂移"
            "（或样本/环境变更：对照 manifest 环境锚，升级须显式重生成）")
    if "batch" in expected:
        for col, (exp_c, act_c) in enumerate(
                zip(expected["batch"], actual["batch"])):
            for face in ("metrics", "equity", "trade_returns"):
                assert act_c[face] == exp_c[face], (
                    f"{name}.batch[{col}].{face} 字节级不一致——批量路径"
                    "行为漂移")


def test_manifest_integrity() -> None:
    """manifest 三重完整性：样本定义 hash+golden hash+环境锚在场。"""
    manifest_path = GOLDEN_DIR / "manifest.json"
    if GENERATE:
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in ("env", "samples", "golden"):
        assert key in manifest, f"manifest 缺 {key} 键"
    assert manifest["samples"] == {
        name: hashlib.sha256(
            json.dumps(spec, sort_keys=True).encode()).hexdigest()
        for name, spec in SAMPLES.items()
    }, "样本定义与 manifest 指纹不一致——golden 基线已失效，须重生成"
    for name, digest in manifest["golden"].items():
        text = (GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() == digest, (
            f"golden 文件 {name} 与 manifest 指纹不一致——静默篡改防线")
    for key in ("python", "numpy", "pandas", "numba", "llvmlite",
                "vectorbt"):
        assert key in manifest["env"], f"环境锚缺 {key}"


def test_generate_golden() -> None:
    """生成模式入口（VR_TRACE_GOLDEN=1 时全量重写 golden+manifest）。

    常规运行恒 pass（生成只经环境变量显式触发——见模块 docstring 的
    重生成纪律）。
    """
    if GENERATE:
        _write_golden()
    else:
        assert GOLDEN_DIR.exists() or True
