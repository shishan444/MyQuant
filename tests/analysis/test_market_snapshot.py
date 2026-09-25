"""时点行情快照服务单测（add-market-snapshot 1.2）。

走真 parquet（tmp_path 写 {symbol}_{tf}.parquet，命名对齐
core.data.mtf_loader.find_parquet；衍生品 {symbol}_oi_{tf} 与
{symbol}_funding 对齐 _try_merge_derivatives）。

确定性分段数据取自 tests/helpers/regime_factory.py（趋势段=定向漂移
+小噪声，ADX 收敛 >25；震荡段=均值回归振荡，ADX 回落 <20）。
单数据集两段，as_of 各取一段内时点做区分验收。
"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.market_snapshot import get_market_snapshot
from core.persistence.repository import research as rr
from tests.helpers.regime_factory import (
    make_range_ohlcv,
    make_trend_ohlcv,
    make_two_regime_df,
)

SYMBOL = "BTCUSDT"


def write_parquet(data_dir: Path, df: pd.DataFrame, timeframe: str) -> None:
    df.to_parquet(data_dir / f"{SYMBOL}_{timeframe}.parquet")


def write_derivatives(
    data_dir: Path, ohlcv: pd.DataFrame, timeframe: str,
    *, funding_from: int | None = None, oi: bool = False, seed: int = 5,
) -> None:
    """衍生品 parquet：funding_from=None 不写 / 整数=从第几根起有值（头部缺口）。"""
    rng = np.random.default_rng(seed)
    if funding_from is not None:
        fr_idx = ohlcv.index[funding_from:]
        fr = pd.DataFrame(
            {"funding_rate": rng.normal(1e-4, 5e-5, len(fr_idx))},
            index=fr_idx)
        fr.to_parquet(data_dir / f"{SYMBOL}_funding.parquet")
    if oi:
        oi_series = 1e8 * np.cumprod(1 + rng.normal(0, 0.002, len(ohlcv)))
        oi_df = pd.DataFrame({"open_interest": oi_series}, index=ohlcv.index)
        oi_df.to_parquet(data_dir / f"{SYMBOL}_oi_{timeframe}.parquet")


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """升级到 head 的临时库（research_log 留痕断言用）。"""
    db = tmp_path / "research.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# 两时点区分（趋势/震荡）
# ---------------------------------------------------------------------------

def test_two_regime_distinction(db_path, data_dir):
    """同数据集两时点：趋势段时点判「趋势」（adx>25）、震荡段时点判「震荡」（adx<20）。"""
    df = make_two_regime_df()
    write_parquet(data_dir, df, "1d")

    trend_as_of = df.index[329]  # 趋势段末（前缀 329 >= warmup 300）
    range_as_of = df.index[-1]

    out_t = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                                timeframes=["1d"], as_of=trend_as_of)
    out_r = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                                timeframes=["1d"], as_of=range_as_of)

    card_t = out_t["timeframes"]["1d"]
    card_r = out_r["timeframes"]["1d"]
    assert card_t["trend"]["state"] == "趋势"
    assert card_t["trend"]["adx"] > 25
    assert card_r["trend"]["state"] == "震荡"
    assert card_r["trend"]["adx"] < 20
    # 五维结构齐备（读数/分位/判读/window_bars/as_of）
    for card in (card_t, card_r):
        assert set(card) >= {"trend", "momentum", "volatility", "volume",
                             "derivatives", "window_bars", "as_of"}
        assert card["momentum"]["rsi"] is not None
        assert card["momentum"]["pct"] is not None
        assert card["volatility"]["atr_pct"] is not None
        assert card["volume"]["rvol"] is not None


def test_as_of_default_latest_bar(db_path, data_dir):
    """as_of 缺省 = 最新一根 bar（周期卡 as_of 即末根时间戳）。"""
    df = make_trend_ohlcv(400, seed=3)
    write_parquet(data_dir, df, "1d")

    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                              timeframes=["1d"])

    assert out["as_of"] == "latest"
    assert out["timeframes"]["1d"]["as_of"] == df.index[-1].isoformat()


# ---------------------------------------------------------------------------
# as_of 防前视
# ---------------------------------------------------------------------------

def test_as_of_no_lookahead(db_path, data_dir):
    """as_of 之后追加极端 bar 重写 parquet，同 as_of 再调 → 读数逐位不变。"""
    df = make_two_regime_df(seed=21)
    write_parquet(data_dir, df, "1d")
    as_of = df.index[329]  # 趋势段末时点

    before = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                                 timeframes=["1d"], as_of=as_of)

    # 追加 50 根极端 bar：价格翻数倍 + 巨量（若泄入计算将污染读数与分位）
    tail_idx = pd.date_range(df.index[-1] + pd.Timedelta(days=1), periods=50,
                             freq="1D", tz="UTC", name="timestamp")
    c = df["close"].iloc[-1]
    extreme_close = c * np.cumprod(np.full(50, 1.15))
    extreme = pd.DataFrame({
        "open": extreme_close, "high": extreme_close * 1.05,
        "low": extreme_close * 0.95, "close": extreme_close,
        "volume": np.full(50, 1e7),
    }, index=tail_idx)
    write_parquet(data_dir, pd.concat([df, extreme]), "1d")

    after = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                                timeframes=["1d"], as_of=as_of)

    assert after == before  # 未来数据不污染过去（含分位窗口）


# ---------------------------------------------------------------------------
# derivatives 三态容缺
# ---------------------------------------------------------------------------

def test_derivatives_no_file(db_path, data_dir):
    """无 OI/funding parquet → derivatives 两子维标无数据，其余四维正常。"""
    df = make_trend_ohlcv(400, seed=7)
    write_parquet(data_dir, df, "1d")

    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                              timeframes=["1d"])
    deriv = out["timeframes"]["1d"]["derivatives"]

    assert deriv == {"funding": {"status": "no_data"},
                     "oi": {"status": "no_data"}}
    assert out["timeframes"]["1d"]["trend"]["adx"] is not None  # 其余维正常


def test_derivatives_head_gap(db_path, data_dir):
    """funding parquet 从第 300 根起（ffill 头部缺口）→ 头部时点窗内全 NaN
    标无数据（不冒充假 0）；覆盖后时点正常读数。"""
    df = make_range_ohlcv(700, seed=9)
    write_parquet(data_dir, df, "1d")
    write_derivatives(data_dir, df, "1d", funding_from=400)  # oi 仍无文件

    early = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                                timeframes=["1d"], as_of=df.index[350])
    deriv_early = early["timeframes"]["1d"]["derivatives"]
    # 窗 [260,350] 原始 funding_rate 全 NaN → 无数据；禁止 fillna(0) 冒充
    assert deriv_early["funding"] == {"status": "no_data"}
    assert deriv_early["oi"] == {"status": "no_data"}

    late = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                               timeframes=["1d"], as_of=df.index[650])
    funding = late["timeframes"]["1d"]["derivatives"]["funding"]
    assert funding["status"] == "ok"
    assert funding["funding_z"] is not None
    assert funding["window_bars"] == 90  # 窗内有效 bar 数如实声明


def test_derivatives_full_coverage(db_path, data_dir):
    """funding+OI 自始覆盖 → 两子维正常读数（含分位与有效 bar 数）。"""
    df = make_range_ohlcv(700, seed=13)
    write_parquet(data_dir, df, "1d")
    write_derivatives(data_dir, df, "1d", funding_from=0, oi=True)

    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                              timeframes=["1d"], as_of=df.index[650])
    deriv = out["timeframes"]["1d"]["derivatives"]

    assert deriv["funding"]["status"] == "ok"
    assert deriv["funding"]["funding_z"] is not None
    assert deriv["oi"]["status"] == "ok"
    assert deriv["oi"]["oi_change_rate"] is not None
    assert deriv["oi"]["pct"] is not None


# ---------------------------------------------------------------------------
# 口径与声明
# ---------------------------------------------------------------------------

def test_bb_bandwidth_and_atr_pct_formula(db_path, data_dir):
    """BB 带宽=(upper-lower)/middle、ATR%=ATR(14)/close*100 口径（对照 pandas_ta 直算）。"""
    import pandas_ta as ta

    df = make_trend_ohlcv(400, seed=17)
    write_parquet(data_dir, df, "1d")
    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                              timeframes=["1d"], as_of=df.index[-1])
    vol = out["timeframes"]["1d"]["volatility"]

    bb = ta.bbands(df["close"], length=20, std=2.0)
    # pandas_ta 列序为 BBL/BBM/BBU（iloc 0/1/2）；带宽=|上下轨距离|/中轨
    expect_bw = (bb.iloc[:, 0] - bb.iloc[:, 2]).abs() / bb.iloc[:, 1]
    expect_atr_pct = ta.atr(df["high"], df["low"], df["close"], length=14) \
        / df["close"] * 100.0
    assert vol["bb_bw"] == round(float(expect_bw.iloc[-1]), 2)
    assert vol["bb_bw"] > 0  # 带宽恒正（列序颠倒下 abs 仍输出正确语义）
    assert vol["atr_pct"] == round(float(expect_atr_pct.iloc[-1]), 2)
    assert out["notes"]["bb_bw"] == "|bb_upper-bb_lower|/bb_middle"


def test_window_bars_declared_per_timeframe(db_path, data_dir):
    """window_bars 如实声明实际窗口：1d=90（足额）；3d=30（90 日仅 30 bar）。"""
    df_1d = make_trend_ohlcv(400, seed=19, start="2024-01-01")
    write_parquet(data_dir, df_1d, "1d")
    df_3d = make_trend_ohlcv(400, seed=19, start="2024-01-01",
                              freq="3D")
    write_parquet(data_dir, df_3d, "3d")

    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                              timeframes=["1d", "3d"])

    assert out["timeframes"]["1d"]["window_bars"] == 90
    assert out["timeframes"]["3d"]["window_bars"] == 30


# ---------------------------------------------------------------------------
# 契约边界与拒绝路径
# ---------------------------------------------------------------------------

def test_timeframes_contract(db_path, data_dir):
    """子集在册可用、重复去重；空集与非法周期结构化拒绝。"""
    df = make_trend_ohlcv(400, seed=23)
    write_parquet(data_dir, df, "1d")

    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                              timeframes=["1d", "1d"])
    assert list(out["timeframes"]) == ["1d"]

    with pytest.raises(ResearchError) as ei:
        get_market_snapshot(db_path, data_dir, symbol=SYMBOL, timeframes=[])
    assert ei.value.code == "RESEARCH_PARAM_INVALID"

    with pytest.raises(ResearchError) as ei:
        get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                            timeframes=["1d", "2d"])
    assert ei.value.code == "RESEARCH_PARAM_INVALID"
    assert "2d" in ei.value.message


def test_symbol_or_timeframe_not_found(db_path, data_dir):
    """symbol/timeframe 无 parquet → RESEARCH_SYMBOL_NOT_FOUND。"""
    df = make_trend_ohlcv(400, seed=25)
    write_parquet(data_dir, df, "1d")

    with pytest.raises(ResearchError) as ei:
        get_market_snapshot(db_path, data_dir, symbol="NOSUCHUSDT")
    assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    with pytest.raises(ResearchError) as ei:
        get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                            timeframes=["4h"])
    assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"


def test_as_of_before_data_start_rejected(db_path, data_dir):
    """as_of 早于数据起点 → RESEARCH 数据类拒绝。"""
    df = make_trend_ohlcv(400, seed=27, start="2024-06-01")
    write_parquet(data_dir, df, "1d")

    with pytest.raises(ResearchError) as ei:
        get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                            timeframes=["1d"], as_of="2024-01-01T00:00:00Z")
    assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"


def test_warmup_insufficient_passthrough(db_path, data_dir):
    """warmup 不足（数据起点前实际 bar 少于推导）→ 透传 RESEARCH_WARMUP_INSUFFICIENT。"""
    df = make_trend_ohlcv(150, seed=29)  # 1d floor 200
    write_parquet(data_dir, df, "1d")

    with pytest.raises(ResearchError) as ei:
        get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                            timeframes=["1d"], as_of=df.index[-1])
    assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"


def test_size_guard_rejects_oversize(db_path, data_dir, monkeypatch):
    """体积护栏：序列化超限结构化拒绝（monkeypatch 压低阈值测护栏逻辑本体）。"""
    from core.analysis import market_snapshot as ms

    df = make_trend_ohlcv(400, seed=31)
    write_parquet(data_dir, df, "1d")
    monkeypatch.setattr(ms, "_MAX_SERIALIZED_BYTES", 50)

    with pytest.raises(ResearchError) as ei:
        ms.get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                               timeframes=["1d"])
    assert ei.value.code == "RESEARCH_PARAM_INVALID"
    assert ei.value.detail["limit"] == 50


# ---------------------------------------------------------------------------
# 体积与留痕
# ---------------------------------------------------------------------------

def test_four_timeframe_size_budget(db_path, data_dir):
    """四周期全量（含衍生品正常覆盖）序列化后 <= 2KB。"""
    specs = {"3d": (400, "3D"), "1d": (400, "1D"),
             "4h": (600, "4h"), "1h": (2200, "1h")}
    for tf, (n, freq) in specs.items():
        df = make_trend_ohlcv(n, seed=41 + n % 7, freq=freq)
        write_parquet(data_dir, df, tf)
        write_derivatives(data_dir, df, tf, funding_from=0, oi=True)

    out = get_market_snapshot(db_path, data_dir, symbol=SYMBOL)

    assert list(out["timeframes"]) == ["3d", "1d", "4h", "1h"]
    assert out["timeframes"]["1h"]["window_bars"] == 2160  # 90 日换算
    assert out["timeframes"]["4h"]["window_bars"] == 540
    serialized = json.dumps(out, ensure_ascii=False).encode("utf-8")
    assert len(serialized) <= 2048, f"体积超限: {len(serialized)} bytes"


def test_research_log_entry_written(db_path, data_dir):
    """每次调用落 research_log adhoc 条目（含 symbol/timeframes/as_of）。"""
    df = make_trend_ohlcv(400, seed=43)
    write_parquet(data_dir, df, "1d")

    get_market_snapshot(db_path, data_dir, symbol=SYMBOL,
                        timeframes=["1d"], as_of=df.index[-1])

    entries = [e for e in rr.list_research_log(db_path)
               if e["entry_type"] == "market_snapshot"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["hypothesis_id"] is None  # adhoc：不进假设判定并集
    assert entry["ref_id"] == SYMBOL
    ranges = json.loads(entry["data_ranges"])
    assert ranges == {"symbol": SYMBOL, "timeframes": ["1d"],
                      "as_of": df.index[-1].isoformat()}
