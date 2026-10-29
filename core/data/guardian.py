"""数据保鲜守护器（DataFreshnessGuardian——应用级，与纸盘解耦）。

职能：服务启动即运行（app lifespan 装配，不依赖任何纸盘任务存在），
对固定订阅 12 键 K 线按独立节拍做尾戳检查与增量补拉（文件面更新），
对 funding/OI 数据按小时级节拍经既有 updater 单点（S3 归档增量）更新。

边界（add-data-freshness-guardian 契约 1，D-1/D-2）：
- 只写文件不碰 feed 内存序列——feed 靠自身 WS+对账保内存新鲜，纸盘
  停止后 feed 回收、下次 acquire 时 ensure_loaded 从 parquet 装载即得
  保鲜成果（内存=决策权威、parquet=持久副本的既有分工不变）；
- 不建 feed 实例、不进 WS 事件路由、不动「无订阅者停止拉取回收」
  条款；无文件键跳过留痕（初始建库归启动建库线程既有分工）；
- 依赖方向：guardian → core/data/fapi（rest/FIXED_SUBSCRIBE_KEYS）→
  core/data/updater → core/data/storage 单向——零纸盘域 import；
- 守护线程永不因单键/单轮异常退出（失败留痕续轮）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from core.data.fapi.rest import RUNTIME_BACKFILL_MAX_BARS
from core.data.storage import get_latest_timestamp, merge_closed_rows
from core.data.timeframes import TF_SECONDS

logger = logging.getLogger(__name__)

# ── 守护节拍（裁决 I/R-01；spec 锁语义不锁数值——沿 stream.py:45 先例）──
# K 线面 5 分钟：无任务时的兜底慢通道（有任务时 feed 对账 20s 主通道）；
# derivs 面（funding/OI）1 小时：S3 归档增量合并（幂等，与 feed 内
# funding 刷新并行无害——同一 parquet 经互斥锁+merge 去重协调）。
KLINE_GUARD_INTERVAL_SECONDS = 300.0
DERIVS_GUARD_INTERVAL_SECONDS = 3600.0

# 未知周期兜底（沿 feed._DEFAULT_BAR_SECONDS 形制）
_DEFAULT_BAR_SECONDS = 14400


class DataFreshnessGuardian:
    """数据保鲜守护器（进程内单守护线程 "data-guardian"）。

    生命周期与测试通道：start_background()/stop()/status() 管线程；
    guard_kline_once()/guard_derivs_once() 两单轮入口同步直调（生产
    守护线程与测试直调同路径——沿 FixedKeyBackfill.run_once 先例）。
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        rest_client: Optional[object] = None,
        funding_refresher: Optional[Callable[..., pd.DataFrame]] = None,
        oi_refresher: Optional[Callable[..., dict]] = None,
        now_fn: Optional[Callable[[], pd.Timestamp]] = None,
    ):
        """初始化守护器（不自启——start_background() 管生命周期）。

        Args:
            data_dir: parquet 数据目录（fapi 独立数据面）。
            rest_client: K 线补拉客户端（缺省延迟构造 FapiRestClient
                ——沿 feed.py _default_rest_fetcher 先例；测试注入
                ScriptedRestClient）。
            funding_refresher: 资金费刷新单点（缺省转
                update_funding_data——显式传 symbol/data_dir）。
            oi_refresher: 持仓量刷新单点（缺省转
                update_open_interest_data——显式传 symbol/data_dir）。
            now_fn: 当前时刻（测试注入固定时钟）。
        """
        self.data_dir = Path(data_dir)
        self._rest_client = rest_client
        self._funding_refresher = funding_refresher
        self._oi_refresher = oi_refresher
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 状态面（status() 输入；六态键算法与 derivs 面结果驻留）
        self._kline_last_run: Optional[pd.Timestamp] = None
        self._kline_keys: dict[str, str] = {}
        self._derivs_last_run: Optional[pd.Timestamp] = None
        self._derivs_symbols: dict[str, dict[str, str]] = {}

    # ── 依赖单点（延迟构造——保持模块顶层零 SDK 依赖面）────────

    def _rest(self):
        """K 线补拉客户端惰性单例（缺省 FapiRestClient）。"""
        if self._rest_client is None:
            from core.data.fapi.rest import FapiRestClient

            self._rest_client = FapiRestClient()
        return self._rest_client

    @staticmethod
    def _default_funding_refresher(
        symbol: str, data_dir: Path,
    ) -> pd.DataFrame:
        """资金费刷新缺省单点（updater.update_funding_data——显式
        data_dir 防 updater 默认值耦合；幂等增量）。"""
        from core.data.updater import update_funding_data

        return update_funding_data(symbol=symbol, data_dir=data_dir)

    @staticmethod
    def _default_oi_refresher(symbol: str, data_dir: Path) -> dict:
        """持仓量刷新缺省单点（updater.update_open_interest_data）。"""
        from core.data.updater import update_open_interest_data

        return update_open_interest_data(symbol=symbol, data_dir=data_dir)

    # ── 生命周期 ────────────────────────────────────────────

    def start_background(self) -> None:
        """起守护线程（幂等——已启动 no-op；首轮 last_run=None 视为
        到期即扫，「服务启动即运行」语义）。"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop, name="data-guardian", daemon=True)
        self._thread.start()

    def _background_loop(self) -> None:
        """单线程双计时：泊等 1s 检查两计时器；last_run=None 视为到期
        （首轮启动即扫，不空等首个间隔）。单轮异常记日志续轮。"""
        while not self._stop_event.wait(1.0):
            now = self._now_fn()
            try:
                if self._timer_due(now, self._kline_last_run,
                                   KLINE_GUARD_INTERVAL_SECONDS):
                    self.guard_kline_once()
            except Exception:
                logger.exception("守护器 K 线单轮异常（续轮）")
            try:
                if self._timer_due(now, self._derivs_last_run,
                                   DERIVS_GUARD_INTERVAL_SECONDS):
                    self.guard_derivs_once()
            except Exception:
                logger.exception("守护器 derivs 单轮异常（续轮）")

    @staticmethod
    def _timer_due(
        now: pd.Timestamp,
        last_run: Optional[pd.Timestamp],
        interval_seconds: float,
    ) -> bool:
        """计时器到期判定（last_run=None 视为到期——首轮即扫）。"""
        if last_run is None:
            return True
        return (now - last_run).total_seconds() >= interval_seconds

    def stop(self) -> None:
        """协作停止（app 关停序——守护器停于 feed flush 前）。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)

    def status(self) -> dict:
        """状态面（runner-status guardian 健康组输入）。

        Returns:
            {"kline": {"last_run": iso|None, "keys": {键: 六态结果}},
             "derivs": {"last_run": iso|None,
                        "{SYMBOL}": {"funding": 结果, "oi": 结果}}}
        """
        with self._lock:
            return {
                "kline": {
                    "last_run": (self._kline_last_run.isoformat()
                                 if self._kline_last_run is not None
                                 else None),
                    "keys": dict(self._kline_keys),
                },
                "derivs": {
                    "last_run": (self._derivs_last_run.isoformat()
                                 if self._derivs_last_run is not None
                                 else None),
                    "symbols": {
                        symbol: dict(faces)
                        for symbol, faces in self._derivs_symbols.items()},
                },
            }

    # ── K 线单轮（六态键算法）──────────────────────────────

    def guard_kline_once(self) -> dict[str, str]:
        """K 线保鲜单轮：逐键尾戳检查+增量补拉（同步直调测试通道）。

        六态结果：fresh（尾戳新鲜跳过）| ok（缺口成立但端点空响应）|
        filled:N（增量落盘 N 根）| skipped_no_file（无文件——初始建库
        归启动建库线程）| skipped_corrupt（读尾戳异常——半写窗口，损坏
        重建归 feed 层既有路径，守护器不自愈只保鲜）| failed（REST 异常
        ——单键捕获续键）。任一异常不中断同轮其余键。
        """
        from core.data.fapi.stream import FIXED_SUBSCRIBE_KEYS

        results: dict[str, str] = {}
        for symbol, timeframe in sorted(FIXED_SUBSCRIBE_KEYS):
            key = f"{symbol}_{timeframe}"
            results[key] = self._guard_kline_key(symbol, timeframe)
        with self._lock:
            self._kline_last_run = self._now_fn()
            self._kline_keys = results
        return results

    def _guard_kline_key(self, symbol: str, timeframe: str) -> str:
        """单键六态算法（路径全捕获——单键异常不外泄续键）。"""
        path = self.data_dir / f"{symbol}_{timeframe}.parquet"
        try:
            last = get_latest_timestamp(path)
        except Exception:
            # 文件损坏半写窗口：留痕续键（守护器不自愈只保鲜——损坏
            # 重建归 feed 层既有路径）
            logger.warning("守护器读尾戳异常（skipped_corrupt）：%s", path)
            return "skipped_corrupt"
        if last is None:
            # 无文件：初始建库归 FixedKeyBackfill 既有分工（与 feed
            # 「无基线键跳过」同构——D-1 伴随裁定）
            return "skipped_no_file"
        interval = TF_SECONDS.get(timeframe, _DEFAULT_BAR_SECONDS)
        now = self._now_fn()
        if now < last + pd.Timedelta(seconds=2 * interval):
            return "fresh"  # 尾戳新鲜（沿 backfill_key 同形判定）
        gap_start = last + pd.Timedelta(seconds=interval)
        try:
            frame = self._rest().fetch_closed_bars(
                symbol, timeframe, gap_start, now,
                max_bars=RUNTIME_BACKFILL_MAX_BARS)
        except Exception:
            logger.exception("守护器补拉失败（续键）：%s_%s",
                             symbol, timeframe)
            return "failed"
        if frame is None or frame.empty:
            return "ok"  # 端点暂无新数据（窗口内无可得闭合 bar）
        merge_closed_rows(path, frame)  # 闭合行合并单点（keep=first）
        return f"filled:{len(frame)}"

    # ── funding/OI 单轮 ────────────────────────────────────

    def guard_derivs_once(self) -> dict[str, dict[str, str]]:
        """derivs 保鲜单轮：对 FAPI_SYMBOLS 两币种逐个调 funding/OI
        refresher（显式传 symbol 与 data_dir）。单 symbol 异常捕获记
        failed 续行（不中断同轮另一币种）。"""
        from core.data.fapi.stream import FAPI_SYMBOLS

        funding_fn = self._funding_refresher or \
            self._default_funding_refresher
        oi_fn = self._oi_refresher or self._default_oi_refresher
        results: dict[str, dict[str, str]] = {}
        for symbol in FAPI_SYMBOLS:
            faces: dict[str, str] = {}
            for face, refresher in (
                ("funding", funding_fn), ("oi", oi_fn),
            ):
                try:
                    refresher(symbol=symbol, data_dir=self.data_dir)
                    faces[face] = "ok"
                except Exception:
                    logger.exception(
                        "守护器 derivs 刷新失败（续行）：%s/%s",
                        symbol, face)
                    faces[face] = "failed"
            results[symbol] = faces
        with self._lock:
            self._derivs_last_run = self._now_fn()
            self._derivs_symbols = results
        return results
