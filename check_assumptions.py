import asyncio, sys
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
        print(f"  {r['name']:<28} {str(r['value']):<18} {r['unit']:<6} {mark:<14} by={r['proposed_by']}")

asyncio.run(main())