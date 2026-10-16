"""V8 存量固定样本回测逐字节锚（unify-tool-return-contract 任务 10）。

基线=main（2026-10-16 b576e53，双静默面收口前）生成的固定样本
metrics 全量 JSON sha256。固定样本：4 组（seed 42/7 × long/short
× leverage 1/2，n=600 合成数据 make_enhanced_df）。本分支实测与
main 逐字节相等（f39b89ca…）——执行器收口不触达合法 DNA 路径的
直接证据。快照漂移=行为回归信号，须先查明再更新。
"""
from __future__ import annotations

import hashlib
import json

import pytest

pytestmark = [pytest.mark.integration]

_BASELINE_SHA = "f39b89caa101a5f4d739d6de6d831689eea65eaf9b429a8f61d07c18cc82f9e7"


def _run_snapshot() -> str:
    from tests.helpers.data_factory import make_enhanced_df, make_dna
    from core.backtest.engine import BacktestEngine
    from core.strategy.executor import dna_to_signal_set, clear_indicator_cache

    out = {}
    for seed, lev in ((42, 1), (7, 2)):
        df = make_enhanced_df(n=600, seed=seed)
        for entry_val, direction in ((30, "long"), (70, "short")):
            clear_indicator_cache()
            dna = make_dna(
                entry_value=entry_val,
                exit_value=70 if entry_val == 30 else 30,
                leverage=lev, direction=direction)
            sig = dna_to_signal_set(dna, df)
            res = BacktestEngine().run(dna, df, signal_set=sig)
            m = res.metrics_dict
            out[f"seed{seed}_lev{lev}_{direction}_{entry_val}"] = {
                k: (round(float(v), 10)
                    if isinstance(v, (int, float)) and v == v else str(v))
                for k, v in sorted(m.items())}
    return json.dumps(out, sort_keys=True)


def test_v8_fixed_sample_metrics_byte_identical():
    blob = _run_snapshot()
    sha = hashlib.sha256(blob.encode()).hexdigest()
    assert sha == _BASELINE_SHA, (
        f"固定样本 metrics 快照漂移：{sha} != {_BASELINE_SHA}"
        "——执行器/回测路径行为回归，查明后（如属预期行为变更）随变更"
        "更新基线并在 commit 说明")
