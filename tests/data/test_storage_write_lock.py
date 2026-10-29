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
import pytest

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


def test_different_file_paths_do_not_block_each_other(tmp_path: Path) -> None:
    """VS-1.11③ 文件粒度：A 路径持锁期间 B 路径写入立即完成——
    互斥按文件键粒度，不同路径不互相阻塞（全局单锁实现在此红）。"""
    path_a = tmp_path / "a.parquet"
    path_b = tmp_path / "b.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path_a)
    in_lock = threading.Event()
    release = threading.Event()

    def hold_a() -> None:
        with parquet_write_lock(path_a):
            in_lock.set()
            release.wait(timeout=5.0)  # 受控持锁窗口（主线程显式释放）

    holder = threading.Thread(target=hold_a)
    holder.start()
    assert in_lock.wait(timeout=5.0)  # A 键已持态
    try:
        writer = threading.Thread(
            target=merge_parquet,
            args=(_ohlcv([("2026-01-01 00:01:00", 2.0)]), path_b))
        writer.start()
        writer.join(timeout=2.0)
        assert not writer.is_alive(), "不同路径键不得互相阻塞（文件粒度）"
        assert list(load_parquet(path_b)["close"]) == [2.0]
    finally:
        release.set()
        holder.join(timeout=5.0)


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


# ── 原子写（CARD-03——save_parquet 崩溃窗口消除）───────────────

@pytest.mark.parametrize("entry", [
    pytest.param("save", id="save_parquet"),
    pytest.param("merge", id="merge_parquet"),
    pytest.param("closed", id="merge_closed_rows"),
])
def test_atomic_write_crash_keeps_old_intact(tmp_path: Path, entry: str) -> None:
    """CARD-03：三写入口写中途崩溃——目标文件保持旧版完整可读
    （崩溃窗口消除）、tmp 异常路径清理、tmp 落同目录（os.replace
    原子性前提）且不与数据集直构路径同名。"""
    path = tmp_path / "atomic.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)
    new_rows = _ohlcv([("2026-01-01 00:01:00", 2.0)])
    seen_tmp: list[Path] = []

    def crash_to_parquet(self, p, *a, **kw):
        seen_tmp.append(Path(p))
        Path(p).write_text("half-written")  # 模拟半写内容落盘
        raise RuntimeError("crash mid-write")

    calls = {
        "save": lambda: save_parquet(new_rows, path),
        "merge": lambda: merge_parquet(new_rows, path),
        "closed": lambda: merge_closed_rows(path, new_rows),
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pd.DataFrame, "to_parquet", crash_to_parquet)
        with pytest.raises(RuntimeError):
            calls[entry]()

    # 崩溃后目标文件旧版完整（读侧旧版可读——可安全重试）
    assert list(load_parquet(path)["close"]) == [1.0]
    # 实写发生在同目录 tmp 上（同文件系统——rename 原子性前提）
    # 且 tmp 名不与 {dataset_id}.parquet 直构路径形制同名
    assert len(seen_tmp) == 1
    tmp = seen_tmp[0]
    assert tmp.parent == path.parent
    assert tmp.name != path.name
    assert ".tmp-" in tmp.name
    # 异常路径 tmp 已被 finally 清理（目录零残留）
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]


def test_atomic_write_no_tmp_residue_on_success(tmp_path: Path) -> None:
    """CARD-03：正常路径三写入口全执行后目录零 tmp 残留（os.replace
    完成形制），三入口写入成果俱在。"""
    path = tmp_path / "clean.parquet"
    save_parquet(_ohlcv([("2026-01-01 00:00:00", 1.0)]), path)
    merge_parquet(_ohlcv([("2026-01-01 00:01:00", 2.0)]), path)
    merge_closed_rows(path, _ohlcv([("2026-01-01 00:02:00", 3.0)]))
    assert [p.name for p in tmp_path.iterdir()] == ["clean.parquet"]
    assert list(load_parquet(path)["close"]) == [1.0, 2.0, 3.0]
