"""Data 上传防护特征网（CARD-04 修复锚定，修复期安全网）。

覆盖 api/routes/data.py 上传链的大小/数量限制（现状=修复后行为）：
- 单文件超限 → 413（读前 size 预检）
- _copy_limited 纵深：size 缺失时分块累计超限 → 413（1MB 分块不全量入内存）
- 批量 >20 文件 → 400
- 批量含超限成员 → 413 且零临时文件泄漏（预检前置）
- 正常小文件导入不受影响（对照组）

上限常量经 patch 缩小到测试可承受的量级（KB 级），走真实 HTTP 路径。
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from fastapi import HTTPException
from fastapi.testclient import TestClient

_SMALL_CSV = (
    "timestamp,open,high,low,close,volume\n"
    "2024-01-01T00:00:00Z,100,110,90,105,10\n"
    "2024-01-01T04:00:00Z,105,115,95,110,12\n"
)


def _files_field(n: int, content: bytes | None = None):
    # 全列有效 CSV（缺列会穿透到业务层 KeyError→500，与防护断言无关）
    if content is None:
        content = _SMALL_CSV.encode()

    return [("files", (f"f{i}.csv", content, "text/csv")) for i in range(n)]


def _endpoint_data_module():
    """定位 conftest 装配的 app 实际使用的 data 路由模块对象。

    身份坑：editable 安装把 MyQuant/ 内子目录映射为顶层包（api.*），
    而 tests/__init__.py 使 pytest 又把项目根插入 sys.path——conftest
    的 `from MyQuant.api.app import create_app` 装配出 MyQuant.api.*
    身份端点。patch 字符串目标 "api.routes.data" 会导入另一份模块，
    对端点无效（本文件首版即踩此坑：413 用例全部穿透到业务层）。
    必须按 sys.modules 中与 app 同身份的现存对象做 patch.object。
    """
    import sys
    mod = sys.modules.get("MyQuant.api.routes.data")
    if mod is None:
        import api.routes.data as mod  # 直跑（无 conftest 装配）时回退
    return mod


@pytest.fixture
def tiny_limit():
    """将 256MB 上限 patch 到 1KB，使超限路径可在测试中真实触达。"""
    with patch.object(_endpoint_data_module(), "MAX_IMPORT_FILE_BYTES", 1024):
        yield


class TestSingleImportLimit:
    def test_oversized_single_file_413(
        self, api_client: TestClient, tiny_limit,
    ) -> None:
        """单文件超限 → 413，detail 指明文件名与上限。"""
        big = b"x" * 2048
        response = api_client.post(
            "/api/data/import",
            files={"file": ("big.csv", big, "text/csv")},
        )
        assert response.status_code == 413
        assert "big.csv" in response.json()["detail"]
        assert "exceeds" in response.json()["detail"]

    def test_normal_size_import_unaffected(
        self, api_client: TestClient,
    ) -> None:
        """对照组：KB 级文件不被防护拒绝（穿过 413 关到达业务层）。"""
        response = api_client.post(
            "/api/data/import",
            files={"file": ("BTCUSDT_4h.csv", _SMALL_CSV.encode(), "text/csv")},
        )
        # 防护语义只锚定「不因大小被拒」；业务层对 CSV 内容的
        # 校验结果（200/400）与本卡无关
        assert response.status_code != 413


class TestCopyLimitedDepth:
    """_copy_limited 纵深（size 缺失时的分块累计防线）。"""

    def test_copy_under_limit_writes_all(self, tiny_limit) -> None:
        dest = io.BytesIO()
        src = io.BytesIO(b"a" * 512)
        _endpoint_data_module()._copy_limited(dest, src)  # 不应抛异常
        assert dest.getvalue() == b"a" * 512

    def test_copy_accumulated_over_limit_413(self, tiny_limit) -> None:
        """分块累计超限 → 413（模拟 size 缺失流式上传超限）。"""
        dest = io.BytesIO()
        src = io.BytesIO(b"a" * 4096)
        with pytest.raises(HTTPException) as exc:
            _endpoint_data_module()._copy_limited(dest, src)
        assert exc.value.status_code == 413

    def test_copy_reads_in_chunks_not_all_at_once(self) -> None:
        """纵深口径：按 1MB 块读取（read 调用参数为块大小，非全量）。"""
        reads: list = []

        class _RecordingSrc(io.BytesIO):
            def read(self, size=-1):  # type: ignore[override]
                reads.append(size)
                return super().read(size)

        mod = _endpoint_data_module()
        mod._copy_limited(io.BytesIO(), _RecordingSrc(b"a" * 10))
        assert reads and all(r == mod._COPY_CHUNK_BYTES for r in reads)


class TestBatchImportLimits:
    def test_batch_over_20_files_400(self, api_client: TestClient) -> None:
        """批量 >20 文件 → 400，detail 注明 max 20。"""
        response = api_client.post(
            "/api/data/import-batch", files=_files_field(21),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Too many files: max 20"

    def test_batch_at_20_files_passes_guard(
        self, api_client: TestClient,
    ) -> None:
        """边界对照：恰 20 文件通过数量关（后续由业务层处理）。"""
        response = api_client.post(
            "/api/data/import-batch", files=_files_field(20),
        )
        assert response.status_code != 400 or \
            "Too many files" not in response.json().get("detail", "")

    def test_batch_oversized_member_413_no_temp_leak(
        self, api_client: TestClient, tiny_limit,
    ) -> None:
        """批量含超限成员 → 413，且 data_dir 零临时文件残留（预检前置）。"""
        data_dir: Path = api_client.app.state.data_dir
        before = {p.name for p in data_dir.iterdir()}
        response = api_client.post(
            "/api/data/import-batch",
            files=[
                ("files", ("ok.csv", _SMALL_CSV.encode(), "text/csv")),
                ("files", ("huge.csv", b"x" * 4096, "text/csv")),
            ],
        )
        assert response.status_code == 413
        after = {p.name for p in data_dir.iterdir()}
        leaked = {n for n in (after - before) if n.endswith(".csv")}
        assert leaked == set(), f"预检拒绝路径泄漏临时文件: {leaked}"

    def test_batch_normal_files_import_unaffected(
        self, api_client: TestClient,
    ) -> None:
        """对照组：正常小文件批量不被防护拒绝（穿过数量与大小关）。"""
        response = api_client.post(
            "/api/data/import-batch",
            files=[
                ("files", ("BTCUSDT_4h.csv",
                           _SMALL_CSV.encode(), "text/csv")),
                ("files", ("BTCUSDT_4h.csv",
                           _SMALL_CSV.encode(), "text/csv")),
            ],
        )
        assert response.status_code not in (400, 413) or \
            "Too many files" not in str(response.json().get("detail", "")) \
            and "too large" not in str(response.json().get("detail", "")).lower()


class TestCopyMidstream413Cleanup:
    """size 缺失走 _copy_limited 累计超限（纵深路径）的中途 413 清理。

    security 修补锚定：临时文件段并入主 try（先记路径再写），copy
    中途拒绝时 finally 清理——批量场景第 N 个文件超限时，前 N-1 个
    已写临时文件同样被清。经函数级直调构造 size=None 的 UploadFile
    （HTTP multipart 下解析器恒记录 size，预检先行拦截）。
    """

    @staticmethod
    def _upload(content: bytes):
        import tempfile as _tf
        from starlette.datastructures import UploadFile
        spool = _tf.SpooledTemporaryFile()
        spool.write(content)
        spool.seek(0)
        return UploadFile(file=spool, size=None, filename="mid.csv")

    def test_single_copy_midstream_413_no_temp_leak(
        self, tmp_path: Path, tiny_limit,
    ) -> None:
        db_path = tmp_path / "t.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mod = _endpoint_data_module()
        with pytest.raises(HTTPException) as exc:
            mod.import_csv_endpoint(
                file=self._upload(b"x" * 4096), symbol=None, interval=None,
                mode="merge", db_path=db_path, data_dir=data_dir,
            )
        assert exc.value.status_code == 413
        leaked = [p.name for p in data_dir.iterdir() if p.suffix == ".csv"]
        assert leaked == [], f"copy 中途 413 泄漏临时文件: {leaked}"

    def test_batch_copy_midstream_413_cleans_prior_temps(
        self, tmp_path: Path, tiny_limit,
    ) -> None:
        """批量第 2 个文件 copy 中途超限：第 1 个已写临时文件同被清理。"""
        db_path = tmp_path / "t2.db"
        data_dir = tmp_path / "data2"
        data_dir.mkdir()
        mod = _endpoint_data_module()
        files = [
            self._upload(_SMALL_CSV.encode()),  # 先成功落盘临时文件
            self._upload(b"x" * 4096),          # 中途超限触发 finally
        ]
        with pytest.raises(HTTPException) as exc:
            mod.import_csv_batch_endpoint(
                files=files, symbol=None, interval=None,
                mode="merge", db_path=db_path, data_dir=data_dir,
            )
        assert exc.value.status_code == 413
        leaked = [p.name for p in data_dir.iterdir() if p.suffix == ".csv"]
        assert leaked == [], f"copy 中途 413 泄漏临时文件: {leaked}"
