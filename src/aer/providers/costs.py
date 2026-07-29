"""Turning token usage into money, exactly.

**Metering is not reporting.** These figures are what the budget cap compares against, and
a cap fed by an approximation is a cap that lets a run through at 1.4 times its ceiling. Every
number here is `Decimal`; nothing rounds until it is written to a `NUMERIC(12,6)` column,
which is six decimal places of a pound — enough that a single cheap call is not lost to
rounding and a thousand of them still sum correctly.

**Four categories, priced separately.** Input, output, cache write and cache read. The
ratios are wide: output is typically five times input, a cache read is a tenth of input,
and a cache write is a quarter more than input. A meter that treated them alike would
misreport a heavily cached run by roughly an order of magnitude — in the direction that
makes the platform look cheaper than it is, which is the direction nobody checks.

**The exchange rate is recorded on the row, not applied and forgotten.** Prices are
published in USD and the budget is in GBP. Storing only the converted figure would make
last month's costs unreconcilable the moment the configured rate changed; storing the rate
alongside makes every row self-describing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from aer.providers.protocol import Usage

__all__ = [
    "DEFAULT_PRICES",
    "CostCategory",
    "CostLine",
    "ModelPrices",
    "price_usage",
    "unknown_model_prices",
]

# Per million tokens, in USD. Published list prices as at July 2026; they change, which is
# why they are a table here and overridable from configuration rather than arithmetic
# scattered through the code.
#
# The pricing shape is the same for every current Claude model: output costs five times
# input, a cache read a tenth, a cache write a quarter more.
_MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class ModelPrices:
    """USD per million tokens, by category."""

    input_usd: Decimal
    output_usd: Decimal
    cache_read_usd: Decimal
    cache_write_usd: Decimal

    @classmethod
    def from_input_rate(cls, input_usd: str | Decimal, output_usd: str | Decimal) -> ModelPrices:
        """Derive the cache rates from the input rate, as every current model does.

        Stated as a derivation rather than four literals so that the relationship is
        visible: if a future model prices caching differently, the constructor call
        changes and the reader sees that it did.
        """
        base = Decimal(str(input_usd))
        return cls(
            input_usd=base,
            output_usd=Decimal(str(output_usd)),
            cache_read_usd=base / 10,
            cache_write_usd=base * Decimal("1.25"),
        )


DEFAULT_PRICES: Final[dict[str, ModelPrices]] = {
    # Sticker rates. Opus 5 ships at Opus 4.8's pricing; Sonnet 5 holds the $3/$15 sticker
    # (an introductory $2/$10 runs to 2026-08-31, deliberately not used here — a cap fed by
    # a promotional rate starts under-reporting on the day it lapses).
    "claude-opus-5": ModelPrices.from_input_rate("5.00", "25.00"),
    "claude-sonnet-5": ModelPrices.from_input_rate("3.00", "15.00"),
    "claude-haiku-4-5": ModelPrices.from_input_rate("1.00", "5.00"),
}


class CostCategory(StrEnum):
    """What was consumed. Matches the ``category`` column on ``costs``."""

    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    CACHE_READ = "cache_read"
    CACHE_WRITE = "cache_write"
    WEB_SEARCH = "web_search"
    DATA_API = "data_api"


@dataclass(frozen=True, slots=True)
class CostLine:
    """One priced line, ready to become a ``costs`` row."""

    category: CostCategory
    provider: str
    model: str
    units: Decimal
    unit_type: str
    amount_usd: Decimal
    amount_gbp: Decimal
    fx_rate: Decimal

    def as_dict(self) -> dict[str, str]:
        return {
            "category": self.category.value,
            "provider": self.provider,
            "model": self.model,
            "units": str(self.units),
            "unit_type": self.unit_type,
            "amount_usd": str(self.amount_usd),
            "amount_gbp": str(self.amount_gbp),
            "fx_rate": str(self.fx_rate),
        }


def unknown_model_prices(model: str) -> ModelPrices:  # noqa: ARG001 -- see the docstring
    """Prices for a model that is not in the table.

    Priced at the **most expensive** known model rather than at zero. A model nobody has
    priced is one whose cost the platform cannot verify, and the safe error is to
    overstate it: an overstatement pauses a run for a decision, while an understatement
    spends money nobody agreed to.

    ``model`` is taken and deliberately unused. It is what a caller has in hand, and
    naming it here keeps the signature stable for the day this grows a per-family
    fallback rather than one ceiling for everything.
    """
    return max(DEFAULT_PRICES.values(), key=lambda p: p.output_usd)


def price_usage(
    usage: Usage,
    *,
    provider: str,
    usd_to_gbp: Decimal,
    prices: dict[str, ModelPrices] | None = None,
) -> list[CostLine]:
    """Turn one call's usage into priced lines, one per non-zero category.

    Categories with zero usage produce no line. A row saying "zero cache reads cost zero
    pounds" is noise in a table whose whole purpose is to be summed and read.
    """
    table = prices if prices is not None else DEFAULT_PRICES
    model_prices = table.get(usage.model) or unknown_model_prices(usage.model)

    priced = (
        (CostCategory.LLM_INPUT, usage.input_tokens, model_prices.input_usd),
        (CostCategory.LLM_OUTPUT, usage.output_tokens, model_prices.output_usd),
        (CostCategory.CACHE_READ, usage.cache_read_tokens, model_prices.cache_read_usd),
        (CostCategory.CACHE_WRITE, usage.cache_write_tokens, model_prices.cache_write_usd),
    )

    lines: list[CostLine] = []
    for category, tokens, rate_per_million in priced:
        if tokens <= 0:
            continue
        units = Decimal(tokens)
        amount_usd = units * rate_per_million / _MILLION
        lines.append(
            CostLine(
                category=category,
                provider=provider,
                model=usage.model,
                units=units,
                unit_type="tokens",
                amount_usd=amount_usd,
                amount_gbp=amount_usd * usd_to_gbp,
                fx_rate=usd_to_gbp,
            )
        )
    return lines


def total_gbp(lines: list[CostLine]) -> Decimal:
    """What a set of lines comes to, in pounds."""
    return sum((line.amount_gbp for line in lines), Decimal(0))


def estimate_gbp(
    *,
    model: str,
    input_tokens: int,
    expected_output_tokens: int,
    usd_to_gbp: Decimal,
    prices: dict[str, ModelPrices] | None = None,
) -> Decimal:
    """What a call is expected to cost, before making it.

    Used for the figure shown at the approval gate and for the budget guard. ``input_tokens``
    comes from the provider's own counter — see :meth:`LLMProvider.count_tokens` — because
    an estimate from character counts is wrong by enough to make the gate misleading.
    """
    table = prices if prices is not None else DEFAULT_PRICES
    model_prices = table.get(model) or unknown_model_prices(model)

    usd = (
        Decimal(input_tokens) * model_prices.input_usd
        + Decimal(expected_output_tokens) * model_prices.output_usd
    ) / _MILLION
    return usd * usd_to_gbp
