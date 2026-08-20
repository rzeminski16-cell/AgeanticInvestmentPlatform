"""Three statements assembled from facts, and the identities that say whether they hold.

A ratio suite needs lines, not a bag of facts. This turns the latter into the former: given
the canonical facts for one company and one period, it produces an income statement, a
balance sheet and a cash-flow statement whose every line is a
:class:`~aer.calc.units.Quantity` still carrying the fact it came from.

**A missing line is absent, never zero.** A filer that did not report inventory has no
inventory line, and every ratio depending on it is correspondingly absent with a reason. The
alternative -- defaulting to zero -- produces a current ratio that is arithmetically fine and
factually invented, and nothing downstream can tell it from a real one.

**A derived line says it was derived.** Where a subtotal is absent but its components are
present, it is computed -- and :attr:`Line.derived` records that it was, because a reported
gross profit and one this module worked out are different evidence. The computation goes
through ``@traced`` like everything else, so the derived line's provenance points at a
calculation whose inputs point at the facts.

**The identities are output, not assertions.** Assets less liabilities less equity should be
zero and often is not: a filer's tagging is incomplete, a concept this project maps is one
the filer split three ways, a rounding convention differs between statements. Raising would
end a run over a condition that is normal and informative; returning a discrepancy an
operator can see is what the finished report needs anyway. **A statement that does not close
is a finding**, and `docs/PLAN.md` section 2.4 escalates it at gate 2 rather than hiding it.

Pure and side-effect free -- no database, no clock. The service layer loads the facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import Quantity, Unit

__all__ = [
    "BALANCE_SHEET_LINES",
    "CASH_FLOW_LINES",
    "INCOME_STATEMENT_LINES",
    "SUPPLEMENTARY_LINES",
    "TOLERANCE",
    "IdentityCheck",
    "Line",
    "Statement",
    "StatementSet",
    "Statements",
    "assemble",
    "money_unit_of",
    "subtotal_difference",
    "subtotal_sum",
]


class Statements(StrEnum):
    """Which statement a line belongs to."""

    INCOME = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"

    SUPPLEMENTARY = "supplementary"
    """Share counts and per-share data. Not one of the three statements, but every canonical
    concept has to land somewhere -- see :attr:`StatementSet.unplaced` for why."""


# The lines each statement carries, in the order they are presented. Order is data rather
# than a sort, because a balance sheet is read top to bottom and "alphabetical" is not a
# presentation any reader expects.
INCOME_STATEMENT_LINES: Final[tuple[str, ...]] = (
    "revenue",
    # A depository's leading lines (gap A62). Between revenue and cost of revenue
    # because that is where a bank's statement puts them; an industrial filer simply
    # never reports them, and an absent line renders nothing.
    "interest_and_dividend_income",
    "net_interest_income",
    "provision_for_credit_losses",
    "noninterest_income",
    "cost_of_revenue",
    "gross_profit",
    "sg_and_a",
    "research_and_development",
    "operating_expenses",
    "impairment",
    "restructuring_costs",
    "operating_income",
    "interest_income",
    "interest_expense",
    "pre_tax_income",
    "income_tax_expense",
    "net_income",
    "noncontrolling_interest_income",
    "preferred_dividends",
    "earnings_per_share_basic",
    "earnings_per_share_diluted",
)

BALANCE_SHEET_LINES: Final[tuple[str, ...]] = (
    "cash_and_equivalents",
    "short_term_investments",
    "accounts_receivable",
    "inventory",
    "current_assets",
    "property_plant_and_equipment",
    "goodwill",
    "intangible_assets",
    "noncurrent_assets",
    "assets",
    "accounts_payable",
    "accrued_liabilities",
    "deferred_revenue",
    "short_term_debt",
    "current_liabilities",
    "long_term_debt",
    "lease_liabilities",
    "noncurrent_liabilities",
    "liabilities",
    "total_debt",
    "retained_earnings",
    "treasury_stock",
    "noncontrolling_interests",
    "equity",
)

CASH_FLOW_LINES: Final[tuple[str, ...]] = (
    "operating_cash_flow",
    "depreciation_and_amortisation",
    "share_based_compensation",
    "deferred_income_tax_expense",
    "change_in_working_capital",
    "investing_cash_flow",
    "capital_expenditure",
    "financing_cash_flow",
    "share_repurchases",
    "dividends_paid",
    "proceeds_from_debt",
    "repayments_of_debt",
    "interest_paid",
    "income_taxes_paid",
    "effect_of_exchange_rate_on_cash",
    "net_change_in_cash",
)

# Share counts and per-share figures. They sit beside the statements rather than in them,
# but they are canonical concepts and a fact carrying one must not fall through the floor.
SUPPLEMENTARY_LINES: Final[tuple[str, ...]] = (
    "shares_outstanding",
    "basic_shares_outstanding",
    "diluted_shares_outstanding",
    "dividends_per_share",
)

# How far an identity may miss and still be called closed, as a fraction of the larger side.
#
# Not zero. Filings round to the nearest million and the roundings do not cancel, so an exact
# comparison would report every real balance sheet as broken and the check would be switched
# off within a week. Ten basis points is far below anything that indicates a mapping error
# and far above presentation rounding on any figure a filing states.
TOLERANCE: Final = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a statement.

    ``quantity`` is ``None`` for a line the filer did not report and this module could not
    derive. That is a fact about the filing and is carried as one -- see the module docstring
    on why it is not zero.
    """

    concept: str
    quantity: Quantity | None
    derived: bool = False

    # Why the line is absent, in words, for the operator who wants to know whether it is a
    # gap in the filing or a gap in the concept map.
    absent_because: str = ""

    @property
    def present(self) -> bool:
        return self.quantity is not None

    @property
    def value(self) -> Decimal | None:
        return self.quantity.value if self.quantity is not None else None


