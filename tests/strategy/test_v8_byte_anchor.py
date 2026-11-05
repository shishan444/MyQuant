"""V8 存量固定样本回测逐字节锚（unify-tool-return-contract 任务 10）。

基线=main（2026-10-16 b576e53，双静默面收口前）生成的固定样本
metrics 全量 JSON sha256。固定样本：4 组（seed 42/7 × long/short
× leverage 1/2，n=600 合成数据 make_enhanced_df）。快照漂移=行为
回归信号，须先查明再更新。

基线沿革：
- f39b89ca…（原基线）→ ae069171…：research-execution-config-
  alignment 卡 C 资金费永续化——合成数据无 funding_rate 列，
  lev2 组旧模型恒定正费扣减被时序供给零费取代（裸数据零费为
  新合法语义）；lev1 组旧零费不变。属预期行为变更（proposal
  影响面：B2 修复后回测指标跨版本不可比），随变更更新基线。
"""
from __future__ import annotations

import hashlib
import json

import pytest

pytestmark = [pytest.mark.integration]

_BASELINE_SHA = "ae069171f22c84eb50400a614401a4209e5fdf5201ed0603839f5ca048510151"


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
            # 披露增量键排除（complete-research-tooling 任务 4）：
            # position_sizing 是声明面披露（fixed 档七子键——短路边
            # 返回 avg/max_entry_size=None，值不随执行路径变化）——
            # 锚语义=执行路径行为回归检测，非 metrics 键集
            # 冻结；增量披露键入锚会把锚降级为键集快照，每次加披露键
            # 都要动基线，掩盖真正的行为漂移信号
            m = {k: v for k, v in m.items() if k != "position_sizing"}
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
