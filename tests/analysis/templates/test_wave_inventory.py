"""浪清单盘点模板测试（add-evaluation-ruler 任务 3.1-3.4）。

五规则期望值全部人工独立推导（T1 铁律：不从 r9 demo 复制期望）；
demo 底账复算锚（任务 3.2）为唯一例外——对账对象的期望值来自冻结
的 demo 工件 r9_d1_waves_{SYM}.csv（amp 百分数形制换算小数对账，
防形制错配假红）。真实数据锚直读 data/market_fapi parquet
（test_param_sweep 同款先例），右端固定 2026-11-07 为硬性复现条件
（活数据追加不破坏复算：历史 bar 不变）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.wave_inventory import (
    compute_zigzag_waves, wave_inventory)

RANGES = [{"symbol": "TESTUSDT", "timeframe": "1h",
           "start": "2024-01-01", "end": "2024-02-01"}]


def _df_from_closes(closes, start="2024-01-01", freq="1h"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC",
                        name="timestamp")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"close": c})


class TestZigzagRules:
    """五规则独立单测（任务 3.1）——期望值人工推导。"""

    def test_rule1_unconfirmed_phase_updates_extremum_both_ways(self):
        """首段方向未定：极值候选随新高新低双向更新（取更极端侧），
        随后反向达门槛确认首个 pivot。

        人工推导 [100,102,97,99,110,120,100] thr=0.05：
        102>100 更新候选→102；97 不达门槛（102×0.95=96.9<97）且 97<102
        更新候选→97；99 不达门槛且 99>97 更新候选→99（低点上移）；
        110≥99×1.05=103.95 确认 pivot(99)（位置 3）转上行；120>110
        候选→120；120/100−1=0.2≥0.05 确认 pivot(120)（位置 5）转下行。
        末端 100 不确认。→ 唯一浪 99→120 long，dur=5−3=2。
        """
        waves = compute_zigzag_waves(
            _df_from_closes([100, 102, 97, 99, 110, 120, 100])["close"],
            0.05)
        assert len(waves) == 1
        w = waves[0]
        assert w["start_price"] == 99.0
        assert w["end_price"] == 120.0
        assert w["direction"] == "long"
        assert w["amp"] == pytest.approx(120 / 99 - 1, rel=1e-12)
        assert w["dur"] == 2

    def test_rule2_dir_states_confirm_reversal_at_threshold(self):
        """dir_=±1 回认翻转：下行态反弹达门槛回认谷为 pivot。

        人工推导 [100,95,90,96]：95≤100×0.95=95 等号确认 pivot(100)
        转下行；90<95 候选→90；96/90−1≈0.0667≥0.05 确认 pivot(90)
        转上行。→ 唯一浪 100→90 short amp=0.10。
        """
        waves = compute_zigzag_waves(
            _df_from_closes([100, 95, 90, 96])["close"], 0.05)
        assert len(waves) == 1
        w = waves[0]
        assert (w["start_price"], w["end_price"]) == (100.0, 90.0)
        assert w["direction"] == "short"
        assert w["amp"] == pytest.approx(0.10, rel=1e-12)

    def test_rule3_confirmation_includes_equality_boundary(self):
        """确认含等号：p 恰等于 ext×(1+thr) 时确认（≥ 比较）。

        人工推导 A 案 [100,105,90]：105=100×1.05 恰等号 → pivot(100)
        转上行；90 触发 105/90−1≈0.167≥0.05 回认 pivot(105)。浪
        100→105 long amp=0.05（浮点 0.050000000000000044）。
        """
        waves = compute_zigzag_waves(
            _df_from_closes([100, 105, 90])["close"], 0.05)
        assert len(waves) == 1
        assert waves[0]["direction"] == "long"
        assert waves[0]["start_price"] == 100.0

    def test_rule3b_one_ulp_below_threshold_not_confirmed(self):
        """恰差一个 ULP 不确认：p=105×(1−2⁻⁵²) 低于门槛乘积一 ULP，
        dir_=0 分支不确认——行为与恰等号分立。

        人工推导 B 案 [100, p−, 90]（p−=105 差一个 ULP）：105×(1+thr)
        的 double 高于 p− → 不确认，候选更新→p−；90≤p−×0.95 →
        确认 pivot(p−) 直接转下行。pivots=[p−] 单个 → 无浪。
        """
        p_minus = 105.0 * (1.0 - 2.0 ** -52)
        assert p_minus < 105.0, "构造前提：确差一个 ULP"
        waves = compute_zigzag_waves(
            _df_from_closes([100, p_minus, 90])["close"], 0.05)
        assert waves == [], "差一个 ULP 的首候选不构成 100 起点 pivot"

    def test_rule4_unconfirmed_tail_pivot_excluded(self):
        """末端未确认 pivot 剔除：序列末端新高无反向确认不入清单。

        人工推导 [100,60,120,130]：60≤95 确认 pivot(100) 下行；120/60−1
        =1.0≥0.05 确认 pivot(60) 上行；130>120 候选→130 末端未确认。
        → 唯一浪 100→60 short；130 不产生任何浪。
        """
        waves = compute_zigzag_waves(
            _df_from_closes([100, 60, 120, 130])["close"], 0.05)
        assert len(waves) == 1
        w = waves[0]
        assert (w["start_price"], w["end_price"]) == (100.0, 60.0)
        assert w["dur"] == 1

    def test_rule5_amp_decimal_form_exact_values(self):
        """腿幅小数形制：amp=|p1/p0−1| 全精度小数（demo csv 为百分数
        形制——对账换算注记）；浮点容差 thr−1e-9 为腿幅过滤防御位
        （状态机乘/除比较先行，连续价格输入下不可达——以精确值锚定
        amp 形制承载本规则）。

        人工推导 [100,60,63,57]：浪2=60→63 long amp=63/60−1
        =0.050000000000000044（double 全精度）。
        """
        waves = compute_zigzag_waves(
            _df_from_closes([100, 60, 63, 57])["close"], 0.05)
        assert len(waves) == 2
        assert waves[1]["amp"] == 63 / 60 - 1  # 全精度精确断言
        assert waves[1]["dur"] == 1


class TestWaveInventoryTemplate:
    """模板输出与边界（任务 3.3）。"""

    def _mk(self, closes, ranges=RANGES, **kw):
        df = _df_from_closes(closes)
        return wave_inventory(df, data_ranges=ranges, **kw).to_dict()

    def test_payload_structure_and_summary(self):
        closes = [100, 60, 120, 130, 70, 75]
        out = self._mk(closes, request_start="2024-01-01T00:00:00Z")
        p = out["payload"]
        assert set(p) >= {"waves", "summary", "boundary_effect"}
        assert p["summary"]["n_waves"] == len(p["waves"])
        assert {"n_long", "n_short", "amp_median", "dur_median"} <= set(
            p["summary"])
        assert all(isinstance(w["amp"], float) and w["amp"] > 0
                   for w in p["waves"]), "amp 为正小数（非百分数形制）"

    def test_boundary_effect_fields(self):
        # 前缀 3 bar（<warmup_bars=500）→ insufficient；
        # 人工推导 [100,60,120,130,70,75]：确认 pivot 位置 0/1/4/5，
        # 浪=(100→60)(60→120)(120→70) 起点 0/1/4；request_start=index[3]
        # → 前扩窗内浪 2 条（起点 0、1）被排除
        df = _df_from_closes([100, 60, 120, 130, 70, 75])
        out = wave_inventory(
            df, data_ranges=RANGES,
            request_start=df.index[3], warmup_bars=500).to_dict()
        be = out["payload"]["boundary_effect"]
        assert be["warmup_data_sufficient"] is False
        assert be["warmup_bars_actual"] == 3
        assert be["excluded_boundary_waves"] == 2, "起点在前扩窗内的浪被排除"

    def test_warmup_sufficient_true_when_prefix_covers(self):
        df = _df_from_closes([100 + i * 0.1 for i in range(20)] + [200, 150])
        out = wave_inventory(
            df, data_ranges=RANGES, request_start=df.index[18],
            warmup_bars=10).to_dict()
        be = out["payload"]["boundary_effect"]
        assert be["warmup_data_sufficient"] is True

    @pytest.mark.parametrize("thr", [0, -0.05, 1.0, 1.5])
    def test_threshold_out_of_range_rejected(self, thr):
        with pytest.raises(ResearchError) as ei:
            self._mk([100, 105, 90], threshold=thr)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "threshold"

    @pytest.mark.parametrize("wb", [-1, -500, 2.5])
    def test_warmup_bars_invalid_rejected(self, wb):
        with pytest.raises(ResearchError) as ei:
            self._mk([100, 105, 90], warmup_bars=wb)
        assert ei.value.detail["param"] == "warmup_bars"

    def test_missing_end_rejected(self):
        with pytest.raises(ResearchError, match="无开放右端"):
            self._mk([100, 105, 90],
                     ranges=[{"symbol": "T", "timeframe": "1h",
                              "start": "2024-01-01"}])

    def test_too_few_bars_rejected(self):
        with pytest.raises(ResearchError, match="< 2"):
            self._mk([100])

    def test_idempotent_bitwise(self):
        closes = [100, 60, 120, 130, 70, 75, 72, 68, 140]
        a = self._mk(closes, request_start="2024-01-01T00:00:00Z")
        b = self._mk(closes, request_start="2024-01-01T00:00:00Z")
        assert a == b

    def test_zero_waves_rejected_via_min_samples_path(self):
        # 零浪区间走 build_result min_samples=1 既有拒绝路径（422）
        with pytest.raises(ResearchError, match="小样本拒绝"):
            self._mk([100, 101, 100.5, 101.2])


class TestWaveDemoReconciliation:
    """demo 底账逐数复算（任务 3.2 硬验收锚——W3）。

    全历史起点（2024-10-29）＋右端固定 2026-11-07＋threshold=0.05；
    demo csv amp 为百分数（×100 round 2），契约 amp 为小数——对账
    换算。skip 条件：真实数据不在库。
    """

    MARKET = Path("data/market_fapi")
    RIGHT_EDGE = "2026-11-07"

    def _load(self, sym):
        path = self.MARKET / f"{sym}_4h.parquet"
        if not path.exists():
            pytest.skip(f"真实数据 {sym} 4h 不在库（data/market_fapi）")
        df = pd.read_parquet(path)
        return df[df.index <= pd.Timestamp(self.RIGHT_EDGE, tz="UTC")]

    @pytest.mark.parametrize(
        ("sym", "n_total", "n_long", "n_short", "amp_med"),
        [("BTCUSDT", 79, 40, 39, 0.104),
         ("ETHUSDT", 159, 82, 77, 0.102)])
    def test_demo_counts_and_median(self, sym, n_total, n_long, n_short,
                                    amp_med):
        df = self._load(sym)
        out = wave_inventory(
            df, data_ranges=[{"symbol": sym, "timeframe": "4h",
                              "start": str(df.index[0].date()),
                              "end": self.RIGHT_EDGE}],
            request_start=df.index[0], threshold=0.05).to_dict()
        s = out["payload"]["summary"]
        assert s["n_waves"] == n_total
        assert s["n_long"] == n_long
        assert s["n_short"] == n_short
        # 中位锚双断言：任务书粗锚（demo out 一位小数格式化值）＋
        # csv 精确锚（round(2) 百分数中位换算——量化差 ≤5e-5/值）
        assert s["amp_median"] == pytest.approx(amp_med, abs=5e-3)
        csv_path = (Path(__file__).resolve().parents[3]
                    / "works/research/consensus-ema"
                    / f"r9_d1_waves_{sym}.csv")
        if csv_path.exists():
            demo_med = pd.read_csv(csv_path)["amp"].median() / 100.0
            assert s["amp_median"] == pytest.approx(demo_med, abs=1e-4)

    @pytest.mark.parametrize("sym", ["BTCUSDT", "ETHUSDT"])
    def test_demo_rowwise_reconciliation(self, sym):
        """逐行对账：start/end（前 16 位）/dir（多↔long）/amp（百分数
        ÷100）/dur 五列与冻结 csv 全量一致。"""
        csv_path = (Path(__file__).resolve().parents[3]
                    / "works/research/consensus-ema"
                    / f"r9_d1_waves_{sym}.csv")
        if not csv_path.exists():
            pytest.skip("demo 工件不在库（works/research）")
        demo = pd.read_csv(csv_path)
        df = self._load(sym)
        out = wave_inventory(
            df, data_ranges=[{"symbol": sym, "timeframe": "4h",
                              "start": str(df.index[0].date()),
                              "end": self.RIGHT_EDGE}],
            request_start=df.index[0], threshold=0.05).to_dict()
        waves = out["payload"]["waves"]
        assert len(waves) == len(demo), \
            f"浪总数不一致: 平台 {len(waves)} vs demo {len(demo)}"
        for w, (_, d) in zip(waves, demo.iterrows()):
            assert w["start"][:16].replace("T", " ") == d["start"], (sym, d["start"])
            assert w["end"][:16].replace("T", " ") == d["end"], (sym, d["start"])
            assert w["direction"] == ("long" if d["dir"] == "多" else "short")
            assert w["amp"] == pytest.approx(d["amp"] / 100.0, abs=5e-5)
            assert w["dur"] == int(d["dur"])
