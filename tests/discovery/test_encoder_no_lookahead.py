"""discovery 特征编码时序哨兵（fix-indicator-output-mapping 阶段 3，E 域）。

防前视修复锚点：knn_engine/tree_engine 的 MinMaxScaler 曾对全样本
fit_transform（min/max 含未来数据，2026-08 审计 P0 前视集群项）。
修复后 scaler 仅用前段 fit；本哨兵构造后段极端值，断言缩放边界
不吸收未来信息——前视复发即红。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.discovery.feature_encoder import FeatureEncoder


def _ohlcv_with_future_extreme() -> pd.DataFrame:
    """前段 200 根窄幅震荡，后段 100 根出现 10 倍量级极端值。"""
    rng = np.random.default_rng(21)
    idx = pd.date_range("2026-01-01", periods=300, freq="h")
    close = np.concatenate([
        100.0 + rng.standard_normal(200) * 0.5,      # 前段窄幅
        100.0 + rng.standard_normal(100) * 50.0,     # 后段极端
    ])
    close = pd.Series(close, index=idx)
    spread = np.abs(rng.standard_normal(300)) * 0.3 + 0.01
    return pd.DataFrame(
        {
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": pd.Series(rng.random(300) * 1000, index=idx),
            # 前段 rsi 窄幅 [20,80]，后段贴 0/100 极端（哨兵信号列）
            "rsi_14": pd.Series(np.concatenate([
                np.clip(50 + rng.standard_normal(200) * 5, 25, 75),
                np.tile([0.0, 100.0], 50),
            ]), index=idx),
        },
        index=idx,
    )


class TestEncoderNoLookahead:
    def test_scaler_bounds_from_fit_segment_only(self):
        """scaler 边界只由 fit 段决定：后段极端值不得改变 data_min/max。"""
        df = _ohlcv_with_future_extreme()
        fit_end = 200

        enc = FeatureEncoder()
        enc.fit(df.iloc[:fit_end])
        full_sample_enc = FeatureEncoder()
        full_sample_enc.fit(df)

        assert enc.scaler is not None and full_sample_enc.scaler is not None
        assert not np.allclose(enc.scaler.data_min_, full_sample_enc.scaler.data_min_), \
            "fit 段边界与全样本一致：数据不足以证明哨兵有效"
        # 后段极端值不进入边界
        rsi_max = enc.scaler.data_max_[enc.feature_columns.index("rsi_14")]
        assert rsi_max < df["rsi_14"].iloc[fit_end:].max(), \
            "rsi_14 data_max 吸收了未来极端值（前视复发）"

    def test_knn_engine_scaler_excludes_future(self):
        """引擎级：knn 的 encoder 边界不含后段极端值。"""
        from core.discovery.knn_engine import SimilarCaseEngine

        df = _ohlcv_with_future_extreme()
        engine = SimilarCaseEngine()
        engine.fit(df)
        if engine.encoder.scaler is None:
            pytest.skip("引擎未完成编码（API 形态差异），哨兵降级为单元级")
        rsi_min = engine.encoder.scaler.data_min_[engine.encoder.feature_columns.index("rsi_14")]
        assert rsi_min > df["rsi_14"].iloc[200:].min(), \
            "knn encoder 边界吸收未来数据（前视复发）"

    def test_tree_engine_scaler_excludes_future(self):
        """引擎级：tree 的 encoder 边界不含后段极端值。"""
        from core.discovery.tree_engine import PatternDiscoveryEngine

        df = _ohlcv_with_future_extreme()
        engine = PatternDiscoveryEngine()
        engine.discover(df, target="direction")
        if engine.encoder.scaler is None:
            pytest.skip("引擎未完成编码（API 形态差异），哨兵降级为单元级")
        rsi_min = engine.encoder.scaler.data_min_[engine.encoder.feature_columns.index("rsi_14")]
        assert rsi_min > df["rsi_14"].iloc[200:].min(), \
            "tree encoder 边界吸收未来数据（前视复发）"
