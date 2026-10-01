"""数据集命名契约守卫用例（CARD-01/02/03：路径穿越收口）。

锚定问题卡契约（非现状记录）：symbol/interval 是 dataset_id 直拼写入
路径的唯一外部来源，白名单拒绝式校验 + resolve 纵深防御。两档口径
差异属契约：导入侧宽口径 [0-9]+[mhdw]，拉取/读取侧 TF_SECONDS 键集。
"""

import pytest

pytestmark = [pytest.mark.unit]

import pandas as pd
from unittest.mock import patch

from core.data.csv_importer import import_csv, import_csv_batch
from core.data.mtf_loader import find_parquet
from core.data.updater import (
    update_funding_data,
    update_market_data,
    update_open_interest_data,
)
from core.data.validation import (
    ensure_path_within,
    validate_import_interval,
    validate_known_interval,
    validate_symbol,
)

BINANCE_ROW = (
    "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """空数据目录（覆盖全局 fixture 的 dummy parquet 预置——守卫用例须从空盘起证）。"""
    data_dir = tmp_path / "guard_data"
    data_dir.mkdir()
    return data_dir


class TestValidateSymbol:
    """1-20 位字母数字白名单（拒绝而非清洗）。"""

    @pytest.mark.parametrize(
        "symbol",
        [
            pytest.param("A", id="1 位下界"),
            pytest.param("BTCUSDT", id="常规交易对"),
            pytest.param("a" * 20, id="20 位上界"),
        ],
    )
    def test_valid_passes_through(self, symbol: str) -> None:
        assert validate_symbol(symbol) == symbol

    @pytest.mark.parametrize(
        "symbol",
        [
            pytest.param("", id="空串"),
            pytest.param("a" * 21, id="21 位超限"),
            pytest.param("../outside", id="路径穿越"),
            pytest.param("BTC/USDT", id="斜杠"),
            pytest.param("BTC USDT", id="空格"),
            pytest.param("BTC-USDT", id="连字符"),
            pytest.param("１２３", id="全角数字"),
        ],
    )
    def test_invalid_rejected(self, symbol: str) -> None:
        with pytest.raises(ValueError, match="symbol 非法"):
            validate_symbol(symbol)


class TestValidateImportInterval:
    """导入侧宽口径：[0-9]+[mhdw] 只约束形状。"""

    @pytest.mark.parametrize(
        "interval",
        [
            pytest.param("4h", id="小时"),
            pytest.param("30m", id="分钟"),
            pytest.param("1d", id="天"),
            pytest.param("1w", id="周"),
            pytest.param("7m", id="7m 宽口径合法（形状对即可）"),
        ],
    )
    def test_valid_passes_through(self, interval: str) -> None:
        assert validate_import_interval(interval) == interval

    @pytest.mark.parametrize(
        "interval",
        [
            pytest.param("4H", id="大写单位"),
            pytest.param("h4", id="单位在前"),
            pytest.param("4hz", id="多尾字符"),
            pytest.param("", id="空串"),
            pytest.param("../evil", id="路径穿越"),
            pytest.param("１m", id="全角数字"),
        ],
    )
    def test_invalid_rejected(self, interval: str) -> None:
        with pytest.raises(ValueError, match="interval 非法"):
            validate_import_interval(interval)


class TestValidateKnownInterval:
    """拉取/读取侧窄口径：TF_SECONDS 键集。"""

    @pytest.mark.parametrize("interval", ["1m", "15m", "1h", "2h", "4h", "1d", "3d", "1w"])
    def test_known_passes_through(self, interval: str) -> None:
        assert validate_known_interval(interval) == interval

    @pytest.mark.parametrize(
        "interval",
        [
            pytest.param("7m", id="形状合法但不在键集"),
            pytest.param("4H", id="大写单位"),
            pytest.param("../x", id="路径穿越"),
            pytest.param("", id="空串"),
        ],
    )
    def test_unknown_rejected(self, interval: str) -> None:
        with pytest.raises(ValueError, match="interval 未知"):
            validate_known_interval(interval)


class TestEnsurePathWithin:
    """resolve 纵深防御：写入路径必须落在 root 内。"""

    def test_path_inside_root_passes(self, tmp_path) -> None:
        ensure_path_within(tmp_path / "SYM_4h.parquet", tmp_path)

    def test_path_outside_root_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="写入路径越界"):
            ensure_path_within(tmp_path / ".." / "outside.parquet", tmp_path)


class TestImportCsvGuards:
    """导入入口：探测/显式得 symbol/interval 后、读文件前拒绝非法命名。"""

    def test_explicit_traversal_symbol_rejected(self, tmp_path, tmp_data_dir) -> None:
        csv = tmp_path / "data.csv"
        csv.write_text(BINANCE_ROW)
        with pytest.raises(ValueError, match="symbol 非法"):
            import_csv(csv, tmp_data_dir, symbol="../../outside", interval="4h")
        assert not (tmp_data_dir / "outside.parquet").exists()  # 无文件残留

    def test_explicit_traversal_interval_rejected(self, tmp_path, tmp_data_dir) -> None:
        csv = tmp_path / "data.csv"
        csv.write_text(BINANCE_ROW)
        with pytest.raises(ValueError, match="interval 非法"):
            import_csv(csv, tmp_data_dir, symbol="BTCUSDT", interval="../../evil")
        assert list(tmp_data_dir.glob("*.parquet")) == []

    def test_batch_traversal_symbol_rejected(self, tmp_path, tmp_data_dir) -> None:
        csv = tmp_path / "data.csv"
        csv.write_text(BINANCE_ROW)
        # interval 齐备以越过元数据完整性检查（该检查先于白名单）
        with pytest.raises(ValueError, match="symbol 非法"):
            import_csv_batch(
                [csv], tmp_data_dir, symbol="../../outside", interval="4h"
            )
        assert list(tmp_data_dir.glob("*.parquet")) == []


class TestUpdaterGuards:
    """updater 三入口：校验前置（fetch 发生前拒绝，无网络副作用）。"""

    def test_market_data_bad_symbol_prefetch(self, tmp_path) -> None:
        with patch("core.data.updater.fetch_klines") as mock_fetch:
            with pytest.raises(ValueError, match="symbol 非法"):
                update_market_data("BTC/USDT", "4h", data_dir=tmp_path)
        mock_fetch.assert_not_called()

    def test_market_data_bad_interval_prefetch(self, tmp_path) -> None:
        with patch("core.data.updater.fetch_klines") as mock_fetch:
            with pytest.raises(ValueError, match="interval 未知"):
                update_market_data("BTCUSDT", "4H", data_dir=tmp_path)
        mock_fetch.assert_not_called()

    def test_funding_bad_symbol_prefetch(self, tmp_path) -> None:
        with patch("core.data.fetcher.fetch_funding_history") as mock_fetch:
            with pytest.raises(ValueError, match="symbol 非法"):
                update_funding_data("x/y", data_dir=tmp_path)
        mock_fetch.assert_not_called()

    def test_open_interest_bad_symbol_prefetch(self, tmp_path) -> None:
        with patch("core.data.fetcher.fetch_open_interest_history") as mock_fetch:
            with pytest.raises(ValueError, match="symbol 非法"):
                update_open_interest_data("../evil", data_dir=tmp_path)
        mock_fetch.assert_not_called()

    def test_open_interest_bad_tf_element_prefetch(self, tmp_path) -> None:
        """timeframes 逐元素校验：混入一个非法元素即整体拒绝。"""
        with patch("core.data.fetcher.fetch_open_interest_history") as mock_fetch:
            with pytest.raises(ValueError, match="interval 未知"):
                update_open_interest_data(
                    "BTCUSDT", data_dir=tmp_path, timeframes=["5m", "../x"]
                )
        mock_fetch.assert_not_called()


class TestFindParquetGuard:
    """读取入口：未知周期在文件探测前拒绝。"""

    def test_traversal_tf_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="interval 未知"):
            find_parquet(tmp_path, "BTCUSDT", "../x")

    def test_uppercase_tf_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="interval 未知"):
            find_parquet(tmp_path, "BTCUSDT", "4H")
