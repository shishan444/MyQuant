"""纸盘衍生品指标装载测试（fix-paper-funding-indicator-merge 任务 A）。

覆盖三条验收：
1. funding parquet 可达时，含 FundingZScore 基因的策略装载放行
   （此前一律 indicators_unavailable 拒绝）；
2. funding parquet 缺失时该基因仍拒绝（拒绝语义保持——零数据品种
   不因本修复静默放行不可求值策略）；
3. 纯 K 线策略在无 funding parquet 的 data_dir 装载路径零变化。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.paper_trading.runtime.feed import FeedSubscription
from core.strategy.dna import SignalRole
from tests.helpers.data_factory import make_ohlcv, make_dna, make_signal_gene

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
