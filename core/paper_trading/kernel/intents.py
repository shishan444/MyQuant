"""信号解释器（SignalInterpreter——四类信号+闭合 bar → 动作意图）。

无状态纯函数类：interpret(closed_bar, signals_at_bar, account, params)
按七段判定序产出该 bar 闭合时的意图（每 bar 至多一条——判定序内
前者命中即返回）：

    清算强平→SL/TP（SL 优先 TP）→时间出场（time_exit_utc 先于
    max_hold，对齐回测引擎内序）→信号出场→入场→减仓→加仓

即时类（exit_liquidation/exit_sl/exit_tp）当根按触发价成交，其余
意图由执行器挂 pending 次根开盘执行（shift(1) 等价）。

错误语义：解释器不抛业务异常（纯映射）。atr 模式运行中单 bar ATR
值 NaN 或 ≤0 时该 bar 不产生 SL/TP 意图、不回退按 pct 距离计算
（排除 ATR 倍数被按 pct 误用的负价荒谬路径），次根恢复后防线恢复；
ATR 列整体缺失在装载校验层前置拦截（executor 侧）。
"""
from __future__ import annotations

from core.paper_trading.kernel.account import AccountSnapshot, PositionSnapshot
from core.paper_trading.kernel.ledger import liquidation_price
from core.paper_trading.kernel.types import (
    BarSignals,
    ClosedBar,
    Intent,
    RiskParams,
)


