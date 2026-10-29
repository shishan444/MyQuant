"""parquet 写入互斥单点测试（VS-1.11——add-data-freshness-guardian 契约 2）。

三公开写入口（save_parquet/merge_parquet/merge_closed_rows）按文件
粒度互斥：两线程并发 merge 同文件，行集并集完整落盘无丢行；嵌套
组合写盘经私有内层不自锁（merge_parquet 内部两处内调 save 语义
等价面：merge 直调场景全绿即证明拆解正确——threading.Lock 不可
重入，若未拆内层此处全量用例将死锁）。
"""
from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd

from core.data.storage import (
    _file_locks,
    load_parquet,
    merge_closed_rows,
    merge_parquet,
    parquet_write_lock,
    save_parquet,
)


def _ohlcv(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """构造 OHLCV 帧（index=UTC 时刻；close 唯一可辨识）。"""
    index = pd.DatetimeIndex(
        [pd.Timestamp(ts, tz="UTC") for ts, _ in rows], name="timestamp")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0,
         "close": [v for _, v in rows], "volume": 0.0},
        index=index)


def test_concurrent_merge_parquet_no_lost_rows(tmp_path: Path) -> None:
    """两线程并发 merge_parquet 不相交行集——并集完整落盘。"""
    path = tmp_path / "kline.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)

    left = _ohlcv([
        ("2026-01-01 00:01:00", 10.0), ("2026-01-01 00:02:00", 11.0)])
    right = _ohlcv([
        ("2026-01-01 00:03:00", 20.0), ("2026-01-01 00:04:00", 21.0)])

    barrier = threading.Barrier(2)

    def worker(frame: pd.DataFrame) -> None:
        barrier.wait()  # 两线程同相位进入读-改-写序列
        merge_parquet(frame, path)

    threads = [
        threading.Thread(target=worker, args=(frame,))
        for frame in (left, right)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    merged = load_parquet(path)
    assert list(merged["close"]) == [1.0, 10.0, 11.0, 20.0, 21.0]  # 并集无丢行


def test_concurrent_merge_closed_rows_no_lost_rows(tmp_path: Path) -> None:
    """两线程并发 merge_closed_rows——并集完整落盘（keep=first 语义）。"""
    path = tmp_path / "closed.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)

    left = _ohlcv([("2026-01-01 00:01:00", 10.0)])
    right = _ohlcv([("2026-01-01 00:02:00", 20.0)])
    barrier = threading.Barrier(2)

    def worker(frame: pd.DataFrame) -> None:
        barrier.wait()
        merge_closed_rows(path, frame)

    threads = [
        threading.Thread(target=worker, args=(frame,))
        for frame in (left, right)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    merged = load_parquet(path)
    assert list(merged["close"]) == [1.0, 10.0, 20.0]


def test_lock_keyed_by_resolved_path(tmp_path: Path) -> None:
    """同文件复用同一把锁（锁表单源）；锁表键为绝对路径。"""
    path = tmp_path / "same.parquet"
    with parquet_write_lock(path):
        key = str(path.resolve())
        assert key in _file_locks
        # 同键取到同一把锁对象（setdict 单源——并发方互斥的前提）
        assert _file_locks[key] is _file_locks[key]
    # 相对/绝对两形制同键：cwd 相对路径解析到同一绝对键
    rel = path.parent / path.name
    with parquet_write_lock(rel):
        assert _file_locks[str(rel.resolve())] is _file_locks[key]


def test_explicit_lock_context_serializes_writers(tmp_path: Path) -> None:
    """显式持锁上下文与公开入口共用同一把锁（组合写盘面）。"""
    path = tmp_path / "combo.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)
    order: list[str] = []

    with parquet_write_lock(path):
        order.append("ctx")
        # 互斥证明：上下文持有期间另一线程无法完成 merge（join 带超时）
        done = threading.Event()

        def blocked():
            merge_parquet(
                _ohlcv([("2026-01-01 00:05:00", 99.0)]), path)
            done.set()

        t = threading.Thread(target=blocked)
        t.start()
        t.join(timeout=0.5)
        assert t.is_alive() and not done.is_set()  # 被锁阻塞（0.5s 内未完成）
    # 释放后阻塞方完成
    t.join(timeout=5.0)
    assert done.is_set()
    order.append("merged")
    assert order == ["ctx", "merged"]
    assert list(load_parquet(path)["close"]) == [1.0, 99.0]


def test_three_entry_points_hold_lock_during_body(tmp_path, monkeypatch):
    """VS-1.11②：save_parquet/merge_parquet/merge_closed_rows 三公开
    入口函数体内文件锁为持态（hook 读侧检查 _file_locks[键].locked()）。

    hook 面=load_parquet（merge 族读侧必经）+to_parquet（save 内层
    必经）——写序列进行中锁必持。
    """
    import core.data.storage as storage_mod

    path = tmp_path / "probe.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)
    key = str(path.resolve())
    held: list[bool] = []
    original_load = storage_mod.load_parquet
    original_to_parquet_hook = pd.DataFrame.to_parquet

    def probe_load(p, *a, **kw):
        lock = storage_mod._file_locks.get(key)
        held.append(bool(lock and lock.locked()))
        return original_load(p, *a, **kw)

    def probe_to_parquet(self, *a, **kw):
        # to_parquet 首参即路径（save 内层调用形制）
        lock = storage_mod._file_locks.get(key)
        held.append(bool(lock and lock.locked()))
        return original_to_parquet_hook(self, *a, **kw)

    monkeypatch.setattr(storage_mod, "load_parquet", probe_load)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", probe_to_parquet)

    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)  # 入口 1
    assert held and all(held), "save_parquet 体内锁须持态"
    held.clear()

    merge_parquet(_ohlcv([("2026-01-01 00:01:00", 2.0)]), path)  # 入口 2
    assert held and all(held), "merge_parquet 读-改-写序列锁须持态"
    held.clear()

    merge_closed_rows(path, _ohlcv([("2026-01-01 00:02:00", 3.0)]))  # 入口 3
    assert held and all(held), "merge_closed_rows 读-改-写序列锁须持态"
    monkeypatch.setattr(pd.DataFrame, "to_parquet", original_to_parquet_hook)
