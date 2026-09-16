-- [文档性废弃 · B3a.2 2026-09-16] schema 演进权威已移交 Alembic 版本链
--（baseline ae6e14d43dc5 固化终态 schema）。本文件保留仅作历史记录，
-- 其语义已并入 ORM 模型与 baseline；新库 schema 一律经 alembic upgrade
-- 生成。本文件随旧轨在 B3d 退役时删除。

-- Migration 005: Add extended columns to evolution_task
-- These ALTER TABLE statements use IF NOT EXISTS equivalent via try/catch in Python.

-- champion_strategy_id: links to the winning strategy
ALTER TABLE evolution_task ADD COLUMN champion_strategy_id TEXT;

-- population_size: number of individuals per generation
-- (applied in Python with default check)

-- max_generations: cap on evolution iterations
-- (applied in Python with default check)

-- elite_ratio: fraction of top performers kept
-- (applied in Python with default check)

-- n_workers: parallel worker count
-- (applied in Python with default check)

-- current_generation: tracks progress
-- (applied in Python with default check)