class SignalInterpreter:
    """信号解释器（无状态纯函数类——考场无观点的映射本体）。"""

    def interpret(self, closed_bar: ClosedBar, signals_at_bar: BarSignals,
                  account: AccountSnapshot,
                  params: RiskParams) -> list[Intent]:
        """单根闭合 bar 的意图产出（七段判定序）。

        Args:
            closed_bar: 已闭合 K 线（OHLC+序号+ATR+时间戳）。
            signals_at_bar: 四类信号在该 bar 的布尔值+方向符号。
            account: 只读账户快照（判定时点状态）。
            params: 物化后的风控参数。

        Returns:
            list[Intent]: 该 bar 产出的意图（至多一条；空列表=无动作）。
        """
        pos = account.position

        # ── 1. 清算强平（leverage=1 不判——无杠杆无清算） ──
        if pos is not None and params.leverage > 1:
            p_liq = liquidation_price(
                pos.side, pos.entry_price, pos.quantity, pos.margin,
                account.balance, closed_bar.close)
            hit = (closed_bar.low <= p_liq if pos.side == "long"
                   else closed_bar.high >= p_liq)
            if hit:
                return [Intent(
                    kind="exit_liquidation", direction=pos.side,
                    reason="liquidation", trigger_price=p_liq)]

        # ── 2. SL/TP（bar 内价格触发；SL 优先 TP） ──
        if pos is not None:
            exit_intent = self._stop_take_intent(closed_bar, pos, params)
            if exit_intent is not None:
                return [exit_intent]

        # ── 3. 时间出场（time_exit_utc 先于 max_hold——引擎内序） ──
        if pos is not None:
            time_intent = self._time_exit_intent(closed_bar, pos,
                                                 account.bars_held, params)
            if time_intent is not None:
                return [time_intent]

        # ── 4. 信号出场（exit 信号全平 / 反向 entry 两步反手） ──
        if pos is not None:
            if signals_at_bar.exit:
                return [Intent(kind="close", direction=pos.side,
                               reason="signal")]
            if signals_at_bar.entry:
                entry_side = self._resolve_entry_side(
                    signals_at_bar.entry_direction, params)
                if entry_side is not None and entry_side != pos.side:
                    return [Intent(kind="close", direction=pos.side,
                                   reason="reverse")]

        # ── 5. 入场（direction 过滤+mixed 符号 0/NaN 不开仓） ──
        if signals_at_bar.entry and pos is None:
            entry_side = self._resolve_entry_side(
                signals_at_bar.entry_direction, params)
            if entry_side is not None:
                return [Intent(
                    kind="open", direction=entry_side,
                    size_fraction=params.position_size, reason="signal")]

        # ── 6. 减仓（比例=exit_fraction；无仓忽略） ──
        if signals_at_bar.reduce and pos is not None:
            return [Intent(kind="reduce", direction=pos.side,
                           size_fraction=params.exit_fraction,
                           reason="signal")]

        # ── 7. 加仓（同向+max_exposure 敞口检查——超限跳过不炸） ──
        if signals_at_bar.add and pos is not None:
            add_side = self._resolve_entry_side(
                signals_at_bar.entry_direction, params)
            if add_side == pos.side:
                notional_now = pos.quantity * closed_bar.close
                add_notional = (account.equity * params.add_fraction
                                * params.leverage)
                if notional_now + add_notional \
                        <= account.equity * params.max_exposure:
                    return [Intent(kind="add", direction=pos.side,
                                   size_fraction=params.add_fraction,
                                   reason="signal")]

        return []

    # ── 防线判定（读 bar 的 high/low，非信号） ────────────────

    def _stop_take_intent(self, closed_bar: ClosedBar,
                          pos: PositionSnapshot,
                          params: RiskParams) -> Intent | None:
        """SL/TP 三模式触发价与触碰判定（SL 优先；ATR 无效守卫）。

        trail 峰值含当根 bar（多头 max(快照峰值, bar.high)/空头
        min(快照峰值, bar.low)——对齐引擎「峰值先更新再判定」形制）；
        TP 沿 pct 语义锚入场价（trail 与 TP 并存时，引擎同形）。
        """
        sl = params.stop_loss
        tp = params.take_profit
        is_long = pos.side == "long"
        atr = closed_bar.atr
        atr_mode = params.sl_mode == "atr"
        # ATR 守卫：atr 模式当根 ATR 无效（None/NaN/≤0）→ 该 bar 无
        # SL/TP 意图（不回退 pct 距离）
        atr_valid = atr is not None and atr == atr and atr > 0.0

        if atr_mode and not atr_valid:
            return None

        sl_level: float | None = None
        tp_level: float | None = None
        if atr_mode:
            mult = float(atr)
            sl_level = (pos.entry_price - sl * mult if is_long
                        else pos.entry_price + sl * mult) if sl > 0.0 \
                else None
            tp_level = (pos.entry_price + tp * mult if is_long
                        else pos.entry_price - tp * mult) \
                if tp is not None and tp > 0.0 else None
        elif params.sl_mode == "trail":
            if is_long:
                peak = max(pos.trail_peak, closed_bar.high)
                sl_level = peak * (1.0 - sl) if sl > 0.0 else None
                tp_level = (pos.entry_price * (1.0 + tp)
                            if tp is not None and tp > 0.0 else None)
            else:
                peak = min(pos.trail_peak, closed_bar.low)
                sl_level = peak * (1.0 + sl) if sl > 0.0 else None
                tp_level = (pos.entry_price * (1.0 - tp)
                            if tp is not None and tp > 0.0 else None)
        else:  # pct
            sl_level = (pos.entry_price * (1.0 - sl) if is_long
                        else pos.entry_price * (1.0 + sl)) if sl > 0.0 \
                else None
            tp_level = (pos.entry_price * (1.0 + tp) if is_long
                        else pos.entry_price * (1.0 - tp)) \
                if tp is not None and tp > 0.0 else None

        # SL 优先 TP（同 bar 并存——沿旧链 account.py:271 语义）
        if sl_level is not None:
            hit = (closed_bar.low <= sl_level if is_long
                   else closed_bar.high >= sl_level)
            if hit:
                return Intent(kind="exit_sl", direction=pos.side,
                              reason="sl", trigger_price=sl_level)
        if tp_level is not None:
            hit = (closed_bar.high >= tp_level if is_long
                   else closed_bar.low <= tp_level)
            if hit:
                return Intent(kind="exit_tp", direction=pos.side,
                              reason="tp", trigger_price=tp_level)
        return None

    def _time_exit_intent(self, closed_bar: ClosedBar,
                          pos: PositionSnapshot, bars_held: int,
                          params: RiskParams) -> Intent | None:
        """时间出场两机制（time_exit_utc 区间覆盖先、max_hold 到期后）。

        time_exit 覆盖判定对齐回测引擎形制：r=(t_exit−bar 开盘当日
        UTC 分钟) mod 1440 < tf_minutes（t_exit 落在本 bar 区间即触发
        ——日线及以上每 bar 覆盖全天恒触发，装载校验层拒绝该组合）。
        design §3.2 i 的「==」按字面会在 tf>1m 大量漏触发（与引擎
        分叉+「时刻必平」破坏），故按引擎等价语义实现（偏差上报）。
        max_hold：已持有根数≥N 全平（减仓不重置——计数锚=入场 bar）。
        """
        if params.time_exit_utc >= 0 and closed_bar.timestamp is not None:
            ts = closed_bar.timestamp
            minute_of_day = ts.hour * 60 + ts.minute  # UTC 语境
            r = (params.time_exit_utc - minute_of_day) % 1440
            if r < closed_bar.tf_minutes:
                return Intent(kind="exit_time", direction=pos.side,
                              reason="time_exit")
        if params.max_hold > 0 and bars_held >= params.max_hold:
            return Intent(kind="exit_max_hold", direction=pos.side,
                          reason="max_hold")
        return None

    # ── 方向解析（a 条 direction 过滤+mixed 符号） ────────────

    @staticmethod
    def _resolve_entry_side(entry_direction: float,
                            params: RiskParams) -> str | None:
        """入场方向解析（None=不开仓）。

        DNA direction 为 long/short 时用 DNA 值（声明是过滤器：信号
        方向与声明冲突不产生 open——与引擎 direction_val 语义一致）；
        mixed 时按 entry_direction 符号（0/NaN 不开仓）。
        """
        if params.direction == "long":
            return None if entry_direction < 0 else "long"
        if params.direction == "short":
            return None if entry_direction > 0 else "short"
        # mixed：方向来自信号符号
        if entry_direction > 0:
            return "long"
        if entry_direction < 0:
            return "short"
        return None
