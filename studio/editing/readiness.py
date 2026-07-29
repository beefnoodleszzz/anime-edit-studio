"""Refuse to cut when the footage cannot support the brief (AGENTS.md R11).

This exists because of a specific failure, not as ceremony.  A project asked for
Akaza; the tagger has never heard of him, so ``--character akaza`` retrieved
nothing; a looser query stood in for it, and the system cheerfully built,
rendered and scored an eighteen-second piece in which ten of twenty-six shots
were from a different anime entirely.  Every downstream metric passed.  Nothing
in the pipeline was capable of noticing that the film was of the wrong person.

So the gate checks the two things that failure needed in order to happen:

- the target character must be **identifiable** — a name that resolves to enough
  labelled shots, rather than a name that silently resolves to nothing;
- the candidate pool must be **single-work** — a pool spanning several source
  works means the query is matching a look, not a character.

Both are cheap, deterministic, and refuse loudly.  A gate that only warns would
not have stopped this: the render happened, and the warning would have scrolled
past.
"""
from __future__ import annotations

import sqlite3
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

READINESS_VERSION = "production-readiness-1.0.0"

#: Fewer labelled shots than this cannot fill six roles without heavy reuse, and
#: is far more often a sign the name did not resolve at all.
MIN_IDENTIFIED_SHOTS = 12
#: Share of the pool that must come from one source work.  Mixed-work pools are
#: how a look-alike from another series gets in.
MIN_SINGLE_WORK_SHARE = 0.85


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    passed: bool
    detail: str
    remedy: str | None = None


class ProductionReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = READINESS_VERSION
    project_id: str
    character: str | None = None
    ready: bool
    checks: list[ReadinessCheck]

    @property
    def blocking(self) -> list[ReadinessCheck]:
        return [check for check in self.checks if not check.passed]


class ProductionNotReady(RuntimeError):
    """Raised instead of building a first cut the footage cannot support."""

    def __init__(self, report: ProductionReadinessReport) -> None:
        reasons = "; ".join(
            f"{check.name}: {check.detail}" for check in report.blocking
        )
        super().__init__(f"素材未就绪，拒绝出片 —— {reasons}")
        self.report = report


def _work_of(path: str) -> str:
    """The source work a media file belongs to.

    Library layout is ``sources/<work>/<season>/<file>``, so the work is the
    directory under ``sources``.  Falling back to the filename stem keeps the
    check meaningful for flat layouts instead of silently passing.
    """
    parts = PurePath(path).parts
    if "sources" in parts:
        index = parts.index("sources")
        if index + 1 < len(parts):
            return parts[index + 1]
    return PurePath(path).stem.split("_")[0] or "unknown"


def evaluate_production_readiness(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    character: str | None,
    candidate_shot_ids: list[str],
) -> ProductionReadinessReport:
    """Check that the brief's character is real and the pool is one work."""
    checks: list[ReadinessCheck] = []

    if character:
        identified = conn.execute(
            "SELECT count(*) FROM shots WHERE character=?", (character,)
        ).fetchone()[0]
        checks.append(
            ReadinessCheck(
                name="character_identified",
                passed=identified >= MIN_IDENTIFIED_SHOTS,
                detail=(
                    f"character='{character}' 在库中有 {identified} 个已标注镜头"
                    f"（需 >= {MIN_IDENTIFIED_SHOTS}）"
                ),
                remedy=(
                    None if identified >= MIN_IDENTIFIED_SHOTS else
                    "标注器可能不认识该角色；用 studio.asset_intelligence.appearance "
                    "以人工确认的种子建 Appearance Catalog，再重跑。"
                    "禁止改用宽松标签（如发色）顶替——那会剪出另一个角色。"
                ),
            )
        )

    if candidate_shot_ids:
        placeholders = ",".join("?" for _ in candidate_shot_ids)
        works: dict[str, int] = {}
        for row in conn.execute(
            "SELECT a.path FROM shots s JOIN assets a ON a.id=s.asset_id "
            f"WHERE s.id IN ({placeholders})",
            candidate_shot_ids,
        ):
            work = _work_of(row["path"])
            works[work] = works.get(work, 0) + 1
        total = sum(works.values())
        top_work, top_count = (
            max(works.items(), key=lambda item: item[1]) if works else ("", 0)
        )
        share = top_count / total if total else 0.0
        checks.append(
            ReadinessCheck(
                name="single_work_pool",
                passed=share >= MIN_SINGLE_WORK_SHARE,
                detail=(
                    f"候选来自 {len(works)} 部作品，主作品 '{top_work}' 占 "
                    f"{share:.0%}（需 >= {MIN_SINGLE_WORK_SHARE:.0%}）："
                    + ", ".join(
                        f"{name}={count}"
                        for name, count in sorted(
                            works.items(), key=lambda item: -item[1]
                        )
                    )
                ),
                remedy=(
                    None if share >= MIN_SINGLE_WORK_SHARE else
                    "检索命中了跨作品的相似外观。收窄到目标作品的素材范围，"
                    "或先建 Appearance Catalog 按角色而非外观检索。"
                ),
            )
        )

    return ProductionReadinessReport(
        project_id=project_id,
        character=character,
        ready=all(check.passed for check in checks),
        checks=checks,
    )


__all__ = [
    "MIN_IDENTIFIED_SHOTS",
    "MIN_SINGLE_WORK_SHARE",
    "READINESS_VERSION",
    "ProductionNotReady",
    "ProductionReadinessReport",
    "ReadinessCheck",
    "evaluate_production_readiness",
]
