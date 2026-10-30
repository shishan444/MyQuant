"""纸盘衍生品指标装载与运行期注入测试（fix-paper-funding-indicator-merge）。

特征测试网（现状记录语义），四组覆盖：
1. 装载放行面（任务 A 原三验收）：funding parquet 可达时 FundingZScore
   基因装载放行；归档缺失时仍拒绝；纯 K 线策略装载路径零变化；
2. 装载降级分支：坏档/空档/缺目标列三形态一律降级为缺失语义——
   衍生品基因照旧拒绝、纯 K 线装载零阻断、坏档 warning 留痕；
3. OI 注入路径：OI_ChangeRate 基因 + {symbol}_oi_{tf} 归档装载放行
   （OI 归档文件名带周期后缀，与 funding 归档无后缀形制不同）；
4. 运行期注入（任务 B）：process_frames 的运行帧（feed 纯 K 线列）
   经同款 merge 后衍生品基因可求值——全帧回放至结题 + stale 语义
   （归档末端之后的 bar 沿用最后已知费率）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.repository import paper
from core.strategy.dna import SignalRole
from tests.helpers.data_factory import (
    make_ohlcv, make_dna, make_mtf_dna, make_signal_gene,
)

from tests.paper_trading.test_paper_executor import _Harness, _funding_frame


def _fzs_dna():
    """含 FundingZScore 进场基因的 DNA（进出场同指标极端分置）。"""
    entry = make_signal_gene(
        indicator="FundingZScore",
        params={"period": 30},
        role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt",
        condition_value=1.0,
    )
    exit_gene = make_signal_gene(
        indicator="FundingZScore",
        params={"period": 30},
        role=SignalRole.EXIT_TRIGGER,
        condition_type="lt",
        condition_value=-1.0,
    )
    dna = make_dna()
    dna.signal_genes = [entry, exit_gene]
    return dna


def _write_funding_parquet(data_dir: Path, n: int = 40) -> None:
    """写覆盖 K 线全窗口的 funding 归档（8h 结算网格）。"""
    kline = pd.read_parquet(data_dir / "BTCUSDT_4h.parquet")
    start = kline.index.min()
    times = pd.date_range(start=start, periods=n, freq="8h", tz="UTC")
    save_parquet(_funding_frame(list(times)), data_dir / "BTCUSDT_funding.parquet")


def _write_degraded_funding_parquet(data_dir: Path, mode: str) -> None:
    """按降级形态写 funding 归档（坏档/空档/缺目标列三形态）。"""
    path = data_dir / "BTCUSDT_funding.parquet"
    if mode == "corrupt":
        path.write_bytes(b"not a parquet file")
    elif mode == "empty":
        save_parquet(_funding_frame([]), path)
    else:  # wrong_column：合法非空帧但无 funding_rate 列
        idx = pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=5, freq="8h", tz="UTC"),
            name="timestamp")
        save_parquet(
            pd.DataFrame({"other": [1.0] * 5}, index=idx), path)


_DEGRADE_MODES = [
    pytest.param("corrupt", id="坏档-非parquet字节"),
    pytest.param("empty", id="空档-合法空帧"),
    pytest.param("wrong_column", id="缺目标列-列名不匹配"),
]


class TestFundingIndicatorMount:
    def test_funding_gene_mounts_with_funding_parquet(self, tmp_path):
        h = _Harness(tmp_path, dna=_fzs_dna())
        _write_funding_parquet(h.data_dir)
        # funding 归档后建 feed（装载读归档与 feed 索引互不依赖，
        # registry 在 Harness 构造期已建——此处直接重放装载）
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] != "error"

    def test_funding_gene_still_rejected_without_parquet(self, tmp_path):
        h = _Harness(tmp_path, dna=_fzs_dna())
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "indicators_unavailable" in (h.task()["error_detail"] or "")

    def test_pure_kline_unchanged_without_funding_parquet(self, tmp_path):
        # 纯 RSI 基因 + data_dir 无 funding parquet：装载路径与修复前
        # 逐字节一致（prepare 通过——merger 双 None 原样返回语义）
        h = _Harness(tmp_path)
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] != "error"


class TestLoadDegradeBranches:
    """装载期注入降级分支（_load_deriv_frame 三降级：坏档/空档/缺列）。

    特征记录：三形态一律降级为缺失语义——衍生品基因照旧拒绝
    （indicators_unavailable），纯 K 线策略装载零阻断（归档问题
    不误伤纯价格策略）。缺失分支由 TestFundingIndicatorMount 后两
    用例锁定，此处补齐另外三形态。
    """

    @pytest.mark.parametrize("mode", _DEGRADE_MODES)
    def test_degrade_rejects_deriv_gene_same_as_missing(
            self, tmp_path, mode):
        h = _Harness(tmp_path, dna=_fzs_dna())
        _write_degraded_funding_parquet(h.data_dir, mode)
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "indicators_unavailable" in (h.task()["error_detail"] or "")

    @pytest.mark.parametrize("mode", _DEGRADE_MODES)
    def test_degrade_keeps_pure_kline_mount_untouched(
            self, tmp_path, mode):
        h = _Harness(tmp_path)
        _write_degraded_funding_parquet(h.data_dir, mode)
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] != "error"

    def test_corrupt_parquet_logs_warning(self, tmp_path, caplog):
        """坏档降级 warning 留痕（观测面——空档/缺列是静默语义）。"""
        h = _Harness(tmp_path, dna=_fzs_dna())
        # 坑位补偿（同 test_scheduler.py:976）：init_db_ext→alembic
        # env.py fileConfig 重置 root handlers 摘掉 caplog 捕获
        # handler——补挂回来才能断言日志
        logging.getLogger().addHandler(caplog.handler)
        _write_degraded_funding_parquet(h.data_dir, "corrupt")
        with caplog.at_level(logging.WARNING, logger="paper_runtime"):
            h.make_executor().prepare()
        assert any(
            "衍生品归档读取失败" in r.message for r in caplog.records)


def _oi_dna():
    """含 OI_ChangeRate 进场基因的 DNA（进出场同指标极端分置）。"""
    entry = make_signal_gene(
        indicator="OI_ChangeRate",
        params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt",
        condition_value=10.0,
    )
    exit_gene = make_signal_gene(
        indicator="OI_ChangeRate",
        params={"period": 14},
        role=SignalRole.EXIT_TRIGGER,
        condition_type="lt",
        condition_value=-10.0,
    )
    dna = make_dna()
    dna.signal_genes = [entry, exit_gene]
    return dna


class TestOiIndicatorMount:
    """OI 注入路径（_load_deriv_frame 的 oi 分支——文件名带周期后缀）。"""

    def test_oi_gene_mounts_with_oi_parquet(self, tmp_path):
        """OI_ChangeRate 基因 + {symbol}_oi_4h.parquet 装载放行。

        特征锁定 OI 归档文件名形制：{symbol}_oi_{timeframe}.parquet
        （与 funding 归档 {symbol}_funding.parquet 无周期后缀不同）。
        """
        h = _Harness(tmp_path, dna=_oi_dna())
        kline = pd.read_parquet(h.data_dir / "BTCUSDT_4h.parquet")
        times = pd.date_range(
            start=kline.index.min(), periods=40, freq="4h", tz="UTC")
        save_parquet(
            pd.DataFrame(
                {"open_interest": [100.0 + i for i in range(40)]},
                index=pd.DatetimeIndex(times, name="timestamp")),
            h.data_dir / "BTCUSDT_oi_4h.parquet")
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] != "error"


def _funding_pressure_dna():
    """含 FundingPressure 进场基因的 DNA（entry 恒真形制，stale 用）。

    rate 恒 0.0001 时满窗 pressure=8×0.0001=0.0008，entry 条件
    gt 1e-9 恒真；exit 条件 lt -1.0 恒假。FundingPressure 对窗口内
    NaN 敏感（rolling sum 含 NaN → NaN → 条件 False）——stale 失效
    （归档末端后列变 NaN）时信号消失，区分度成立。
    """
    entry = make_signal_gene(
        indicator="FundingPressure",
        params={"period": 8},
        role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt",
        condition_value=1e-9,
    )
    exit_gene = make_signal_gene(
        indicator="FundingPressure",
        params={"period": 8},
        role=SignalRole.EXIT_TRIGGER,
        condition_type="lt",
        condition_value=-1.0,
    )
    dna = make_dna()
    dna.signal_genes = [entry, exit_gene]
    return dna


class TestRuntimeInjection:
    """运行期注入（process_frames 信号组装前的 merge）。

    装载放行只证明装载帧（parquet 历史帧）有衍生列；运行帧来自
    feed 内存序列（纯 K 线列），未经注入则衍生品基因在信号组装处
    不可求值。归档缺失时运行期静默跳过（双 None 原样返回）已由
    test_paper_executor 全量回放隐式覆盖（默认 DNA + 无归档）。
    """

    def test_runtime_funding_gene_full_replay_completes(self, tmp_path):
        """运行帧注入后衍生品基因可求值：全帧回放至窗口结题无 error。"""
        h = _Harness(tmp_path, dna=_fzs_dna(), window_bars=30)
        _write_funding_parquet(h.data_dir)
        ex = h.make_executor()
        assert ex.prepare() is True
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = h.task()
        assert task["status"] == "completed"
        assert task["bars_processed"] == 30
        assert task["error_detail"] is None

    def test_runtime_stale_semantics_after_archive_end(self, tmp_path):
        """stale 语义：归档末端之后的 bar 沿用最后已知费率（ffill）。

        funding 归档只写 5 个 8h 结算点（覆盖前 10 根 4h bar），窗口
        16 根——bar 10 之后全部在归档末端之后。initial_cash=0 使开仓
        恒被 insufficient_funds 拒（rejected 信号行同带指标快照）。
        断言：stale 段（bar_time ≥ 第 10 根）存在信号行，且其
        indicator_snapshot 的 funding_pressure_8=满窗值 0.0008——
        stale 携值证据（若末端后变 NaN，rolling sum 传染为 NaN、
        条件恒 False、stale 段零信号行）。
        """
        h = _Harness(
            tmp_path, dna=_funding_pressure_dna(), window_bars=16,
            initial_cash=0.0)
        _write_funding_parquet(h.data_dir, n=5)
        ex = h.make_executor()
        assert ex.prepare() is True
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)

        task = h.task()
        assert task["status"] == "completed"
        stale_from = h.enhanced.index[10].isoformat()
        stale_rows = [
            r for r in paper.list_signals(h.db, h.task_id, limit=1000)
            if r["bar_time"] >= stale_from
            and r["outcome"] in ("filled", "rejected")]
        assert stale_rows, "stale 段须仍有信号行（ffill 携值求值）"
        for r in stale_rows:
            snapshot = json.loads(r["indicator_snapshot"])
            assert snapshot["funding_pressure_8"] == pytest.approx(0.0008)


def _fzs_gene_pair():
    """FundingZScore 进出场基因对（极端分置——进出场同指标形制）。"""
    entry = make_signal_gene(
        indicator="FundingZScore",
        params={"period": 30},
        role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt",
        condition_value=1.0,
    )
    exit_gene = make_signal_gene(
        indicator="FundingZScore",
        params={"period": 30},
        role=SignalRole.EXIT_TRIGGER,
        condition_type="lt",
        condition_value=-1.0,
    )
    return [entry, exit_gene]


class TestMtfRuntimeInjection:
    """MTF×执行层衍生品基因运行期注入（CARD-01 修复锚）。

    修复历史：修复前装载放行（装载期帧先注入后组装）而运行期首帧
    崩——merge 在 _assemble_mtf_frames 之后只重绑局部 df，MTF 帧表
    主周期帧仍纯 K 线，执行层衍生品基因不可求值抛 ResearchError
    （缺陷现状锚见 /tmp/tester-repro-mtf.log 复现记录）。修复后
    （_inject_derivatives 提前到帧组装前）主周期帧带衍生列，执行层
    基因运行期可求值——全帧推进至窗口结题，与单周期路径同构。
    """

    @staticmethod
    def _make_mtf_harness(tmp_path: Path, dna):
        """MTF 装配（4h 主帧+1d 辅 parquet+重建 registry）。"""
        h = _Harness(tmp_path)
        daily = make_ohlcv(n=60, freq="1d", seed=7, base_price=100.0)
        save_parquet(daily, h.data_dir / "BTCUSDT_1d.parquet")
        h.registry = FeedRegistry(
            h.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame([]))
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-mtf-fzs",
            dna_snapshot_json=dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        return h, daily

    def test_mtf_exec_layer_funding_gene_runtime_replays_to_completion(
            self, tmp_path):
        """修复锚：装载放行（pending 非 error）+运行期全帧推进至
        窗口结题（帧表主周期帧带衍生列，执行层基因可求值）。"""
        dna = make_mtf_dna(timeframes=("1d", "4h"))
        dna.layers[1].signal_genes = _fzs_gene_pair()  # 4h 执行层
        h, daily = self._make_mtf_harness(tmp_path, dna)
        _write_funding_parquet(h.data_dir)
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] == "pending"
        # 辅帧注入（1d 层真实参与——不混「辅层跳过」容错路径）
        ex._make_aux_handler("1d")(compute_all_indicators(daily), 0)
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.df, 0)
        task = h.task()
        assert task["status"] == "completed"
        assert task["bars_processed"] == 30
        assert task["error_detail"] is None

    def test_mtf_aux_layer_funding_gene_rejected_at_mount(
            self, tmp_path):
        """对照形态：辅层衍生品基因装载即拒——装载期辅层帧来自
        feed 内存序列（纯 K 线、不注入衍生列），试算不可求值转
        indicators_unavailable（拒绝语义自洽，非装载绿运行崩）。"""
        dna = make_mtf_dna(timeframes=("1d", "4h"))
        dna.layers[0].signal_genes = _fzs_gene_pair()  # 1d 辅层
        h, _ = self._make_mtf_harness(tmp_path, dna)
        _write_funding_parquet(h.data_dir)
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "indicators_unavailable" in (h.task()["error_detail"] or "")
