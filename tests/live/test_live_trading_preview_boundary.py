"""live-trading-preview（C8）后端零改动边界锚。

spec Scenario「后端零改动断言」（openspec/changes/live-trading-preview/
specs/live-trading-preview/spec.md）：本 change SHALL 保持 api/ 与
core/live_trading/ 零 diff——纯前端 change 边界的机械验证。

固化判定（tester）：该 Scenario 原文是闸门手工命令（`git diff main
--stat -- api/ core/live_trading/` 输出为空），此前不入任何测试锚。
固化为 pytest 锚后，全量批次在本 change 分支族上自动守卫，修复期任何
人误改后端即红，不再依赖闸门手工命令被记得执行。

实现口径与 spec 原文的差异及理由：
- diff 基取 merge-base(main, HEAD) 而非 main 指针：审查窗口内 main 可能
  因其他 change 合并前移，指针形制会把 main 侧其他 change 的后端改动
  误报为红；merge-base 只度量本分支自己的变更，与 spec「变更分支相对
  main」语义一致（当前 main 即本分支基点，两种形制输出相同）。
- `git diff <commit>` 对比的是工作树：未提交的后端改动同样拦截。
- 分支族激活：仅在 feature/live-trading-preview / fix/live-trading-review
  上运行，其余分支与合并后的 main skip——零后端改动是本 change 的边界
  约束，不是永久产品不变量；无差别运行会让未来合法触碰 api/ 的 change
  莫名见红。
"""
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活

# 本 change 分支族：原实施分支 + 审查修复分支
_CHANGE_FAMILY_BRANCHES = {"feature/live-trading-preview", "fix/live-trading-review"}
# spec 点名的后端零改动域
_BACKEND_PATHS = ("api/", "core/live_trading/")

_REPO = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(_REPO)
    )
    if proc.returncode != 0:
        pytest.skip(f"git {' '.join(args)} 不可用：{proc.stderr.strip()}")
    return proc.stdout


class TestLivePreviewBackendBoundary:
    def test_backend_zero_diff(self):
        """api/ 与 core/live_trading/ 相对 main 基点零 diff（spec Scenario 1）。"""
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch not in _CHANGE_FAMILY_BRANCHES:
            pytest.skip(
                f"非 live-trading-preview 分支族（当前 {branch}）——"
                "零改动边界锚仅对本 change 分支有效"
            )
        base_ref = next(
            (r for r in ("main", "origin/main") if _ref_exists(r)), None
        )
        if base_ref is None:
            pytest.skip("本地与远端均无 main ref，零 diff 断言无基准")
        merge_base = _git("merge-base", base_ref, "HEAD").strip()
        diff = _git("diff", merge_base, "--stat", "--", *_BACKEND_PATHS)
        assert diff.strip() == "", (
            "live-trading-preview 要求后端零改动，"
            f"但 { '/'.join(_BACKEND_PATHS) } 相对 {merge_base[:8]} 存在 diff：\n{diff}"
        )


def _ref_exists(ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True, cwd=str(_REPO),
    )
    return proc.returncode == 0
