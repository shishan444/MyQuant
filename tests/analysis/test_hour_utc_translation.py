"""hour_utc 翻译面守护（fix-fullrun-baseline-4red 阶段四盲区消解）。

hypothesis_to_strategy._translate_conditions 的 hour_utc 基因承载
分支（security F-1 段——target [0,24) 小时域硬校验/求值轴日内
校验/四则数值比较承载/cross 族拒绝）此前翻译面零测试承载：既有
test_hour_utc_intraday_boundary.py 只测 column_ref 域判定层。

触达路径分工（登记面/翻译面不对齐的实测依据）：
- 登记面拦「条件带 1d 修饰」（resolve_column_ref intraday_only）；
- target 域外值与 cross 族登记面放行——翻译面为唯一防线，正常链
  （register+translate）可达；
- 求值轴分支设计语义=拦库内/手工路径，正常登记的同形态被登记面
  先拦；但翻译基准周期 strategy_timeframe 是 translate 参数（登记
  面不管），基准 1d + 条件无修饰经正常链即可触达（本文件实证）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.errors import ResearchError
from tests.helpers.vr_factory import insert_hypothesis
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
_RANGES = [{"symbol": SYMBOL, "timeframe": "1h",
            "start": "2023-06-01T00:00:00Z",
            "end": "2023-07-01T00:00:00Z"}]
_THEN_RSI = {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "gt", "target": 50}


@pytest.fixture()
def env(tmp_path: Path):
    """1h 数据 + 独立 db（register+translate 真实链装配）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    make_ohlcv(n=800, freq="1h", start="2023-06-01", seed=7).to_parquet(
        data_dir / f"{SYMBOL}_1h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    return {"db": db, "data_dir": data_dir}


def _translate(env, when, strategy_timeframe="1h"):
    """翻译面统一入口（retire-legacy 后经 build_signal_genes——
    hour_utc 域校验/基因承载在 _translate_conditions 同路径）。"""
    from core.analysis.hypothesis_to_strategy import build_signal_genes
    genes, _sources = build_signal_genes(
        when_conditions=[when], then_conditions=[_THEN_RSI],
        db_path=env["db"], strategy_timeframe=strategy_timeframe)
    return genes


class TestHourUtcTranslation:
    def test_inbound_comparison_translated_to_gene(self, env):
        """域内承载：gt 12（[0,24) 内+日内基准）→ 翻译产物含
        hour_utc 基因（indicator/params 空承载/condition gt 阈值/
        入场角色/field None）。"""
        genes = _translate(
            env, {"subject": "hour_utc", "action": "gt", "target": 12.0})
        hour_genes = [g for g in genes if g.indicator == "hour_utc"]
        assert hour_genes, f"hour_utc 基因未承载: {genes}"
        g = hour_genes[0]
        assert g.condition == {"type": "gt", "threshold": 12.0}
        assert g.params == {}
        assert g.role.value == "entry_trigger"
        assert g.field_name is None

    def test_target_outside_domain_rejected(self, env):
        """域外拒：target=25 超出 [0,24)（比较恒 False 无语义，
        security F-1 硬校验）——登记面放行，翻译面唯一防线。"""
        with pytest.raises(ResearchError) as ei:
            _translate(env, {"subject": "hour_utc", "action": "lt",
                             "target": 25.0})
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "[0,24)" in ei.value.message

    def test_cross_action_rejected(self, env):
        """action 拒：cross 族对小时列不支持（周期回绕语义未定义
        ——仅 gt/lt/ge/le × 数值 target）。B6 后 'cross' 在登记面即
        拒（不可表达动作集）；小时列翻译防线经直插库构造存量形
        测试（登记面拒/翻译防线两态各自在案）。"""
        with pytest.raises(ResearchError) as ei_reg:
            _translate(env, {"subject": "hour_utc", "action": "cross",
                             "target": 12.0})
        assert ei_reg.value.code in (
            "RESEARCH_CONDITION_INVALID", "RESEARCH_NOT_EXPRESSIBLE")

    def test_daily_base_timeframe_rejected(self, env):
        """求值轴拒：基准 1d（条件无修饰回退基准）→ 每日一 bar 下
        小时列值恒定无意义。设计语义=拦库内/手工路径；基准周期是
        translate 参数，登记面不管，正常链可触达（本文件实证）。"""
        with pytest.raises(ResearchError) as ei:
            _translate(env, {"subject": "hour_utc", "action": "gt",
                             "target": 12.0}, strategy_timeframe="1d")
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert "求值轴" in ei.value.message

    @pytest.mark.parametrize(
        ("target", "should_pass"),
        [
            pytest.param(0.0, True, id="下界 0 含入"),
            pytest.param(23.9833, True, id="上界内 23.9833"),
            pytest.param(24.0, False, id="上界 24 排除"),
            pytest.param(-0.5, False, id="负值拒"),
        ])
    def test_hour_domain_boundaries(self, env, target, should_pass):
        """[0,24) 小时域边界两侧（属性补强——security F-1 域校验
        的临界条件：0 含入、24 排除、负值拒）。"""
        if should_pass:
            genes = _translate(
                env, {"subject": "hour_utc", "action": "gt",
                      "target": target})
            assert any(g.indicator == "hour_utc" for g in genes)
        else:
            with pytest.raises(ResearchError, match=r"\[0,24\)"):
                _translate(env, {"subject": "hour_utc", "action": "gt",
                                 "target": target})
