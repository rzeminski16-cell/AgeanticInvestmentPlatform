"""A bank gets a bank model.

:mod:`aer.calc.residual_income` values a bank's equity as its book value plus the present
value of the return it earns above its cost of equity, so ``residual_income`` becomes a
permitted model for the two profiles whose balance sheet is the reliable part of their
accounts and whose free cash flow to the firm is blocked: banks and insurers.

Two warnings move with it. The bank warning said this build "does not implement a
specialist bank model", which is no longer true and would read to a report's reader as a
gap where there is now a model; it is replaced by the caveat that actually limits the new
model, clean surplus. The insurer warning gains the corresponding limit — residual income
on reported book value is not an embedded-value calculation.

The rendered warnings and the permitted set both come from
:data:`aer.core.sectors.SECTOR_PROFILES`; these rows are the parallel record the Phase 3
gate queries, and ``test_the_seed_matches_the_constants`` is what keeps the two in step.
This migration is that step.

Revision ID: 0053
Revises: 0052
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


_MODEL = "residual_income"

_BANK_OLD = (
    "Capital adequacy, net interest margin and provisioning govern the valuation. "
    "This build produces P/TBV and P/E comparables only, and does not implement a "
    "specialist bank model."
)
_BANK_NEW = (
    "Capital adequacy, net interest margin and provisioning govern the valuation. "
    "The residual-income model offered here values the excess return earned on book "
    "value, and equals a dividend discount only under clean surplus: a bond book "
    "carrying unrealised losses through other comprehensive income is treated as "
    "fully earning."
)
_INSURER_NEW = (
    "The residual-income model offered here values the excess return on reported "
    "book value. It is not an embedded-value calculation, so for life business it "
    "omits the value of profits already written into policies in force."
)


def _permit(key: str, model: str) -> None:
    """Append one model to a profile's ``allowed_models``, leaving the rest alone.

    Read-modify-write rather than a whole-array overwrite, for the reason migration 0051
    gives: the array's other entries are not this migration's business, and rewriting them
    wholesale would silently revert any other change made to them.
    """
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT allowed_models FROM sector_profiles WHERE key = :key"), {"key": key}
    ).first()
    if row is None:
        return
    allowed = list(row[0] or [])
    if model in allowed:
        return
    allowed.append(model)
    connection.execute(
        sa.text("UPDATE sector_profiles SET allowed_models = :allowed WHERE key = :key"),
        {"key": key, "allowed": json.dumps(allowed)},
    )


def _forbid(key: str, model: str) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT allowed_models FROM sector_profiles WHERE key = :key"), {"key": key}
    ).first()
    if row is None:
        return
    allowed = [entry for entry in (row[0] or []) if entry != model]
    connection.execute(
        sa.text("UPDATE sector_profiles SET allowed_models = :allowed WHERE key = :key"),
        {"key": key, "allowed": json.dumps(allowed)},
    )


def _rewrite(key: str, before: str, after: str) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT warnings FROM sector_profiles WHERE key = :key"), {"key": key}
    ).first()
    if row is None:
        return
    warnings = list(row[0] or [])
    if before not in warnings:
        return
    warnings[warnings.index(before)] = after
    connection.execute(
        sa.text("UPDATE sector_profiles SET warnings = :warnings WHERE key = :key"),
        {"key": key, "warnings": json.dumps(warnings)},
    )


def _append_warning(key: str, warning: str) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT warnings FROM sector_profiles WHERE key = :key"), {"key": key}
    ).first()
    if row is None:
        return
    warnings = list(row[0] or [])
    if warning in warnings:
        return
    warnings.append(warning)
    connection.execute(
        sa.text("UPDATE sector_profiles SET warnings = :warnings WHERE key = :key"),
        {"key": key, "warnings": json.dumps(warnings)},
    )


def _drop_warning(key: str, warning: str) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT warnings FROM sector_profiles WHERE key = :key"), {"key": key}
    ).first()
    if row is None:
        return
    warnings = [entry for entry in (row[0] or []) if entry != warning]
    connection.execute(
        sa.text("UPDATE sector_profiles SET warnings = :warnings WHERE key = :key"),
        {"key": key, "warnings": json.dumps(warnings)},
    )


def upgrade() -> None:
    _permit("banks", _MODEL)
    _permit("insurers", _MODEL)
    _rewrite("banks", _BANK_OLD, _BANK_NEW)
    _append_warning("insurers", _INSURER_NEW)


def downgrade() -> None:
    _forbid("banks", _MODEL)
    _forbid("insurers", _MODEL)
    _rewrite("banks", _BANK_NEW, _BANK_OLD)
    _drop_warning("insurers", _INSURER_NEW)
