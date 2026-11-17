"""纸盘交易常量单源（全部数值锚——改值改锚同步，spec 同值写死）。

消费方：调度器（并发上限/轮询节奏）、报告器（最低样本线）、执行器
与接口层（费滑与窗口缺省）。环境变量覆盖仅 MAX_RUNNING_TASKS 一项
（部署侧并发调优面），形制沿 core/data/fetcher.py 的 BINANCE_PROXY
先例（import 时读取——测试覆盖经 monkeypatch+reload 表达）。
"""
from __future__ import annotations

import os

# 运行任务数上限（超限任务停留 pending 按 created_at 顺序排队拾取；
# 缺省 100=change paper-mount-and-concurrency 护栏上调〔2026-10-28
# 用户裁决〕——spec「上限排队语义（质询闭环修订）」同值写死，改值改
# 锚同步）
MAX_RUNNING_TASKS = int(os.environ.get("PAPER_MAX_RUNNING_TASKS", "100"))

# 调度器守护循环轮询间隔（秒）——拾取/心跳/回补族节拍（对账节拍已
# 独立为 RECONCILE_INTERVAL_SECONDS；同名类属性
# FixedKeyBackfill.POLL_INTERVAL_SECONDS 是建库线程 60s 重试轮询，
# 两者同名不同物）
POLL_INTERVAL_SECONDS = 2.0

# 对账守护循环节拍（秒）——独立于拾取节拍（add-data-freshness-
# guardian R-02/裁决 A）：驱动活跃 feed 的 reconcile（缺口判定→
# REST 补拉）；与拾取节拍由同一守护线程双计时分频执行（不新增
# 第二调度线程）。窗口上限 RUNTIME_BACKFILL_MAX_BARS 下 60 根
# 1m 缺口≈6 轮×20s≈2 分钟收敛（R-03 数学）
RECONCILE_INTERVAL_SECONDS = 20.0

# 最低样本线（证据强度层双阈值，design §6.5 定值依据：
# 60=缺省窗口 180 根的三分之一；5=工程验证协议机械门槛同值）
PAPER_MIN_BARS = 60
PAPER_MIN_TRADES = 5

# 任务费滑缺省（任务覆写面缺省值，与回测族缺省同值）
DEFAULT_FEE = 0.001
DEFAULT_SLIPPAGE = 0.0005

# 验证窗口缺省（根 K 线；显式 null=不限期不自动结题，由表字段表达）
DEFAULT_WINDOW_BARS = 180

# ── 资金枯竭终态（change paper-depletion-terminal）──────────────
# 枯竭计数阈值（累计 N 次 below_min_notional 开仓拒绝且空仓→
# stopped+stop_reason="depleted"；spec 写死同值——机制不可配置）
DEPLETED_STREAK_N = 3

# ── 减仓终止条款（change add-replay-engine）─────────────────────
# 经济锚权重：剩余仓位继续持有的费滑损耗相对账户不再经济的权益占比
# （终局口径：经济阈值=max(权益×本系数, 交易所最小成交名义)——两锚
# 取大；物理条件=本次减仓量名义<最小成交名义。任一命中→该次减仓升格
# 全额平仓 reason=reduce_terminated，平台风控层保护性出清）
REDUCE_TERMINATE_EQUITY_FRACTION = 0.05

# MIN_NOTIONAL 内置回退表（端点拉取失败时兜底；env 覆写优先——
# 装载链见 core/paper_trading/min_notional.py）。
# 核实记录（禁凭记忆写值——实施期真实核实留档）：
# - 渠道：https://www.binance.com/fapi/v1/exchangeInfo（生产 fapi 的
#   www 域名别名——本机直连 fapi.binance.com 受地域限制 451，www
#   别名可达；响应 U_MARGINED 936 symbols，serverTime=1793215948248，
#   2026-10-28 拉取）
# - 取值：BTCUSDT PERPETUAL(TRADING) filters MIN_NOTIONAL.notional='50'；
#   ETHUSDT PERPETUAL(TRADING) notional='20'
# - 交叉验证：testnet.binancefuture.com/fapi/v1/exchangeInfo 同值
#   （50/20——两渠道互证；运行中不刷新，重启即更新）
MIN_NOTIONAL_BUILTIN: dict[str, float] = {"BTCUSDT": 50.0, "ETHUSDT": 20.0}

# ── 挂载品种与周期双白名单（change paper-mount-and-concurrency；
#    周期面固定六档=用户裁决 2026-10-28）───────────────────────
# 值域与 change-1 固定订阅清单同源对齐（core/data/fapi/stream.py
# FAPI_SYMBOLS/FAPI_TIMEFRAMES——12 路=2 币种×6 周期）：两处各自
# 单源、同值域，一致性由 test_api_paper 断言锚定（防漂移；订阅扩容
# 时纸盘是否跟随是独立决策，不 import 派生）。收窄仅表达为挂载/装载
# 层能力边界——validator 的 DNA 周期值域（TF_SECONDS 14 键）不收窄，
# 超集周期策略可存在可回测仅不可挂盘；研究数据面不受此约束。
PAPER_SYMBOL_WHITELIST: frozenset[str] = frozenset(
    {"BTCUSDT", "ETHUSDT"})
PAPER_TIMEFRAME_WHITELIST: frozenset[str] = frozenset(
    {"1m", "5m", "15m", "1h", "4h", "1d"})
