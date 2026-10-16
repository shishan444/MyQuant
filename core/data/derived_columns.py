"""派生基础列注入（add-session-structure-expression 任务 7）。

hour_utc（bar 开盘 UTC 小时，[0,24) 半开浮点域）等由时间索引派生的
基础列——两装载入口（load_and_prepare_df / load_mtf_data）各调一次
本单点（只挂一处必红的对拍锚在 tests/data/）。
"""
from __future__ import annotations

import pandas as pd


def inject_derived_columns(df: pd.DataFrame, timeframe: str = "") -> pd.DataFrame:
    """就地附加派生基础列（幂等——在场跳过）。

    hour_utc = index.hour + index.minute / 60（开盘时刻 UTC 小时；
    [0,24) 半开——23:59 → 23.9833）。naive 索引按 UTC 解释（装载层
    已统一 tz-aware，防御分支）。

    分周期注入（design 2.7 双保险之二，D-6②）：timeframe 为日线及
    以上（>=1d）不注入——小时列在每日一 bar 帧上值恒定无物理语义，
    物理列缺失与域校验拒绝（column_ref intraday_only）互为双保险；
    未知/缺省周期保持注入（兼容面——调用方无周期上下文时不断供）。
    """
    from core.data.timeframes import TF_SECONDS
    tf_sec = TF_SECONDS.get(timeframe or "")
    if tf_sec is not None and tf_sec >= 86400:
        return df
    if "hour_utc" in df.columns:
        return df
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    df["hour_utc"] = idx.hour + idx.minute / 60.0
    return df
