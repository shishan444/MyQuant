"""回放喂帧器（ReplayFeeder）——历史供数外壳。

回放侧供数部件（change add-replay-engine 任务 2.1）：装载链（主/
辅周期 warmup 前扩装载+全帧指标+ATR 补列+funding 归档索引）与分段
迭代（辅帧可见集按「收盘时刻 ≤ 当前主 bar 收盘时刻（含等号）」推进
——判定可见性契约，回放时序纪律 spec 场景锚）。与 runtime/feed.py
（实时供数）责任独立：数据源（parquet 历史 vs 实时行情）、时钟
（区间驱动 vs 线程调度）、生命周期（单次装载 vs 常驻刷新）三不同；
本包不订阅真实 feed、不做网络 IO。

命名辨析：runtime/scheduler.py 的 _replay_task_end/_replay_paper_
strategies 是策略生命周期回流（paper→active 态迁移），与本包无关。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd

from core.analysis.registry_meta import derive_warmup, derive_warmup_full
from core.analysis.validation_service import (
    dna_indicator_names,
    gene_tf_axis_refs,
)
from core.analysis.warmup_loader import TF_MINUTES, load_with_warmup
from core.data.mtf_loader import find_parquet
from core.data.storage import load_parquet
from core.paper_trading.runtime.feed import snap_to_funding_grid


class ReplayFeeder:
    """历史喂帧器：装载链 6-10 步 + 分段迭代 + funding 索引。

    供数契约五件（设计卡 D6）：主周期全量帧+from_idx 喂帧形制、辅
    周期帧切片按收盘锚刷新引用、funding 查询与实时 feed 同签名同
    语义、atr 档策略帧含 atr_{period} 列、MIN_NOTIONAL 经 runner
    装载链第 11 步（本件不管——只读模块级表现状，不发网络拉取）。
    """

    def __init__(
        self,
        data_dir: Path,
        db_path: Path,
        dna,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> None:
        """初始化（装载延迟到 load()——失败即显式拒绝，行不建纪律）。

        Args:
            data_dir: parquet 数据目录（与 FeedRegistry 同源）。
            db_path: 研究库（插件 warmup 闭包推导经注册表查库）。
            dna: 策略 DNA（已过 validate_dna+trading 兼容校验）。
            symbol: 主周期交易对（从 DNA execution_genes 抄录）。
            timeframe: 主周期（DNA execution_genes.timeframe）。
            start/end: 回放区间（naive 按 UTC 解释，闭端过滤）。
        """
        self.data_dir = Path(data_dir)
        self.db_path = Path(db_path)
        self.dna = dna
        self.symbol = symbol
        self.timeframe = timeframe
        self.start = start
        self.end = end
        self._main: Optional[pd.DataFrame] = None
        self._main_start: Optional[pd.Timestamp] = None
        self._aux: Dict[str, pd.DataFrame] = {}
        self._funding_index: Dict[pd.Timestamp, float] = {}

    # ── 装载链（6-10 步；1-5 步归 ReplayRunner）──

    def load(self) -> None:
        """执行装载链 6-10（失败抛 ResearchError 语义错误即拒）。"""
        indicator_names = dna_indicator_names(self.dna)
        axis_refs = gene_tf_axis_refs(self.dna)
        # 主轴四维预算（内置基准轴/高周期轴折算/插件闭包/分钟维——与
        # 隔离回测 _derive_dna_warmup 同源同值：公共入口同参组装）
        base_warmup = derive_warmup_full(
            self.db_path, indicator_names,
            indicator_tfs=axis_refs or None, base_tf=self.timeframe)
        # 6. 主周期前扩装载（min_warmup_bars 承载四维预算——
        # load_with_warmup 内部取 max(内置推导, min_warmup_bars)，
        # 四维 ≥ 内置维故结果=四维预算；前缀不足显式拒绝）
        main_df, self._main_start = load_with_warmup(
            self.data_dir, self.symbol, self.timeframe,
            self.start, self.end,
            indicator_names, min_warmup_bars=base_warmup)
        from core.features.indicators import compute_all_indicators

        # 8. 主帧指标（全帧向量化一次；衍生品列运行期注入归
        # process_frames——本装载链不注入，与实时态同构）
        self._main = compute_all_indicators(main_df)
        # 9. atr 档补列（沿 executor.prepare 形制）
        self._main = self._ensure_atr(self._main)
        # 7. 辅周期各自前扩+指标（轴引用指标集独立折算，含 200 floor）
        for tf in self.dna.timeframes:
            if tf == self.timeframe:
                continue
            aux_names = sorted({name for name, _ in axis_refs.get(tf, [])})
            aux_df, _ = load_with_warmup(
                self.data_dir, self.symbol, tf,
                self.start, self.end,
                aux_names, min_warmup_bars=derive_warmup(aux_names))
            self._aux[tf] = compute_all_indicators(aux_df)
        # 10. funding 归档索引（归档缺失=空索引非错误——运行期结算
        # 点全 pending 挂起，与实时态 feed 缺值同语义）
        self._load_funding_index()

    def _ensure_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """atr 档策略补 atr_{period} 列（列在则直读——沿 prepare 形制）。"""
        sl_mode = getattr(self.dna.risk_genes, "sl_mode", "pct")
        if sl_mode != "atr":
            return df
        period = int(getattr(self.dna.risk_genes, "atr_period", 14))
        col = f"atr_{period}"
        if col in df.columns and df[col].notna().any():
            return df
        from core.features.indicators import _compute_indicator

        computed = _compute_indicator(df, "ATR", {"period": period})
        series = computed.get(col)
        if series is None or not pd.Series(series).notna().any():
            raise ValueError(f"atr_missing: ATR({period}) 列缺失且现算不可得")
        df = df.copy()
        df[col] = series
        return df

    def _load_funding_index(self) -> None:
        """{symbol}_funding.parquet → 8h 网格索引（与 feed 同归一式）。"""
        path = self.data_dir / f"{self.symbol}_funding.parquet"
        if not path.exists():
            return  # 空索引=全挂起（非错误）
        frame = load_parquet(path)
        if frame is None or frame.empty:
            return
        self._funding_index = {
            snap_to_funding_grid(pd.Timestamp(ts)): float(rate)
            for ts, rate in frame["funding_rate"].items()
        }

    # ── 公共面（ReplayRunner 消费）──

    @property
    def main(self) -> pd.DataFrame:
        """主周期帧（含 warmup 前缀+指标列；None=未装载）。"""
        return self._main  # type: ignore[return-value]

    @property
    def main_start(self) -> pd.Timestamp:
        """请求起点（归一 UTC；warmup 前缀 < 该值）。"""
        return self._main_start  # type: ignore[return-value]

    def segments(self) -> Iterator[Tuple[int, Dict[str, pd.DataFrame]]]:
        """分段迭代：(段起点主 bar 下标, 各辅帧切片) 序列。

        分段算法：主轴逐根推进，每根收盘时刻 c_i=index[i]+tf_delta；
        各辅轴可见前缀长度 k_tf=|{辅 bar: 收盘时刻 ≤ c_i}|（含等号
        ——判定可见性契约）；可见向量 (k_tf...) 相同的连续主 bar 归
        同段，变化处为段边界。段内辅帧引用不变（process_frames 调用
        开头组装快照——段边界刷新即重建可见性）。单周期策略产单段
        全区间（辅帧表空 dict）。
        """
        assert self._main is not None, "load() 先行"
        main_index = self._main.index
        tf_minutes = TF_MINUTES.get(self.timeframe, 240)
        tf_delta = pd.Timedelta(minutes=tf_minutes)
        close_times = main_index + tf_delta
        aux_close: Dict[str, pd.Timestamp] = {}
        for tf, aux_df in self._aux.items():
            aux_minutes = TF_MINUTES.get(tf, 240)
            aux_close[tf] = aux_df.index + pd.Timedelta(minutes=aux_minutes)
        seg_start = 0
        prev_vis: Optional[Tuple[int, ...]] = None
        for i in range(len(main_index)):
            c_i = close_times[i]
            vis = tuple(
                int((aux_close[tf] <= c_i).sum()) for tf in self._aux)
            if prev_vis is not None and vis != prev_vis:
                yield seg_start, {
                    tf: self._aux[tf].iloc[:prev_vis[pos]]
                    for pos, tf in enumerate(self._aux)}
                seg_start = i
            prev_vis = vis
        yield seg_start, {
            tf: self._aux[tf].iloc[:prev_vis[pos]]
            if prev_vis is not None else self._aux[tf]
            for pos, tf in enumerate(self._aux)}

    def funding_lookup(
        self, points: List[pd.Timestamp],
    ) -> Dict[pd.Timestamp, Optional[float]]:
        """按 8h 网格点查费率（缺值 None→挂起——与 SymbolFeed.
        funding_rates 同签名同语义）。"""
        return {p: self._funding_index.get(p) for p in points}

    def aux_timeframes(self) -> List[str]:
        """辅轴周期清单（dna.timeframes 去主轴）。"""
        return [
            tf for tf in self.dna.timeframes if tf != self.timeframe]

    def parquet_exists(self, tf: str) -> bool:
        """数据可行性检查（装载链 4——find_parquet 公共入口含
        safe_symbol 规整与 TF 别名回退，不自拼路径）。"""
        return find_parquet(self.data_dir, self.symbol, tf) is not None
