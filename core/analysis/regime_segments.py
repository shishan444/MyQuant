"""行情分段权威模块（add-regime-split；MCP 0.2.7「能辨真假」检查②）。

以与行情快照（get_market_snapshot）同一套机械判读规则沿时间轴生成
逐 bar 状态序列，同状态合并成带类型的段——段即类型，一步到位
（2026-09-25 三轮评估裁决：推翻规划原文 KS 检验切段——KS 检测分布
漂移边界，与「切类型段」目标不对齐且需二次定型；状态直切在状态
空间切段、判读与快照同源同一把尺、新增参数仅最小段长）。

判读规则（内建不可调，输出 gates 披露——0.2.6 三道门全族纪律）：
- 高波动覆盖态：atr_pct（ATR/close×100，本层内联派生不注册指标——
  快照决策 4 惯例）的滚动分位 >= vol_pct(0.80) 判「高波动」，优先于
  方向三态（风险管理维度优先）
- 方向三态：adx_state 自 market_snapshot 提升（内容逐字不动）——
  ADX<20 震荡 / >25 趋势 / 其间过渡；过渡态与抖动经最小段长吸收

段定义权威在本模块（0.2.10 run_staged_validation 消费此处切段函数，
检查侧与考试侧段边界同源——考试侧不自行切段）。

平滑实现说明：短段吸收（短于 min_segment_bars 的段迭代并入相邻较长
段）替代滚动众数——语义目标一致（压制过渡抖动）且保留段边界清晰。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

# 判读参数（内建常量；gates 披露形制同 0.2.6）
GATES: Dict[str, Any] = {
    "adx_trend": 25.0,        # ADX > 此值判趋势（自 0.2.4 _adx_state 同源）
    "adx_range": 20.0,        # ADX < 此值判震荡
    "vol_pct": 0.80,          # atr_pct 滚动分位 >= 此值判高波动（覆盖态）
    "percentile_window": 90,  # 滚动分位窗口（bar 数，同快照 90 日口径）
    "min_segment_bars": 20,   # 短于此的段吸收进相邻较长段
    "max_segments": 12,       # 段数上限（超限最小段优先并入，合并不截断）
}


def adx_state(adx: Optional[float]) -> Optional[str]:
    """三态机械判读（自 market_snapshot._adx_state 提升，内容逐字不动；
    代码规则可复现，刻意不做智能判读）：<20 震荡 / >25 趋势 / 之间过渡。"""
    if adx is None:
        return None
    if adx < 20:
        return "震荡"
    if adx > 25:
        return "趋势"
    return "过渡"


def rolling_percentile(
    series: pd.Series, window: int = GATES["percentile_window"],
) -> pd.Series:
    """向量化滚动分位（与快照单点版 _window_percentile 数学同义：
    当前值在近 window 根中的 rank 位置——双实现测试互钉）。"""
    return series.rolling(window, min_periods=2).rank(pct=True)


def classify_series(
    df: pd.DataFrame, adx_col: str, atr_col: str,
) -> pd.Series:
    """逐 bar 判读状态序列（「高波动」覆盖态优先于方向三态）。

    Args:
        df: 含 ADX/ATR 指标列与 close 的 DataFrame（warmup 前缀由调用
            方以统计段切分后传入，或本层全序列判读由模板负责切回）。
        adx_col / atr_col: 指标列名。
    """
    atr_pct = df[atr_col] / df["close"] * 100.0
    vol_p = rolling_percentile(atr_pct)
    adx = df[adx_col]

    state = pd.Series("过渡", index=df.index, dtype="object")
    state[adx < GATES["adx_range"]] = "震荡"
    state[adx > GATES["adx_trend"]] = "趋势"
    # 覆盖态：波动分位达标优先（风险管理维度优先于方向分类）
    state[(vol_p >= GATES["vol_pct"]).fillna(False)] = "高波动"
    # 指标未就绪段（warmup NaN）判读为过渡——与 ADX None 语义一致
    state[adx.isna()] = "过渡"
    return state


def _absorb_short(segs: List[Dict[str, Any]], min_bars: int) -> List[Dict[str, Any]]:
    """短段吸收：短于 min_bars 的段迭代并入相邻较长段（保边界清晰）。"""
    segs = [dict(s) for s in segs]
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            if s["bars"] >= min_bars:
                continue
            # 并入相邻较长者（首尾只有单邻居）
            if i == 0:
                tgt = 1
            elif i == len(segs) - 1:
                tgt = i - 1
            else:
                tgt = i - 1 if segs[i - 1]["bars"] >= segs[i + 1]["bars"] else i + 1
            lo, hi = min(i, tgt), max(i, tgt)
            segs[lo] = {"start": segs[lo]["start"], "end": segs[hi]["end"],
                        "type": segs[tgt]["type"], "bars": sum(
                            x["bars"] for x in segs[lo:hi + 1])}
            del segs[lo + 1:hi + 1]
            changed = True
            break
    return segs


def build_segments(
    df: pd.DataFrame, adx_col: str, atr_col: str,
) -> List[Dict[str, Any]]:
    """切段主入口（确定性纯函数：同数据同参数段界逐字节一致）。

    流程：逐 bar 判读 → 同状态合并 → 短段吸收 → 段数超上限最小段优先
    并入（合并不截断）→ 段描述字段（bars / adx_mean / atr_pct_mean /
    atr_pct_pct_mean）。
    """
    state = classify_series(df, adx_col, atr_col)
    atr_pct = df[atr_col] / df["close"] * 100.0
    vol_p = rolling_percentile(atr_pct)

    # 位置驱动切段（无索引均匀性假设；bar 计数=位置差）
    idx = state.index.to_numpy()
    vals = state.to_numpy()
    raw: List[Dict[str, Any]] = []
    start_i = 0
    for i in range(1, len(vals) + 1):
        if i == len(vals) or vals[i] != vals[start_i]:
            raw.append({"start_i": start_i, "end_i": i, "type": str(vals[start_i])})
            start_i = i
    segs: List[Dict[str, Any]] = [
        {"start": idx[r["start_i"]], "end": idx[r["end_i"] - 1],
         "type": r["type"], "bars": r["end_i"] - r["start_i"]}
        for r in raw
    ]

    segs = _absorb_short(segs, GATES["min_segment_bars"])
    # 段数上限：最小段优先并入相邻较长段（合并不截断——信息变粗不丢失）
    while len(segs) > GATES["max_segments"]:
        smallest = min(range(len(segs)), key=lambda i: segs[i]["bars"])
        if smallest == 0:
            tgt = 1
        elif smallest == len(segs) - 1:
            tgt = smallest - 1
        else:
            tgt = (smallest - 1 if segs[smallest - 1]["bars"] >= segs[smallest + 1]["bars"]
                   else smallest + 1)
        lo, hi = min(smallest, tgt), max(smallest, tgt)
        segs[lo] = {"start": segs[lo]["start"], "end": segs[hi]["end"],
                    "type": segs[tgt]["type"], "bars": sum(
                        x["bars"] for x in segs[lo:hi + 1])}
        del segs[lo + 1:hi + 1]

    # 合并可能产生相邻同类型段（吸收后类型一致）——收尾再合并一次
    merged: List[Dict[str, Any]] = []
    for s in segs:
        if merged and merged[-1]["type"] == s["type"]:
            merged[-1]["end"] = s["end"]
            merged[-1]["bars"] += s["bars"]
        else:
            merged.append(s)

    # 段类型重定型：短段吸收/上限合并的类型继承链会使标签偏离段内
    # 实际多数状态（真实数据实证：逐 bar 趋势 42% 的年被切出 84% 震荡
    # 段）——统一按段内状态众数定型；对未经合并的纯 run 段为恒等操作
    for s in merged:
        sub = state[(state.index >= s["start"]) & (state.index <= s["end"])]
        if not sub.empty:
            s["type"] = str(sub.mode().iloc[0])

    # 段描述字段在全部合并完成后统一计算（合并重构段 dict，先算会丢）
    for s in merged:
        sl = df[(df.index >= s["start"]) & (df.index <= s["end"])]
        s["adx_mean"] = round(float(sl[adx_col].mean()), 2) if len(sl) else None
        s["atr_pct_mean"] = round(
            float((sl[atr_col] / sl["close"] * 100).mean()), 2) if len(sl) else None
        vp = vol_p[(vol_p.index >= s["start"]) & (vol_p.index <= s["end"])].dropna()
        s["atr_pct_pct_mean"] = round(float(vp.mean()), 2) if len(vp) else None
    return merged
