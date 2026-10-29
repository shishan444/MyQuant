"""超集周期冻结迁移（cleanup-data-segment 契约 5 / 裁决 B / R-14）。

把旧域独有超集周期（30m/3d K 线四文件）以冻结快照形态迁入活跃域：
①cp -p 复制（mtime 约束落在复制动作）→②校验（行数+首尾时间戳
一致+load_parquet 成功+mtime 等值断言）→③校验全过后删源。任一步
校验不过即中止留档（源文件不动可重跑——中止路径也是验收面）。

用法：python scripts/migrate_frozen_timeframes.py [--data-root PATH]
（缺省项目根 data/）。迁移完成后本脚本归档 works/历史脚本/。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# 迁移源精确清单（杜绝双源——market_backup_20260920 副本不纳入）
FROZEN_FILES = (
    "BTCUSDT_30m.parquet",
    "BTCUSDT_3d.parquet",
    "ETHUSDT_30m.parquet",
    "ETHUSDT_3d.parquet",
)


def _load(path: Path):
    import pandas as pd

    return pd.read_parquet(path, engine="pyarrow")


def migrate(data_root: Path) -> int:
    old = data_root / "market"
    new = data_root / "market_fapi"
    if not old.is_dir():
        print(f"[中止] 旧域不存在：{old}")
        return 1
    new.mkdir(parents=True, exist_ok=True)

    log: list[str] = []
    copied: list[Path] = []
    try:
        for name in FROZEN_FILES:
            src = old / name
            dst = new / name
            if not src.exists():
                print(f"[中止] 迁移源缺失：{src}")
                return 1
            if dst.exists():
                print(f"[中止] 目标已存在（避免双源覆写）：{dst}")
                return 1
            # ①cp -p（保 mtime）
            shutil.copy2(src, dst)
            copied.append(dst)
            # ②校验四关：行数/首尾时间戳/可装载/mtime 等值
            s, d = _load(src), _load(dst)
            assert len(s) == len(d), f"{name}: 行数 {len(s)} != {len(d)}"
            assert str(s.index[0]) == str(d.index[0]) and str(s.index[-1]) == str(d.index[-1]), \
                f"{name}: 首尾时间戳不一致"
            assert d.index.name == s.index.name, f"{name}: index 名漂移"
            mtime_equal = (
                src.stat().st_mtime == dst.stat().st_mtime)
            assert mtime_equal, f"{name}: mtime 未保持（cp -p 语义失效）"
            log.append(
                f"{name}: rows={len(s)} head={s.index[0]} "
                f"tail={s.index[-1]} mtime 保持")
            print(f"[ok] {log[-1]}")
        # ③校验全过 → 删源
        for name in FROZEN_FILES:
            (old / name).unlink()
            print(f"[del] 源已删：{old / name}")
    except AssertionError as exc:
        print(f"[中止-校验不过] {exc}（源文件不动可重跑）")
        for dst in copied:  # 回滚半成品副本
            dst.unlink(missing_ok=True)
        return 1

    manifest = "\n".join(
        [f"超集周期冻结迁移记录（{__import__('datetime').datetime.now().isoformat()}）",
         "四文件 cp -p 迁移校验通过（行数/首尾戳/可装载/mtime 等值）：", *log])
    print(manifest)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data-root", type=Path,
        default=Path(__file__).resolve().parents[1] / "data")
    args = ap.parse_args()
    sys.exit(migrate(args.data_root))


if __name__ == "__main__":
    main()
