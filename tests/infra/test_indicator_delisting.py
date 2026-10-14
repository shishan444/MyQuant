"""Q3 指标摘牌守护测试（变更二 5.3 · G5 组，2026-10-14 用户裁决）。

摘牌对象：VWAP（计算层零实现）与 MultifactorOsc（子指标预接线缺失）；
不动项：FractalEntropy（有完整实现且分析链可达）与 VolumeProfile。

四联动（注册表条目/计算分支含委托函数/进化参数画像/进化剔除清单+
分类映射与物种条目）完成后，对拍④既有断言零改动全绿即机制证明
（G5-T01 的一部分——在 test_contract_parity_family.py 内承载，本文件
承载 G5-T01 精确键集断言与 G5-T02/T03/T04/T06）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]
DELISTED = ("VWAP", "MultifactorOsc")


def test_g5_t01_no_compute_exact_keys():
    """G5-T01 精确断言：population._NO_COMPUTE 键集恰为保留两名
    （防豁免集残留死键——FractalEntropy 保留+VolumeProfile 不动合并；
    _NO_COMPUTE 为函数内局部清单，取对拍④同款源码静态提取）。"""
    import inspect
    import re

    from core.evolution import population
    src = inspect.getsource(population)
    m = re.search(r"_NO_COMPUTE\s*=\s*\{([^}]+)\}", src)
    assert m, "population._NO_COMPUTE 源码提取失败"
    keys = {x.strip().strip('"') for x in m.group(1).split(",")}
    assert keys == {"FractalEntropy", "VolumeProfile"}, keys


def test_g5_t02_four_dirs_zero_hits():
    """G5-T02 负向清单：core/api/mcpserver/scripts 四目录 .py 文件对
    两摘牌名零命中（capability.py 零改动一并锚定——不含 tests）。"""
    hits = []
    for base in ("core", "api", "mcpserver", "scripts"):
        for root, _, files in os.walk(REPO_ROOT / base):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = Path(root) / fn
                text = p.read_text(encoding="utf-8", errors="ignore")
                for name in DELISTED:
                    if name in text:
                        hits.append(f"{p.relative_to(REPO_ROOT)}:{name}")
    assert not hits, f"四目录残留摘牌名: {hits}"


def test_g5_t03_listing_faces_exclude():
    """G5-T03 清单面负向：registry 不含两名（list_indicators 与信号列
    前缀的自动收缩由遍历 INDICATOR_REGISTRY 实现——注册表为单一事实源，
    不含即清单面不列出）。"""
    from core.features.registry import INDICATOR_REGISTRY
    assert "VWAP" not in INDICATOR_REGISTRY
    assert "MultifactorOsc" not in INDICATOR_REGISTRY
    # 保留项在册（摘牌对象至多两名的边界锚）
    assert "FractalEntropy" in INDICATOR_REGISTRY
    assert "VolumeProfile" in INDICATOR_REGISTRY


def test_g5_t04_explicit_rejection_two_entries():
    """G5-T04 分入口两码断言（以实测既有行为为准）：引用摘牌名不返回
    静默空结果——显式 indicator 参数入口（condition_stats 模板
    indicator="VWAP"）→ RESEARCH_INDICATOR_UNKNOWN（detail 携
    available_indicators 注册表清单指引；spec delta 原文
    RESEARCH_PARAM_INVALID 系方案期推断码，实现面的显式 indicator
    参数域自始为 RESEARCH_INDICATOR_UNKNOWN——已随归档修正 spec）；
    条件结构化引用入口（{indicator:"MultifactorOsc"}）→
    ColumnRefError PARAM_INVALID（未知指标结构化拒绝，经翻译链转译
    为 RESEARCH_CONDITION_INVALID 列引用解析失败族）。"""
    import numpy as np
    import pandas as pd
    from core.analysis.column_ref import ColumnRefError, resolve_column_ref
    from core.analysis.hypothesis_service import ResearchError
    from core.analysis.templates.condition_stats import condition_stats

    idx = pd.date_range("2024-01-01", periods=300, freq="1h", tz="UTC")
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, 300))
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": rng.uniform(10, 100, 300),
                       "trades": 5}, index=idx)
    with pytest.raises(ResearchError) as ei:
        condition_stats(
            df, indicator="VWAP", params={},
            condition={"subject": "close", "action": "lt", "target": 1},
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1h",
                          "start": "2024-01-20", "end": "2024-01-25"}])
    assert ei.value.code == "RESEARCH_INDICATOR_UNKNOWN"
    assert "VWAP" in ei.value.message

    with pytest.raises(ColumnRefError) as ei2:
        resolve_column_ref(
            {"indicator": "MultifactorOsc", "params": {"lookback": 20}})
    assert ei2.value.code == "PARAM_INVALID"  # 未知指标结构化拒绝族


def test_g5_t06_fractal_entropy_dual_face():
    """G5-T06 FractalEntropy 双面回归：①分析链可达（委托链
    indicators→ml_indicators 产出非空序列）②进化剔除不变（仍驻
    _NO_COMPUTE——lazy 语义不进种群，与实现存在性正交）。"""
    import numpy as np
    import pandas as pd
    from core.features.indicators import _compute_indicator

    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    rng = np.random.default_rng(3)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": rng.uniform(10, 90, n),
                       "trades": 5}, index=idx)
    cols = _compute_indicator(df, "FractalEntropy",
                              {"bins": 10, "lookback": 100})
    assert "mfe_score_10_100" in cols
    assert int(cols["mfe_score_10_100"].dropna().shape[0]) > 0, (
        "分析链直调须产出非空序列（委托链存活）")
    import inspect
    import re

    from core.evolution import population
    src = inspect.getsource(population)
    m = re.search(r"_NO_COMPUTE\s*=\s*\{([^}]+)\}", src)
    keys = {x.strip().strip('"') for x in m.group(1).split(",")}
    assert "FractalEntropy" in keys, (
        "进化剔除语义不变——分析链可达不能错用到进化链")
