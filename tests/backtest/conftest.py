"""worktree 环境路径补丁（retire-legacy 终波环境三件套补齐件）。

pytest 从 worktree 根启动时 cwd 已能解析 tests 包身份，prepend 导入
被短路，仓库父目录（提供 MyQuant 顶层包身份）不进 sys.path，测试里
`from MyQuant.api.app import create_app` 双身份导入面全灭。在此注入
仓库父目录；主 checkout 场景该目录本在 conftest basedir 路径上，
重复插入幂等无害。
"""
import sys as _sys
from pathlib import Path as _Path

_PARENT = str(_Path(__file__).resolve().parents[3])
if _PARENT not in _sys.path:
    _sys.path.insert(0, _PARENT)
