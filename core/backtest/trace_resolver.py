"""验证信号留痕解析器（add-vr-signal-trace 组 4——纯函数无 IO）。

职责（设计卡 §2.7）：引擎事件码矩阵 trace_buf → vr_signal_trace 行
列表。五步：①非零格→(bar_time, event_code) 行②reason 单源映射——
未映射 event_code → raise ValueError（fail-fast：静默过滤会重新打开
三布尔刚封死的「有标记而数据空」洞）③capped 推导（重放先例
_position_sizing_disclosure：实际 Size÷期望下单量，比值∈(0,1)——
Q2 裁决 2026-11-04）④swallowed 行合并+seq 分配（引擎行先求值层后、
层内 event_code 升序、0 起）⑤幂等键族排序去重。

outcome 判定（枚举三值 filled/rejected/swallowed——吞与拒不混）：
- 蓝族 10-13/金族 20-25（引擎发单码）：与 vbt trades 对照实测——
  该 bar 有对应成交→filled+size/price 回填；无→rejected（reason
  NULL——vbt 拒单原因黑箱不可得，如实缺失不捏造）
- 32（risk_budget 降级回退发单）：Q1 裁决（方案 A·对照实测）——该
  bar 有成交→filled+reason=risk_budget_fallback+size/price（路径
  披露）；无成交→rejected+reason=risk_budget_fallback
- 30/31（决策层拒绝未发单）→rejected+专属 reason
- 33/34（求值层清洗未达引擎）→swallowed+专属 reason

trail_buf 不入本模块——trail_series 已由 engine.run 单源产出
（result.trail_series），重算是被禁止的漂移源（需求 D2）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ── 事件码分配表（单源，值域稳定性=后端契约承诺 D4）──
# 蓝族=策略信号动作（filled 语义）/金族=风控出场族（filled 语义）/
# 灰族=未遂（rejected·决策层两值+fallback·对照实测+swallowed·求值层
# 两值）。0=无事件。引擎写入点（engine.py order_func_nb 决策分支）
# 的事件码枚举 MUST ⊆ 本映射键集——CI 结构层首拦（tests 锚）+
# raise 产线末防双锚。
EVENT_REASON_MAP: Dict[int, Dict[str, Optional[str]]] = {
    10: {"outcome": "filled", "reason": None},          # 入场
    11: {"outcome": "filled", "reason": None},          # 加仓
    12: {"outcome": "filled", "reason": None},          # 减仓
    13: {"outcome": "filled", "reason": None},          # 信号离场
    20: {"outcome": "filled", "reason": None},          # SL
    21: {"outcome": "filled", "reason": None},          # TP
    22: {"outcome": "filled", "reason": None},          # 清算强平
    23: {"outcome": "filled", "reason": None},          # 金额防线
    24: {"outcome": "filled", "reason": None},          # 固定时刻
    25: {"outcome": "filled", "reason": None},          # max_hold
    30: {"outcome": "rejected", "reason": "exposure_cap"},
    31: {"outcome": "rejected", "reason": "insufficient_funds"},
    32: {"outcome": "filled_or_rejected", "reason": "risk_budget_fallback"},
    33: {"outcome": "swallowed", "reason": "entry_exit_conflict"},
    34: {"outcome": "swallowed", "reason": "add_inhibited"},
}

# 对照分派：entry 类码查 trade 行 Entry Timestamp、exit 类码查
# Exit Timestamp（vbt records_readable 配对记录——加仓/减仓各成
# 独立配对行，两类时间戳各自可对齐执行 bar）
_ENTRY_CODES = frozenset({10, 11})
_EXIT_CODES = frozenset({12, 13, 20, 21, 22, 23, 24, 25})
_UNMAPPED_PLACEHOLDER = -1  # 求值层行 event_code（swallowed 无引擎码）


def _expected_qty(risk_params: Dict[str, Any], price: float,
                  value_now: float) -> Optional[float]:
    """期望下单量重放（与 _position_sizing_disclosure 同款对齐假设）。

    value_now=订单 bar 前一 bar 期末组合价值（首 bar 退化 init_cash
    ——调用方传入的 value_curve 已含）；stop_dist 取 pct/trail 档
    price×sl_stop（比例域）——atr 档需当根 ATR 列，本模块无装载
    通道，置 None（不可比——表列契约「未截断/不可比 NULL」合法态，
    主要截断面〔max_exposure cap〕集中在 pct/trail 档）。
    """
    if price <= 0.0 or value_now <= 0.0:
        return None
    mode = risk_params.get("position_mode") or "fixed"
    if mode == "risk_budget":
        rf = risk_params.get("risk_fraction")
        sl_stop = risk_params.get("sl_stop") or 0.0
        if rf is None or sl_stop <= 0.0:
            return None
        if risk_params.get("sl_mode") == "atr":
            return None  # ATR 距离不可得——如实不可比
        qty = value_now * float(rf) / (price * sl_stop)
    else:
        size_pct = risk_params.get("size_pct")
        if size_pct is None or size_pct <= 0.0:
            return None
        qty = value_now * float(size_pct) / price
    return qty


def resolve_signal_trace(
    trace_buf: Optional[np.ndarray],
    bar_index: pd.DatetimeIndex,
    swallowed_rows: Optional[List[Dict[str, Any]]],
    trades_df: Optional[pd.DataFrame],
    *,
    exec_ref: str = "main",
    init_cash: float = 100000.0,
    risk_params: Optional[Dict[str, Any]] = None,
    value_curve: Optional[pd.Series] = None,
) -> List[Dict[str, Any]]:
    """事件码矩阵+吞没行+成交对照 → vr_signal_trace 行列表。

    Args:
        trace_buf: 引擎缓冲矩阵（n_bars×n_cols，0.0=无事件；None=
            空矩阵等价——零信号记录三布尔仍打 1 的管线通态）。
        bar_index: 执行轴（trace_buf 行轴对齐 DatetimeIndex）。
        swallowed_rows: SignalSet.swallowed（[{bar_time, reason}]，
            None/[] 无行）。
        trades_df: vbt trades.records_readable（None/空=零成交——
            对照实测下全部引擎发单码行 outcome=rejected）。
        exec_ref: 份标识（错误消息定位上下文+行携带）。
        init_cash: 初始资金（value_curve 缺失时的重放退化值）。
        risk_params: 仓位口径参数（position_mode/size_pct/sl_mode/
            sl_stop/risk_fraction——capped 重放）。
        value_curve: 原始组合价值曲线（放大/资金费前——value_now
            单源；None 退化 init_cash 常数）。

    Returns:
        行 dict 列表（exec_ref/bar_time/seq/outcome/reason/event_code/
        size/price/capped；record_id 由落库层补；按 bar_time+seq 升序）。
    """
    risk_params = risk_params or {}
    value_arr = (
        value_curve.to_numpy(dtype=np.float64)
        if value_curve is not None else None)

    def _value_at(pos: int) -> float:
        if value_arr is None:
            return float(init_cash)
        if pos <= 0:
            return float(value_arr[0])
        return float(value_arr[pos - 1])

    # ① 非零格 → (pos, event_code) 引擎行
    engine_events: List[Dict[str, Any]] = []
    if trace_buf is not None and trace_buf.size:
        nz = np.argwhere(trace_buf != 0.0)
        for row_pos, _col in nz:
            pos = int(row_pos)
            code = int(trace_buf[pos, int(_col)])
            mapping = EVENT_REASON_MAP.get(code)
            if mapping is None:
                # ② fail-fast：未映射码禁止静默丢弃禁止直显（spec
                # 「未遂原因枚举稳定」——静默过滤=重新打开「有标记而
                # 数据空」洞；消息含码值/bar/exec_ref 定位上下文）
                raise ValueError(
                    f"trace_resolver 未映射事件码 {code}: "
                    f"bar={bar_index[pos]}, exec_ref={exec_ref!r} ——"
                    "事件码分配表与引擎写入点漂移，演进须同步"
                    "EVENT_REASON_MAP（值域变更=公共契约变更走 spec 修订）")
            engine_events.append({
                "pos": pos,
                "bar_time": str(bar_index[pos]),
                "event_code": code,
                "layer": 0,  # 引擎行先于求值层（seq 分配规则）
                **mapping,
            })

    # ③ 成交对照实测（filled 语义的 outcome 判定+size/price 回填）
    entry_trades: Dict[str, List[Dict[str, Any]]] = {}
    exit_trades: Dict[str, List[Dict[str, Any]]] = {}
    if trades_df is not None and len(trades_df) > 0:
        for k in range(len(trades_df)):
            row = trades_df.iloc[k]
            et = row["Entry Timestamp"]
            xt = row["Exit Timestamp"]
            payload = {
                "size": abs(float(row["Size"])),
                "entry_price": float(row["Avg Entry Price"]),
                "exit_price": float(row["Avg Exit Price"]),
            }
            if et is not None and not pd.isna(et):
                entry_trades.setdefault(str(et), []).append(payload)
            if xt is not None and not pd.isna(xt):
                exit_trades.setdefault(str(xt), []).append(payload)

    consumed: set = set()  # (桶键, 行内序) 已消费防同 bar 重复回填

    def _match(bar_time: str, is_entry: bool):
        bucket = entry_trades if is_entry else exit_trades
        rows = bucket.get(bar_time) or []
        for j, tr in enumerate(rows):
            key = (bar_time, "e" if is_entry else "x", j)
            if key not in consumed:
                consumed.add(key)
                return tr
        return None

    for ev in engine_events:
        code = ev["event_code"]
        if code in (30, 31):
            continue  # 决策层拒绝未发单——无对照面
        if code == 32:
            # Q1 对照实测（方案 A·2026-11-04 裁决）：回退发单后该 bar
            # 有成交→filled+reason 披露降级路径；无→rejected 同 reason
            tr = _match(ev["bar_time"], is_entry=True)
            ev["outcome"] = "filled" if tr is not None else "rejected"
            if tr is not None:
                ev["size"], ev["price"] = tr["size"], tr["entry_price"]
            continue
        # 蓝族/金族：对照实测
        is_entry = code in _ENTRY_CODES
        tr = _match(ev["bar_time"], is_entry=is_entry)
        ev["outcome"] = "filled" if tr is not None else "rejected"
        if tr is not None:
            ev["size"] = tr["size"]
            ev["price"] = (tr["entry_price"] if is_entry
                           else tr["exit_price"])

    # ④ swallowed 行合并（层序 1——引擎行先求值层后）
    for sw in (swallowed_rows or []):
        reason = sw.get("reason")
        code = {v["reason"]: k for k, v in EVENT_REASON_MAP.items()}.get(
            reason)
        if code is None:
            raise ValueError(
                f"trace_resolver 未映射吞没原因 {reason!r}: "
                f"bar={sw.get('bar_time')}, exec_ref={exec_ref!r}")
        engine_events.append({
            "pos": None,
            "bar_time": str(sw["bar_time"]),
            "event_code": code,
            "layer": 1,
            "outcome": "swallowed",
            "reason": reason,
        })

    # 排序（bar_time 升序→层序→event_code 升序）+seq 分配（同 bar 内 0 起）
    order = {}
    for i, ts in enumerate(bar_index):
        order[str(ts)] = i
    engine_events.sort(key=lambda e: (
        order.get(e["bar_time"], 1 << 30), e["layer"], e["event_code"]))

    # ⑤ 幂等键族去重（(bar_time, layer, event_code) 全同防御）+行成形
    out: List[Dict[str, Any]] = []
    seen: set = set()
    seq_counter: Dict[str, int] = {}
    for ev in engine_events:
        key = (ev["bar_time"], ev["layer"], ev["event_code"])
        if key in seen:
            continue
        seen.add(key)
        seq = seq_counter.get(ev["bar_time"], 0)
        seq_counter[ev["bar_time"]] = seq + 1
        # capped 推导：filled 的 entry 类行（10/11/32 成交态）才有
        # 「计划 vs 实际」可比面
        capped = None
        if (ev.get("outcome") == "filled" and ev["event_code"] in (10, 11, 32)
                and ev.get("size") is not None and ev.get("price")):
            pos = ev["pos"]
            value_now = _value_at(pos) if pos is not None else float(init_cash)
            plan = _expected_qty(risk_params, ev["price"], value_now)
            if plan is not None and plan > 0.0:
                ratio = ev["size"] / plan
                if 0.0 < ratio < 1.0:
                    capped = float(ratio)
        out.append({
            "exec_ref": exec_ref,
            "bar_time": ev["bar_time"],
            "seq": seq,
            "outcome": ev["outcome"],
            "reason": ev.get("reason"),
            "event_code": ev["event_code"],
            "size": ev.get("size"),
            "price": ev.get("price"),
            "capped": capped,
            "gene_hits": None,  # 验证链 with_hits 未开——无命中信息 NULL
        })
    return out


def engine_written_event_codes() -> frozenset:
    """引擎写入点事件码枚举（分配表全集——映射表完整性断言消费）。

    tests 锚（T1-U1 强档）：本集 ⊆ EVENT_REASON_MAP 键集——静态结构
    断言，引擎新增写入码而映射表未同步时 CI 层首拦。
    """
    return frozenset(EVENT_REASON_MAP.keys())
