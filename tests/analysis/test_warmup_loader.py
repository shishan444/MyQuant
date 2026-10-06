"""warmup 前扩装载公共件单测（fix-condition-stats-forward-return U1）。

走真 parquet（tmp_path 写 {symbol}_{timeframe}.parquet，命名对齐
core.data.mtf_loader.find_parquet 的主名形制）。期望 warmup 值按注册表
手工写死（RSI period max=50 → max(200,150)=200；EMA period max=200 →
max(200,600)=600），与 derive_warmup 实现互证而非同义反复。
"""

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.warmup_loader import TF_MINUTES, load_with_warmup
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


def _write_parquet(tmp_path, df, timeframe):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    df.to_parquet(data_dir / f"{SYMBOL}_{timeframe}.parquet")
    return data_dir


def test_warmup_prefix_loaded(tmp_path):
    """前扩生效：请求区间前有充足数据 → df 含与推导值一致的前缀根数，
    返回的 start_ts 等于请求起点。"""
    df = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1h")
    start, end = df.index[400].isoformat(), df.index[599].isoformat()

    out, start_ts = load_with_warmup(
        data_dir, SYMBOL, "1h", start, end, ["RSI"])  # RSI → 200 bar

    assert start_ts == pd.Timestamp(start)
    assert out.index[0] < start_ts
    assert int((out.index < start_ts).sum()) >= 200  # 前缀根数 ≥ 推导值
    assert int((out.index < start_ts).sum()) == 200  # 连续数据 → 恰为 200
    assert (out.index >= start_ts).sum() == 200  # 统计段 = 请求区间


def test_warmup_derivation_drives_expansion(tmp_path):
    """推导取值：注册表周期上限驱动前扩量——RSI(max=50) → 200 bar；
    EMA(max=200) → max(200, 200×3)=600 bar。"""
    df = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1h")

    start_rsi = df.index[800].isoformat()
    out, _ = load_with_warmup(data_dir, SYMBOL, "1h", start_rsi,
                              df.index[899].isoformat(), ["RSI"])
    assert int((out.index < pd.Timestamp(start_rsi)).sum()) == 200

    start_ema = df.index[800].isoformat()
    out2, _ = load_with_warmup(data_dir, SYMBOL, "1h", start_ema,
                               df.index[899].isoformat(), ["EMA"])
    assert int((out2.index < pd.Timestamp(start_ema)).sum()) == 600


def test_minute_timeframe_mapping(tmp_path):
    """1m/5m 映射生效：1m 下 200-bar warmup 的装载起点恰早 200 分钟
    （若落 240 分钟兜底将早 240 分钟，断言即红）。"""
    df = make_ohlcv(n=600, freq="1min", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1m")
    start = df.index[300].isoformat()  # 2024-01-01T05:00Z

    out, start_ts = load_with_warmup(
        data_dir, SYMBOL, "1m", start, df.index[599].isoformat(), ["RSI"])

    assert out.index[0] == start_ts - pd.Timedelta(minutes=200)
    # CARD-04 收敛：TF_MINUTES 自 TF_SECONDS（14 档权威表）派生——
    # 派生一致断言防两表再漂移（字面断言已随收敛退役）
    from core.data.timeframes import TF_SECONDS
    assert TF_MINUTES == {tf: sec // 60 for tf, sec in TF_SECONDS.items()}
    assert len(TF_MINUTES) == len(TF_SECONDS) == 14


def test_warmup_insufficient_rejected(tmp_path):
    """数据起点贴近请求起点（前仅 100 bar < 200）→ 显式拒绝。"""
    df = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1h")

    with pytest.raises(ResearchError) as ei:
        load_with_warmup(data_dir, SYMBOL, "1h", df.index[100].isoformat(),
                         df.index[500].isoformat(), ["RSI"])
    assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
    assert "200" in ei.value.message


def test_data_insufficient_rejected(tmp_path):
    """无 parquet 可装载 → RESEARCH_DATA_INSUFFICIENT。"""
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    with pytest.raises(ResearchError) as ei:
        load_with_warmup(data_dir, SYMBOL, "1h", "2024-01-01T00:00:00Z",
                         "2024-02-01T00:00:00Z", ["RSI"])
    assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"


def test_min_warmup_bars_expands_prefix(tmp_path):
    """叠加路径：min_warmup_bars 高于推导值（RSI=200 < 500）→ 前扩取
    max(derive_warmup, min_warmup_bars)=500；不足 500 根同样走拒绝。"""
    df = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1h")
    start = df.index[800].isoformat()

    out, _ = load_with_warmup(data_dir, SYMBOL, "1h", start,
                              df.index[899].isoformat(), ["RSI"],
                              min_warmup_bars=500)

    assert int((out.index < pd.Timestamp(start)).sum()) == 500

    with pytest.raises(ResearchError) as ei:
        load_with_warmup(data_dir, SYMBOL, "1h", df.index[300].isoformat(),
                         df.index[500].isoformat(), ["RSI"],
                         min_warmup_bars=500)
    assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
    assert "500" in ei.value.message


def test_min_warmup_bars_default_unchanged(tmp_path):
    """回归路径：缺省（不传或显式 0）时 warmup 仍由 derive_warmup 主导，
    行为与扩展前完全一致。"""
    df = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1h")
    start = df.index[800].isoformat()
    end = df.index[899].isoformat()

    out_default, _ = load_with_warmup(data_dir, SYMBOL, "1h", start, end,
                                      ["RSI"])
    out_zero, _ = load_with_warmup(data_dir, SYMBOL, "1h", start, end,
                                   ["RSI"], min_warmup_bars=0)
    assert int((out_default.index < pd.Timestamp(start)).sum()) == 200
    assert int((out_zero.index < pd.Timestamp(start)).sum()) == 200


def test_warmup_insufficient_detail_disclosure(tmp_path):
    """0.3.3 G5：warmup 不足报错 detail 携带实算预算与规则全文。

    四键断言——warmup_bars 与 derive_warmup(+min) 同源同参对照、
    available_bars 为装载后实测、data_start 可定位、rule 与
    registry_meta.WARMUP_RULE_TEXT 同文（四披露面一致的第一面）。
    """
    from core.analysis.registry_meta import WARMUP_RULE_TEXT, derive_warmup

    df = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=7)
    data_dir = _write_parquet(tmp_path, df, "1h")
    start = df.index[100].isoformat()
    end = df.index[500].isoformat()

    with pytest.raises(ResearchError) as ei:
        load_with_warmup(data_dir, SYMBOL, "1h", start, end, ["RSI"],
                         min_warmup_bars=500)
    detail = ei.value.detail
    assert detail["warmup_bars"] == max(derive_warmup(["RSI"]), 500)
    assert detail["available_bars"] == 100
    assert detail["data_start"] == df.index[0].isoformat()
    assert detail["rule"] == WARMUP_RULE_TEXT
    # 忠实口径要素在 rule 全文中（参数求和语义——非「最大周期×3」旧口径）
    assert "周期类参数注册表上限之和" in detail["rule"]
