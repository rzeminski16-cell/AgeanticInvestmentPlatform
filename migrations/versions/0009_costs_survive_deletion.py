"""Make spend records outlive the runs they paid for.

``costs`` referenced ``jobs``, ``job_steps`` and ``agent_runs``, all with ``ON DELETE
CASCADE``, and all three chain back to ``research_requests``. Deleting a request therefore
erased every record of what it had cost — by three separate paths, so removing one would
not have helped.

That is a hole in the budget ledger, and the budget is a control rather than a report: the
platform exists to run inside about £100 a month, and a total that can be reduced by
deleting the thing you spent it on is not a control. It also made "delete this request" and
"keep an honest month's spend" mutually exclusive, so the delete button was refused for any
request that had reached the planner — which is nearly all of them.

``audit_events`` already solves this by keeping ``request_id`` and ``job_id`` as plain
columns with no foreign key at all, precisely so a record survives the thing it describes.
This applies the same reasoning one step less drastically: the references stay, and become
``SET NULL``. The row survives with its amount, its date, its provider and its model; what
it was spent on is preserved in the ``request.deleted`` audit entry, which by construction
outlives the request.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# (constraint, column, referenced table). Postgres has no "ALTER CONSTRAINT ... ON DELETE",
# so each one is dropped and recreated.
_REFERENCES = (
    ("fk_costs_job_id_jobs", "job_id", "jobs"),
    ("fk_costs_job_step_id_job_steps", "job_step_id", "job_steps"),
    ("fk_costs_agent_run_id_agent_runs", "agent_run_id", "agent_runs"),
)


def upgrade() -> None:
    for name, column, target in _REFERENCES:
        op.drop_constraint(name, "costs", type_="foreignkey")
        op.create_foreign_key(name, "costs", target, [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for name, column, target in _REFERENCES:
        op.drop_constraint(name, "costs", type_="foreignkey")
        op.create_foreign_key(name, "costs", target, [column], ["id"], ondelete="CASCADE")
