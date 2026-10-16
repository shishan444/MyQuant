"""纸盘交易常量单源（全部数值锚——改值改锚同步，spec 同值写死）。

消费方：调度器（并发上限/轮询节奏）、报告器（最低样本线）、执行器
与接口层（费滑与窗口缺省）。环境变量覆盖仅 MAX_RUNNING_TASKS 一项
（部署侧并发调优面），形制沿 core/data/fetcher.py 的 BINANCE_PROXY
先例（import 时读取——测试覆盖经 monkeypatch+reload 表达）。
"""
from __future__ import annotations

import os

# 运行任务数上限（超限任务停留 pending 按 created_at 顺序排队拾取）
MAX_RUNNING_TASKS = int(os.environ.get("PAPER_MAX_RUNNING_TASKS", "10"))

# 调度器守护循环轮询间隔（秒）
POLL_INTERVAL_SECONDS = 2.0

# 最低样本线（证据强度层双阈值，design §6.5 定值依据：
# 60=缺省窗口 180 根的三分之一；5=工程验证协议机械门槛同值）
PAPER_MIN_BARS = 60
PAPER_MIN_TRADES = 5

# 任务费滑缺省（任务覆写面缺省值，与回测族缺省同值）
DEFAULT_FEE = 0.001
DEFAULT_SLIPPAGE = 0.0005

# 验证窗口缺省（根 K 线；显式 null=不限期不自动结题，由表字段表达）
DEFAULT_WINDOW_BARS = 180
