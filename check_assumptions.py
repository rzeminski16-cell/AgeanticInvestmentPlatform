"""Print every assumption on one research request, and whether a person confirmed it.

The operator's own script, used to settle R13: it showed all eleven of CHRW's required
assumptions confirmed with the operator's address against each, which is what ruled out
the unconfirmed-assumption theory and left the derivation gap as the only cause.

Reads the live database, so it is a diagnostic and not part of the suite.
"""

import asyncio
import sys

from sqlalchemy import text

from aer.config import get_settings
from aer.db.engine import create_engine

REQUEST = sys.argv[1] if len(sys.argv) > 1 else "074909b3-a647-4ae3-81cd-4ace21f10837"
SQL = text("""
    SELECT name, value, unit, proposed_by, approved, approved_at, approved_by, created_at
    FROM assumptions WHERE request_id = :rid ORDER BY created_at
""")


async def main() -> None:
    engine = create_engine(get_settings())
    async with engine.connect() as conn:
        rows = (await conn.execute(SQL, {"rid": REQUEST})).mappings().all()
    await engine.dispose()
    if not rows:
        print(f"No assumptions at all on request {REQUEST}")
        return
    print(f"{len(rows)} assumption(s):\n")
    for r in rows:
        mark = "CONFIRMED" if r["approved"] else "not confirmed"
        shown = f"{r['value']!s:<18} {r['unit']:<6}"
        print(f"  {r['name']:<28} {shown} {mark:<14} by={r['proposed_by']}")


asyncio.run(main())