@dataclass(frozen=True, slots=True)
class IdentityCheck:
    """An accounting identity, and by how much it missed.

    ``holds`` is the verdict at :data:`TOLERANCE`. ``difference`` is kept whatever the
    verdict, because "out by three pounds" and "out by three billion pounds" are the same
    boolean and completely different problems.
    """

    name: str
    statement: Statements
    expected: str
    difference: Decimal | None
    scale: Decimal | None

    # Concepts the check needed and did not have, or the reason it could not be run. A check
    # that could not run is not a check that passed, and this is what distinguishes them.
    missing: tuple[str, ...] = ()

    @property
    def ran(self) -> bool:
        return self.difference is not None and self.scale is not None

    @property
    def holds(self) -> bool:
        """Whether the identity closed. ``False`` when it could not run at all."""
        relative = self.relative
        if relative is None:
            # Either the check did not run, or both sides are zero -- which closes exactly.
            return self.ran and self.difference == 0
        return relative <= TOLERANCE

    @property
    def relative(self) -> Decimal | None:
        """The miss as a fraction of the larger side, or ``None`` if that is undefined."""
        if self.difference is None or self.scale is None or self.scale == 0:
            return None
        return abs(self.difference) / abs(self.scale)

    def describe(self) -> str:
        if not self.ran:
            reason = ", ".join(self.missing) or "inputs"
            return f"{self.name}: not checked -- missing {reason}"
        verdict = "holds" if self.holds else "DOES NOT HOLD"
        return f"{self.name} ({self.expected}): {verdict}, out by {self.difference}"


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement: its lines in presentation order, present and absent alike."""

    kind: Statements
    lines: tuple[Line, ...]

    def get(self, concept: str) -> Quantity | None:
        """The quantity on a line, or ``None`` if it is absent."""
        for line in self.lines:
            if line.concept == concept:
                return line.quantity
        return None

    @property
    def present_concepts(self) -> tuple[str, ...]:
        return tuple(line.concept for line in self.lines if line.present)

    @property
    def absent_concepts(self) -> tuple[str, ...]:
        return tuple(line.concept for line in self.lines if not line.present)

    @property
    def coverage(self) -> Decimal:
        """The share of this statement's lines that were reported or derived.

        Shown to an operator rather than gated on. A filer reporting 40% of a standard
        income statement is unusual and worth knowing about; it is not automatically wrong,
        because half these lines do not apply to half of all companies.
        """
        if not self.lines:
            return Decimal(0)
        return Decimal(len(self.present_concepts)) / Decimal(len(self.lines))


@dataclass(frozen=True, slots=True)
class StatementSet:
    """The three statements for one period, and whether they close."""

    income: Statement
    balance_sheet: Statement
    cash_flow: Statement
    supplementary: Statement
    identities: tuple[IdentityCheck, ...]

    # Facts that arrived under a key no line carries. Every canonical concept has a line --
    # `test_statements.py` fails if one does not -- so anything here is a key that is not a
    # canonical concept, which means a caller passed raw tags or invented a name. Kept rather
    # than dropped, because silently discarding a fact is how a figure goes missing without
    # anybody being told.
    unplaced: tuple[str, ...] = ()

    @property
    def statements(self) -> tuple[Statement, ...]:
        return (self.income, self.balance_sheet, self.cash_flow, self.supplementary)

    @property
    def failed_identities(self) -> tuple[IdentityCheck, ...]:
        """The identities that ran and did not hold. What gate 2 shows."""
        return tuple(check for check in self.identities if check.ran and not check.holds)

    @property
    def unchecked_identities(self) -> tuple[IdentityCheck, ...]:
        """The identities that could not run. Silence here is not agreement."""
        return tuple(check for check in self.identities if not check.ran)

    def get(self, concept: str) -> Quantity | None:
        for statement in self.statements:
            found = statement.get(concept)
            if found is not None:
                return found
        return None


# -- The two arithmetic helpers, traced like everything else ----------------------------------


@traced(
    name="subtotal_sum",
    formula="subtotal = a + b",
    assumptions=("Both components are stated in the same unit and cover the same period.",),
)
def subtotal_sum(_context: CalculationContext, *, a: Quantity, b: Quantity) -> Quantity:
    """Two lines added, for a subtotal a filer did not state."""
    return a + b


@traced(
    name="subtotal_difference",
    formula="subtotal = minuend - subtrahend",
    assumptions=("Both terms are stated in the same unit and cover the same period.",),
)
def subtotal_difference(
    _context: CalculationContext, *, minuend: Quantity, subtrahend: Quantity
) -> Quantity:
    """One line less another, for a subtotal a filer did not state."""
    return minuend - subtrahend


# Subtotals this module will work out when the filer did not state them.
#
# **Deliberately short.** Every entry holds by definition in both GAAP and IFRS, so a filer
# who stated the components has stated the subtotal. Anything requiring a judgement about
# what a filer put in a line is not here, because a derived line that is subtly wrong is
# worse than an absent one: the absent one is visible.
_DERIVED_DIFFERENCES: Final[tuple[tuple[str, str, str], ...]] = (
    ("gross_profit", "revenue", "cost_of_revenue"),
)

# `pre_tax_income = net_income + income_tax_expense` because tax is stated as a positive
# charge under the sign convention in `aer.core.concepts`; adding it back is what un-taxes
# the figure. Writing it as a subtraction of a negated term would be the same arithmetic
# dressed up to look more like the accounting.
_DERIVED_SUMS: Final[tuple[tuple[str, str, str], ...]] = (
    ("pre_tax_income", "net_income", "income_tax_expense"),
    ("total_debt", "short_term_debt", "long_term_debt"),
)


@dataclass(frozen=True, slots=True)
class _Identity:
    """One accounting identity: two sides that should reach the same figure."""

    name: str
    statement: Statements
    expected: str
    left: tuple[str, ...]
    right: tuple[str, ...]

    # Terms on the right that count as nil when the filer did not report them.
    #
    # **The one exception to "absent is never zero", and it is declared per identity rather
    # than inferred.** It exists for exactly one shape of line: the currency effect on cash,
    # which a filer with no foreign cash omits *because* it is nil. Treating it as missing
    # would mean the roll-forward never ran for a domestic filer, which is the majority of
    # them. Nothing may be added here without the same argument holding.
    nil_when_absent: tuple[str, ...] = ()

    @property
    def concepts(self) -> tuple[str, ...]:
        return (*self.left, *self.right)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(c for c in self.concepts if c not in self.nil_when_absent)


# Every identity is a plain sum of stated totals and balances on each side, which is why
# `_total` adds without consulting the sign convention. Concepts reported as a positive
# magnitude whatever their direction -- capital expenditure, dividends paid, treasury stock --
# are deliberately absent from every side here; adding one would need a decision about its
# sign, and `test_statements.py` fails if one appears without that decision being made.
_IDENTITIES: Final[tuple[_Identity, ...]] = (
    _Identity(
        name="balance sheet balances",
        statement=Statements.BALANCE_SHEET,
        expected="assets = liabilities + equity",
        left=("assets",),
        right=("liabilities", "equity"),
    ),
    _Identity(
        name="assets split",
        statement=Statements.BALANCE_SHEET,
        expected="assets = current assets + non-current assets",
        left=("assets",),
        right=("current_assets", "noncurrent_assets"),
    ),
    _Identity(
        name="liabilities split",
        statement=Statements.BALANCE_SHEET,
        expected="liabilities = current liabilities + non-current liabilities",
        left=("liabilities",),
        right=("current_liabilities", "noncurrent_liabilities"),
    ),
    _Identity(
        name="total debt",
        statement=Statements.BALANCE_SHEET,
        expected="total debt = short-term debt + long-term debt",
        left=("total_debt",),
        right=("short_term_debt", "long_term_debt"),
    ),
    _Identity(
        name="gross profit",
        statement=Statements.INCOME,
        expected="gross profit = revenue - cost of revenue",
        left=("gross_profit", "cost_of_revenue"),
        right=("revenue",),
    ),
    _Identity(
        name="net income",
        statement=Statements.INCOME,
        expected="net income = pre-tax income - tax expense",
        left=("net_income", "income_tax_expense"),
        right=("pre_tax_income",),
    ),
    _Identity(
        name="cash-flow roll-forward",
        statement=Statements.CASH_FLOW,
        expected="net change in cash = operating + investing + financing + currency effect",
        left=("net_change_in_cash",),
        right=(
            "operating_cash_flow",
            "investing_cash_flow",
            "financing_cash_flow",
            "effect_of_exchange_rate_on_cash",
        ),
        nil_when_absent=("effect_of_exchange_rate_on_cash",),
    ),
)


def assemble(context: CalculationContext, facts: Mapping[str, Quantity]) -> StatementSet:
    """Build the three statements from one period's canonical facts.

    Args:
        context: Records the derivations, so a derived subtotal is traceable to the lines
            it came from.
        facts: Canonical concept to sourced quantity. Every value must carry a
            :class:`~aer.calc.units.SourceRef`; the ``@traced`` derivations refuse a value
            that does not.

    Derivation happens before the identities are checked, so a balance sheet whose total debt
    this module worked out is checked on the same footing as one where the filer stated it.
    A derived subtotal never overwrites a stated one -- where the filer gave both, the filer
    wins, and the identity check is what reports any disagreement between them.
    """
    resolved = dict(facts)
    derived: set[str] = set()

    for target, minuend, subtrahend in _DERIVED_DIFFERENCES:
        if target in resolved or minuend not in resolved or subtrahend not in resolved:
            continue
        resolved[target] = subtotal_difference(
            context, minuend=resolved[minuend], subtrahend=resolved[subtrahend]
        )
        derived.add(target)

    for target, first, second in _DERIVED_SUMS:
        if target in resolved or first not in resolved or second not in resolved:
            continue
        resolved[target] = subtotal_sum(context, a=resolved[first], b=resolved[second])
        derived.add(target)

    placed = {
        *INCOME_STATEMENT_LINES,
        *BALANCE_SHEET_LINES,
        *CASH_FLOW_LINES,
        *SUPPLEMENTARY_LINES,
    }
    return StatementSet(
        income=_statement(Statements.INCOME, INCOME_STATEMENT_LINES, resolved, derived),
        balance_sheet=_statement(Statements.BALANCE_SHEET, BALANCE_SHEET_LINES, resolved, derived),
        cash_flow=_statement(Statements.CASH_FLOW, CASH_FLOW_LINES, resolved, derived),
        supplementary=_statement(Statements.SUPPLEMENTARY, SUPPLEMENTARY_LINES, resolved, derived),
        identities=tuple(_check(identity, resolved) for identity in _IDENTITIES),
        unplaced=tuple(sorted(concept for concept in facts if concept not in placed)),
    )


# Precise about what is and is not being claimed. The line is absent because no fact arrived
# for it, which can mean the filer did not report it *or* that they reported it under a tag
# the concept map does not know. The two are indistinguishable from here -- telling them apart
# needs the filing's unmapped tags, which is what the UK financials gate shows an operator.
_ABSENT: Final = (
    "no fact for this concept -- either it was not reported, or it was reported under a tag "
    "the concept map does not know"
)


def _statement(
    kind: Statements,
    concepts: tuple[str, ...],
    resolved: Mapping[str, Quantity],
    derived: set[str],
) -> Statement:
    lines = tuple(
        Line(
            concept=concept,
            quantity=resolved.get(concept),
            derived=concept in derived,
            absent_because="" if concept in resolved else _ABSENT,
        )
        for concept in concepts
    )
    return Statement(kind=kind, lines=lines)


def _check(identity: _Identity, resolved: Mapping[str, Quantity]) -> IdentityCheck:
    """Sum both sides and report the gap, or say why the check could not run.

    The sums are ordinary arithmetic rather than traced calculations. An identity check is a
    *diagnostic about* the statements, not a figure that reaches a report, and recording six
    of them per period in the calculation ledger would bury the calculations that do.
    """
    missing = tuple(concept for concept in identity.required if concept not in resolved)
    if missing:
        return _unrunnable(identity, missing)

    present = tuple(concept for concept in identity.concepts if concept in resolved)
    units = {resolved[concept].unit for concept in present}
    if len(units) > 1:
        # Not an arithmetic failure to be raised: two lines of one statement in different
        # units is a mapping error, and reporting it as an unrunnable check sends the
        # operator to the right place. Invariant 5 forbids coercing them, and this does not.
        return _unrunnable(
            identity,
            tuple(f"{c} is in {resolved[c].unit.symbol}" for c in present),
        )

    left = _total(identity.left, resolved)
    right = _total(identity.right, resolved)
    return IdentityCheck(
        name=identity.name,
        statement=identity.statement,
        expected=identity.expected,
        difference=left - right,
        scale=max(abs(left), abs(right)),
    )


def _unrunnable(identity: _Identity, missing: tuple[str, ...]) -> IdentityCheck:
    return IdentityCheck(
        name=identity.name,
        statement=identity.statement,
        expected=identity.expected,
        difference=None,
        scale=None,
        missing=missing,
    )


def _total(concepts: tuple[str, ...], resolved: Mapping[str, Quantity]) -> Decimal:
    """Sum a side, skipping the terms an identity declared nil when absent.

    Reachable only for those declared terms: `_check` has already refused to run when any
    required concept is missing.
    """
    return sum((resolved[c].value for c in concepts if c in resolved), Decimal(0))


def money_unit_of(statement: Statement) -> Unit | None:
    """The currency this statement's monetary lines are stated in, if they agree.

    A monetary line is one whose unit is a single currency to the first power: ``USD``
    counts, while ``USD/shares`` is earnings per share and ``shares`` is a count, and
    neither says what currency the statement is in. ``None`` where nothing monetary is
    present, or where the monetary lines disagree -- the second being a mapping error worth
    surfacing rather than a value to pick between.
    """
    units = {
        line.quantity.unit
        for line in statement.lines
        if line.quantity is not None and _is_plain_currency(line.quantity.unit)
    }
    return next(iter(units)) if len(units) == 1 else None


def _is_plain_currency(unit: Unit) -> bool:
    return len(unit.dimensions) == 1 and unit.dimensions[0][1] == 1 and bool(unit.currencies)
