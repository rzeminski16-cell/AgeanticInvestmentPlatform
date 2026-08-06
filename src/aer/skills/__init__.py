"""User-authored skill files: parsing, validation and (in later tasks) execution.

The schema the files must satisfy lives in :mod:`aer.core.schemas.skill` and the
additive-only composer in :mod:`aer.core.skill_policy` — both pure. This package holds the
parts that owe the world a dependency: YAML parsing with line numbers here, and from task
36 onward the resolution and execution machinery.
"""

from __future__ import annotations
