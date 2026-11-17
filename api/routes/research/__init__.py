from __future__ import annotations

from fastapi import APIRouter

from . import (
    analysis,
    cases,
    indicator_query,
    log,
    plugins,
    records_list,
    registry,
    snapshot,
    validation,
    validation_detail,
)

router = APIRouter()
for _sub in (
    validation,
    validation_detail,
    analysis,
    cases,
    indicator_query,
    plugins,
    registry,
    snapshot,
    log,
    records_list,
):
    router.include_router(_sub.router, prefix="/api/research", tags=["research"])
