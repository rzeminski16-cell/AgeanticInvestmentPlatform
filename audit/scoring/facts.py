"""Ground truth: the subject's filed figures, read straight from the archived companyfacts.

    uv run python -m audit.scoring.facts audit/out/msft1

The platform's `acquire` step records the SHA-256 of the SEC companyfacts document it
archived; this reads those bytes back from the artefact store and builds a small basket of
annual figures with its own arithmetic — no platform parser, no platform selection — so the
platform's stored facts and calculations can be checked against something it did not
produce. The selection rule is the same one ADR 0010 states (the latest filing on or before
the as-of date wins), applied independently, and the original filing's value is kept beside
it so a restatement is visible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from aer.config import load_settings
from aer.storage.local import LocalArtefactStore

__all__ = ["Truth", "build_truth", "load_truth"]

# Concept -> the tags that carry it, US GAAP first, IFRS after. The first tag with a
# value for a fiscal year wins; the tag that answered is recorded.
TAGS: Final[dict[str, tuple[str, ...]]] = {
    "revenue": (
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:SalesRevenueNet",
        "ifrs-full:Revenue",
    ),
    "gross_profit": ("us-gaap:GrossProfit", "ifrs-full:GrossProfit"),
    "operating_income": (
        "us-gaap:OperatingIncomeLoss",
        "ifrs-full:ProfitLossFromOperatingActivities",
    ),
    "net_income": (
        "us-gaap:NetIncomeLoss",
        "us-gaap:ProfitLoss",
        "ifrs-full:ProfitLoss",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
    ),
    "operating_cash_flow": (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities",
    ),
    "capex": (
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ),
    "cash": ("us-gaap:CashAndCashEquivalentsAtCarryingValue", "ifrs-full:CashAndCashEquivalents"),
    "long_term_debt": (
        "us-gaap:LongTermDebtNoncurrent",
        "us-gaap:LongTermDebt",
        "ifrs-full:NoncurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        "ifrs-full:Borrowings",
    ),
    "total_assets": ("us-gaap:Assets", "ifrs-full:Assets"),
    "equity": (
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:Equity",
        "ifrs-full:EquityAttributableToOwnersOfParent",
    ),
    "research_and_development": (
        "us-gaap:ResearchAndDevelopmentExpense",
        "ifrs-full:ResearchAndDevelopmentExpense",
    ),
    "eps_diluted": ("us-gaap:EarningsPerShareDiluted", "ifrs-full:DilutedEarningsLossPerShare"),
    "diluted_shares": (
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
        "ifrs-full:WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
    "shares_outstanding": (
        "dei:EntityCommonStockSharesOutstanding",
        "us-gaap:CommonStockSharesOutstanding",
    ),
    "net_interest_income": ("us-gaap:InterestIncomeExpenseNet",),
    "deposits": ("us-gaap:Deposits",),
    "loans": ("us-gaap:LoansAndLeasesReceivableNetReportedAmount", "us-gaap:NotesReceivableNet"),
    "dividends": (
        "us-gaap:PaymentsOfDividendsCommonStock",
        "us-gaap:PaymentsOfDividends",
        "ifrs-full:DividendsPaidClassifiedAsFinancingActivities",
    ),
    "buybacks": (
        "us-gaap:PaymentsForRepurchaseOfCommonStock",
        "ifrs-full:PaymentsForPurchaseOfTreasuryShares",
    ),
}
_INSTANT: Final = frozenset(
    {"cash", "long_term_debt", "total_assets", "equity", "shares_outstanding", "deposits", "loans"}
)
_ANNUAL_FORMS: Final = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F"})


@dataclass(frozen=True, slots=True)
class Observation:
    concept: str
    tag: str
    fiscal_year: int
    end: str
    value: Decimal
    unit: str
    filed: str
    form: str
    accession: str
    original_value: Decimal | None
    original_filed: str | None


@dataclass(frozen=True, slots=True)
class Truth:
    cik: str
    entity: str
    as_of: str
    artefact_sha256: str
    observations: tuple[Observation, ...]
    derived: dict[str, dict[str, str]]

    def value(self, concept: str, fiscal_year: int) -> Decimal | None:
        for obs in self.observations:
            if obs.concept == concept and obs.fiscal_year == fiscal_year:
                return obs.value
        derived = self.derived.get(concept, {})
        raw = derived.get(f"FY{fiscal_year}")
        return Decimal(raw) if raw is not None else None

    def periods(self) -> tuple[int, ...]:
        return tuple(sorted({o.fiscal_year for o in self.observations}))


def _duration_days(obs: dict[str, Any]) -> int | None:
    start, end = obs.get("start"), obs.get("end")
    if not start or not end:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _pick(
    units: dict[str, list[dict[str, Any]]], *, instant: bool, as_of: date
) -> dict[int, tuple[dict[str, Any], dict[str, Any] | None, str]]:
    """Per fiscal year: the latest-filed annual observation on or before as-of, the original, the unit."""
    chosen: dict[int, tuple[dict[str, Any], dict[str, Any] | None, str]] = {}
    for unit, observations in units.items():
        if unit not in {"USD", "GBP", "EUR", "shares", "USD/shares", "GBP/shares", "pure"}:
            continue
        by_year: dict[int, list[dict[str, Any]]] = {}
        for obs in observations:
            if obs.get("form") not in _ANNUAL_FORMS or obs.get("fp") != "FY":
                continue
            if date.fromisoformat(obs["filed"]) > as_of:
                continue
            if not instant:
                days = _duration_days(obs)
                if days is None or not 340 <= days <= 380:
                    continue
            fy = obs.get("fy")
            if not isinstance(fy, int):
                continue
            # The fiscal year the period *ends in* is what the platform and a reader mean by
            # FY2025; companyfacts' `fy` is the filing's fiscal year, which for a
            # comparative column names the later year.
            period_year = date.fromisoformat(obs["end"]).year
            by_year.setdefault(period_year, []).append(obs)
        for year, rows in by_year.items():
            rows.sort(key=lambda o: (o["filed"], o.get("accn", "")))
            latest, original = rows[-1], rows[0]
            existing = chosen.get(year)
            if existing is None or latest["filed"] > existing[0]["filed"]:
                chosen[year] = (latest, original if original is not latest else None, unit)
    return chosen


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _derive(observations: list[Observation]) -> dict[str, dict[str, str]]:
    by = {(o.concept, o.fiscal_year): o.value for o in observations}
    years = sorted({o.fiscal_year for o in observations})
    out: dict[str, dict[str, str]] = {}

    def put(name: str, year: int, value: Decimal | None) -> None:
        if value is not None:
            out.setdefault(name, {})[f"FY{year}"] = str(value)

    for year in years:
        rev, gp, oi, ni = (
            by.get(("revenue", year)),
            by.get(("gross_profit", year)),
            by.get(("operating_income", year)),
            by.get(("net_income", year)),
        )
        cfo, capex, eq = (
            by.get(("operating_cash_flow", year)),
            by.get(("capex", year)),
            by.get(("equity", year)),
        )
        if rev:
            put("gross_margin", year, gp / rev if gp is not None else None)
            put("operating_margin", year, oi / rev if oi is not None else None)
            put("net_margin", year, ni / rev if ni is not None else None)
            prev = by.get(("revenue", year - 1))
            put("revenue_growth", year, (rev / prev - 1) if prev else None)
        if cfo is not None and capex is not None:
            put("free_cash_flow", year, cfo - abs(capex))
        if ni is not None and eq:
            put("roe", year, ni / eq)
        prev_ni = by.get(("net_income", year - 1))
        if ni is not None and prev_ni:
            put("net_income_growth", year, ni / prev_ni - 1)
    return out


async def build_truth(directory: Path) -> Truth:
    export = json.loads((directory / "run-export.json").read_text(encoding="utf-8"))
    acquire = next(s.get("output") or {} for s in export["steps"] if s["key"] == "acquire")
    sha = str(acquire["artefact_sha256"])
    as_of = date.fromisoformat(export["subject"]["as_of_date"])
    settings = load_settings()
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    payload = json.loads((await store.read(sha)).decode("utf-8"))
    facts = payload.get("facts", {})
    observations: list[Observation] = []
    for concept, tags in TAGS.items():
        instant = concept in _INSTANT
        for tag in tags:
            taxonomy, _, name = tag.partition(":")
            units = facts.get(taxonomy, {}).get(name, {}).get("units")
            if not units:
                continue
            picked = _pick(units, instant=instant, as_of=as_of)
            for year, (latest, original, unit) in picked.items():
                if any(o.concept == concept and o.fiscal_year == year for o in observations):
                    continue
                value = _dec(latest.get("val"))
                if value is None:
                    continue
                observations.append(
                    Observation(
                        concept=concept,
                        tag=tag,
                        fiscal_year=year,
                        end=str(latest.get("end")),
                        value=value,
                        unit=unit,
                        filed=str(latest.get("filed")),
                        form=str(latest.get("form")),
                        accession=str(latest.get("accn", "")),
                        original_value=_dec(original.get("val")) if original else None,
                        original_filed=str(original.get("filed")) if original else None,
                    )
                )
            if any(o.concept == concept for o in observations):
                break
    observations.sort(key=lambda o: (o.concept, o.fiscal_year))
    truth = Truth(
        cik=str(payload.get("cik", acquire.get("cik", ""))),
        entity=str(payload.get("entityName", "")),
        as_of=as_of.isoformat(),
        artefact_sha256=sha,
        observations=tuple(observations),
        derived=_derive(observations),
    )
    (directory / "truth.json").write_text(
        json.dumps(
            {**asdict(truth), "observations": [asdict(o) for o in truth.observations]},
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return truth


def load_truth(path: Path) -> Truth:
    data = json.loads(path.read_text(encoding="utf-8"))
    observations = tuple(
        Observation(
            concept=o["concept"],
            tag=o["tag"],
            fiscal_year=int(o["fiscal_year"]),
            end=o["end"],
            value=Decimal(str(o["value"])),
            unit=o["unit"],
            filed=o["filed"],
            form=o["form"],
            accession=o["accession"],
            original_value=Decimal(str(o["original_value"]))
            if o.get("original_value") is not None
            else None,
            original_filed=o.get("original_filed"),
        )
        for o in data["observations"]
    )
    return Truth(
        cik=data["cik"],
        entity=data["entity"],
        as_of=data["as_of"],
        artefact_sha256=data["artefact_sha256"],
        observations=observations,
        derived=data["derived"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    truth = asyncio.run(build_truth(args.directory))
    latest = max(truth.periods()) if truth.periods() else None
    print(
        f"{truth.entity} CIK {truth.cik}: {len(truth.observations)} annual observations over FY{truth.periods()[0] if truth.periods() else '?'}-FY{latest}"
    )
    for concept in (
        "revenue",
        "operating_income",
        "net_income",
        "operating_cash_flow",
        "capex",
        "cash",
        "equity",
        "shares_outstanding",
    ):
        if latest is not None:
            print(f"  {concept:>22} FY{latest}: {truth.value(concept, latest)}")
    for name in (
        "gross_margin",
        "operating_margin",
        "net_margin",
        "revenue_growth",
        "free_cash_flow",
    ):
        print(f"  {name:>22}: {truth.derived.get(name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
