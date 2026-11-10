"""translate 试算装载单源锚（fr_296f43410afb 修复④）。

锚点面（断言从 design D5④「两路径共用同一装载权威实现」推导）：
- 主锚（修复前红）：funding 基插件（表达式引用衍生合并列
  funding_rate）假设 translate 成功——修复前试算裸 load_parquet 不
  合并衍生品，插件求值静默缺列 → "公式组件试算零产出: all_nan"
  整体拒绝（缺陷④本体）；修复后走 load_and_prepare_df 与登记预检/
  回测同源（衍生合并+派生基础列注入），试算通过且 component_eval
  记录 evaluated_bars>0。
- 同源数值一致断言：同一插件列在 translate 试算装载链与登记预检
  装载链的求值产物在同一索引上逐位相等（同源注入的机械证明——
  两链喂给 enrich 的 df 出自同一装载权威实现）。
- 回归面：无 funding 数据目录下 close 基插件行为不变（既有
  test_plugin_translation.py 全量回归——错误语义锚在其中）。
"""

import json
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.analysis.plugin_service import (
    enrich_with_plugin_columns, register_formula_plugin)
from core.analysis.templates.precheck_hypothesis import _load_precheck_df
from core.data.mtf_loader import load_and_prepare_df
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def funding_data_dir(tmp_path: Path) -> Path:
    """OHLCV＋funding 衍生品合成目录（4h bar×420 根＋8h funding 全覆盖）。

    funding 起点早于 OHLCV 起点（ffill 合并后全程有值——两链求值
    非全 NaN 的数据前提）。
    """
    d = tmp_path / "market"
    d.mkdir()
    n = 420
    rng = np.random.default_rng(7)
    # 起点 2023-12-10：请求段 [2024-01-20, 2024-02-01] 前有约 246 根
    # （覆盖登记预检 warmup floor 200 前扩——_load_precheck_df 装载前提）
    idx = pd.date_range("2023-12-10", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 3000 + np.cumsum(rng.normal(0, 8, n))
    pd.DataFrame({
        "open": close, "high": close + 20, "low": close - 20,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(d / "ETHUSDT_4h.parquet")
    f_idx = pd.date_range("2023-11-01", periods=520, freq="8h", tz="UTC",
                          name="timestamp")
    pd.DataFrame(
        {"funding_rate": rng.normal(0.0001, 0.00005, len(f_idx))},
        index=f_idx).to_parquet(d / "ETHUSDT_funding.parquet")
    return d


_RANGES = [{"symbol": "ETHUSDT", "timeframe": "4h",
            "start": "2024-01-20T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


def _register_funding_hyp(db: Path, col: str) -> str:
    """funding 基插件条件假设（when 插件入场＋then 内置出场——
    缺陷③修复后插件出场腿同形亦可，本锚聚焦试算装载面用最小组）。"""
    return register_hypothesis(
        db, conditions={
            "when_conditions": [
                {"subject": col, "action": "gt", "target": 0.0}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"}, "action": "gt", "target": 70}],
        }, statement="funding 试算单源测试",
        data_ranges=_RANGES, strategy_params={"stop_loss": 0.05},
    )["hypothesis_id"]


class TestTrialLoadSingleSource:
    """修复④验收面：funding 基插件试算经衍生合并装载非全 NaN。"""

    def test_funding_based_plugin_trial_succeeds(self, db, funding_data_dir):
        """主锚（修复前红）：引用 funding_rate 的插件假设 translate
        成功——修复前裸装载 funding_rate 列缺失 → 求值静默跳过 →
        "公式组件试算零产出" 拒绝；修复后同源装载非全 NaN 通过，
        component_eval 记录 evaluated_bars>0。"""
        col = register_formula_plugin(
            db, name="fp_fz",
            expression="(rolling_mean(funding_rate, 54) - "
                       "rolling_mean(funding_rate, 360)) / "
                       "rolling_std(funding_rate, 360)")["column"]
        hid = _register_funding_hyp(db, col)
        out = translate_hypothesis(db, data_dir=funding_data_dir,
                                   hypothesis_id=hid)
        assert out["status"] == "draft"
        records = [e for e in rr.list_research_log(
            db, entry_type="component_eval", limit=200)
            if e.get("ref_id") == out["strategy_id"]]
        assert records, "试算成功应写 component_eval 记录"
        summary = json.loads(records[0]["summary"])
        assert summary["column"] == col
        assert summary["evaluated_bars"] > 0, "插件列非全 NaN（修复④）"

    def test_translation_of_evidence_shaped_exit_leg(self, db,
                                                     funding_data_dir):
        """插件出场腿形制（缺陷③+④合流面）：when 插件入场＋then 插件
        出场（两角色同列）假设 translate 成功——出场腿校验（C3）与
        试算装载（C4）两修复合流后全链放行。"""
        col = register_formula_plugin(
            db, name="mom_fx", expression="diff(log(close), 24)")["column"]
        hid = register_hypothesis(
            db, conditions={
                "when_conditions": [
                    {"subject": col, "action": "lt", "target": 0.0}],
                "then_conditions": [
                    {"subject": col, "action": "gt", "target": 0.02}],
            }, statement="插件出场腿试算",
            data_ranges=_RANGES, strategy_params={"stop_loss": 0.05},
        )["hypothesis_id"]
        out = translate_hypothesis(db, data_dir=funding_data_dir,
                                   hypothesis_id=hid)
        assert out["status"] == "draft"
        records = [e for e in rr.list_research_log(
            db, entry_type="component_eval", limit=200)
            if e.get("ref_id") == out["strategy_id"]]
        assert records and json.loads(
            records[0]["summary"])["evaluated_bars"] > 0


class TestTwoPathValueConsistency:
    """同源注入机械证明：登记预检链与 translate 试算链插件列逐位一致。"""

    def test_precheck_and_trial_columns_identical(self, db,
                                                  funding_data_dir):
        """同一插件列在两装载链的求值产物同索引逐位相等。

        登记链=_load_precheck_df（warmup 前扩装载→enrich→存在性校验）；
        试算链装载=load_and_prepare_df（_run_formula_trial 同款调用形制
        ——compute_indicators=False）→enrich。两链 df 出自同一装载权威
        （衍生合并+派生列注入单点），同索引上插件列必须逐位相等。
        """
        col = register_formula_plugin(
            db, name="fp_fz_c",
            expression="zscore(funding_rate, 30)")["column"]
        when = [{"subject": col, "action": "gt", "target": 0.0}]

        # 登记预检链（返回 df 含 warmup 前缀与插件列）
        pre_df, start_ts, _warmup = _load_precheck_df(
            db, funding_data_dir, "ETHUSDT", "4h",
            _RANGES[0]["start"], _RANGES[0]["end"], when)
        assert col in pre_df.columns, "预检链插件列在场"
        assert int(pre_df[col].notna().sum()) > 0, "预检链非全 NaN"

        # translate 试算链装载（与 _run_formula_trial 同形制）
        trial_df = load_and_prepare_df(
            funding_data_dir, "ETHUSDT", "4h", compute_indicators=False)
        trial_df = enrich_with_plugin_columns(db, trial_df, {col})
        assert col in trial_df.columns, "试算链插件列在场（修复④）"
        assert int(trial_df[col].notna().sum()) > 0, "试算链非全 NaN"

        # 同索引逐位一致（请求段索引两链都在场）
        common = pre_df.index.intersection(trial_df.index)
        request_idx = common[common >= start_ts]
        assert len(request_idx) > 0, "请求段索引交集非空"
        pd.testing.assert_series_equal(
            pre_df.loc[request_idx, col], trial_df.loc[request_idx, col],
            check_names=False)
