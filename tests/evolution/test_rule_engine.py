"""Tests for core.validation.rule_engine — trade pairing logic.

Focuses on _pair_trades (the highest-ROI pure function): entry/exit mask
pairing into sequential TradeRecords. _pair_trades has no I/O and no
indicator dependency, so it is tested directly with constructed masks.
"""
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.validation.rule_engine import _pair_trades, TradeRecord  # noqa: E402


def _df(closes):
    """Build a minimal DataFrame with a close column (all _pair_trades reads)."""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="4h")
    return pd.DataFrame({"close": closes}, index=idx)


def _mask(df, positions):
    """Boolean mask True at the given integer positions."""
    s = pd.Series(False, index=df.index)
    for p in positions:
        s.iloc[p] = True
    return s


class TestPairTrades:
    """_pair_trades: entry/exit mask -> sequential TradeRecords."""

    def test_single_entry_single_exit(self):
        df = _df([100, 105, 110])
        trades = _pair_trades(df, _mask(df, [0]), _mask(df, [2]))
        assert len(trades) == 1
        t = trades[0]
        assert t.entry_price == 100.0
        assert t.exit_price == 110.0
        assert t.return_pct == pytest.approx(10.0)
        assert t.is_win is True

    def test_no_entry_yields_no_trades(self):
        df = _df([100, 105, 110])
        assert _pair_trades(df, _mask(df, []), _mask(df, [2])) == []

    def test_entry_without_exit_is_skipped(self):
        """An entry with no subsequent exit leaves the trade open (not recorded)."""
        df = _df([100, 105, 110])
        assert _pair_trades(df, _mask(df, [0]), _mask(df, [])) == []

    def test_multiple_rounds_pair_sequentially(self):
        # entry@0 -> exit@1, then entry@2 -> exit@3
        df = _df([100, 110, 105, 115, 108])
        trades = _pair_trades(df, _mask(df, [0, 2]), _mask(df, [1, 3]))
        assert len(trades) == 2
        assert trades[0].return_pct == pytest.approx(10.0)      # (110-100)/100
        assert trades[1].return_pct == pytest.approx(9.52, abs=0.01)  # (115-105)/105

    def test_exit_must_be_strictly_after_entry(self):
        """An exit at the same index as the entry does not pair with it;
        the search starts at entry_loc + 1."""
        df = _df([100, 105, 110])
        trades = _pair_trades(df, _mask(df, [0]), _mask(df, [0, 1]))
        assert len(trades) == 1
        assert trades[0].exit_price == 105.0  # exit@1, not exit@0

    def test_consecutive_entries_first_one_pairs(self):
        """When entries fire on consecutive bars before any exit, the first
        entry pairs with the next exit; scanning resumes after that exit."""
        df = _df([100, 102, 110, 108])
        trades = _pair_trades(df, _mask(df, [0, 1]), _mask(df, [2]))
        assert len(trades) == 1
        assert trades[0].entry_price == 100.0  # first entry wins

    def test_losing_trade_is_win_false(self):
        df = _df([110, 100])
        trades = _pair_trades(df, _mask(df, [0]), _mask(df, [1]))
        assert trades[0].return_pct < 0
        assert trades[0].is_win is False

    def test_return_pct_rounded_to_two_decimals(self):
        df = _df([100, 133])
        trades = _pair_trades(df, _mask(df, [0]), _mask(df, [1]))
        assert trades[0].return_pct == 33.0  # round((133-100)/100*100, 2)

    def test_trade_record_fields_complete(self):
        df = _df([100, 105])
        trades = _pair_trades(df, _mask(df, [0]), _mask(df, [1]))
        assert len(trades) == 1
        t = trades[0]
        assert isinstance(t, TradeRecord)
        assert t.entry_time == str(df.index[0])
        assert t.exit_time == str(df.index[1])
        assert t.entry_price == 100.0
        assert t.exit_price == 105.0

    def test_empty_masks_yields_no_trades(self):
        df = _df([100, 105, 110])
        assert _pair_trades(df, _mask(df, []), _mask(df, [])) == []
