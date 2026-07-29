"""The provider abstraction: conformance, routing, pricing, and the import boundary.

The last of those is the one worth stating. ``CLAUDE.md`` says only
``aer.providers.anthropic`` may import the vendor SDK, and a rule of that kind survives
exactly as long as someone checks it. :class:`TestTheImportBoundary` checks it — by
reading every source file, and by importing the application and looking at what actually
loaded.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from aer.config import load_settings
from aer.errors import ConfigError, ExternalServiceError
from aer.providers.costs import (
    DEFAULT_PRICES,
    CostCategory,
    ModelPrices,
    estimate_gbp,
    price_usage,
    total_gbp,
    unknown_model_prices,
)
from aer.providers.fake import FakeProvider, ScriptedResponse
from aer.providers.protocol import LLMProvider, Message, Usage
from aer.providers.router import Router

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# The one module permitted to import the vendor SDK.
SDK_OWNER = SRC_ROOT / "aer" / "providers" / "anthropic.py"

MILLION = 1_000_000


class Answer(BaseModel):
    verdict: str


class TestTheProtocol:
    """The fake is a real implementation, checked as one."""

    def test_the_fake_satisfies_the_protocol(self) -> None:
        assert isinstance(FakeProvider(), LLMProvider)

    def test_the_anthropic_provider_satisfies_the_protocol(self) -> None:
        """Checked without constructing it, which would need a key.

        ``isinstance`` against a runtime-checkable Protocol only inspects attribute
        presence, so this asserts the class has the methods rather than that a live client
        works. That is the right scope here: whether the real client behaves is a
        ``live_llm`` question, and whether the class satisfies the interface is not.
        """
        from aer.providers.anthropic import AnthropicProvider  # noqa: PLC0415

        for method in ("complete_structured", "count_tokens"):
            assert callable(getattr(AnthropicProvider, method))
        assert isinstance(AnthropicProvider.name, property)

    async def test_it_returns_the_scripted_object(self) -> None:
        provider = FakeProvider({"Answer": Answer(verdict="yes")})
        result = await provider.complete_structured(
            Answer, system="s", messages=[Message(role="user", content="q")], model="m"
        )
        assert result.value.verdict == "yes"

    async def test_an_unscripted_schema_is_refused_rather_than_invented(self) -> None:
        """A fake that returned a default object would make every test using it vacuous."""
        provider = FakeProvider()
        with pytest.raises(ExternalServiceError, match="no scripted response"):
            await provider.complete_structured(
                Answer, system="s", messages=[Message(role="user", content="q")], model="m"
            )

    async def test_usage_reflects_the_prompt_rather_than_being_zero(self) -> None:
        """A longer prompt must really cost more, or the budget guard is untestable."""
        provider = FakeProvider({"Answer": Answer(verdict="yes")})

        short = await provider.complete_structured(
            Answer, system="s", messages=[Message(role="user", content="q")], model="m"
        )
        long = await provider.complete_structured(
            Answer, system="s" * 4000, messages=[Message(role="user", content="q")], model="m"
        )

        assert short.usage.input_tokens > 0
        assert long.usage.input_tokens > short.usage.input_tokens

    async def test_it_records_what_it_was_asked(self) -> None:
        provider = FakeProvider({"Answer": Answer(verdict="yes")})
        await provider.complete_structured(
            Answer,
            system="be brief",
            messages=[Message(role="user", content="q")],
            model="claude-sonnet-5",
            effort="high",
        )
        assert provider.call_count == 1
        assert provider.calls[0]["model"] == "claude-sonnet-5"
        assert provider.calls[0]["effort"] == "high"

    async def test_count_tokens_does_not_count_as_a_completion(self) -> None:
        """The budget-guard tests assert ``call_count == 0``; counting must not disturb it."""
        provider = FakeProvider()
        await provider.count_tokens(
            system="s", messages=[Message(role="user", content="q")], model="m"
        )
        assert provider.call_count == 0

    async def test_a_scripted_failure_propagates(self) -> None:
        boom = ExternalServiceError("upstream is down", provider="fake", retryable=True)
        provider = FakeProvider({"Answer": Answer(verdict="yes")}, fail_with=boom)
        with pytest.raises(ExternalServiceError):
            await provider.complete_structured(
                Answer, system="s", messages=[Message(role="user", content="q")], model="m"
            )


class TestTheRouter:
    def test_every_known_role_resolves(self, settings_env: pytest.MonkeyPatch) -> None:
        router = Router(load_settings())
        assert router.missing_roles() == frozenset()
        assert router.unknown_roles() == frozenset()

    def test_a_role_resolves_to_a_model_and_an_effort(
        self, settings_env: pytest.MonkeyPatch
    ) -> None:
        choice = Router(load_settings()).resolve("planner")
        assert choice.model in DEFAULT_PRICES
        assert choice.effort in {"low", "medium", "high", "xhigh", "max"}

    def test_an_unrouted_role_raises_rather_than_defaulting(
        self, settings_env: pytest.MonkeyPatch
    ) -> None:
        """A silent default is how a run costs thirty times what was expected."""
        with pytest.raises(ConfigError, match="No model route"):
            Router(load_settings()).resolve("no_such_role")

    def test_routing_is_configuration_not_code(self, settings_env: pytest.MonkeyPatch) -> None:
        settings_env.setenv(
            "AER_MODEL_ROUTES",
            '{"planner": {"model": "claude-haiku-4-5", "effort": "low"}}',
        )
        assert Router(load_settings()).resolve("planner").model == "claude-haiku-4-5"


class TestPricing:
    """Arithmetic against published rates, stated as literals rather than derived."""

    def test_a_million_input_tokens_of_sonnet_costs_the_published_rate(self) -> None:
        usage = Usage(input_tokens=MILLION, output_tokens=0, model="claude-sonnet-5")
        lines = price_usage(usage, provider="anthropic", usd_to_gbp=Decimal(1))

        assert len(lines) == 1
        assert lines[0].category is CostCategory.LLM_INPUT
        assert lines[0].amount_usd == Decimal("3.00")

    def test_a_million_output_tokens_of_sonnet_costs_the_published_rate(self) -> None:
        usage = Usage(input_tokens=0, output_tokens=MILLION, model="claude-sonnet-5")
        lines = price_usage(usage, provider="anthropic", usd_to_gbp=Decimal(1))

        assert lines[0].category is CostCategory.LLM_OUTPUT
        assert lines[0].amount_usd == Decimal("15.00")

    def test_cache_reads_and_writes_are_priced_apart_from_input(self) -> None:
        """Folding them into input would misreport a cached run by an order of magnitude."""
        usage = Usage(
            input_tokens=MILLION,
            output_tokens=0,
            model="claude-sonnet-5",
            cache_read_tokens=MILLION,
            cache_write_tokens=MILLION,
        )
        by_category = {
            line.category: line.amount_usd
            for line in price_usage(usage, provider="anthropic", usd_to_gbp=Decimal(1))
        }

        assert by_category[CostCategory.LLM_INPUT] == Decimal("3.00")
        assert by_category[CostCategory.CACHE_READ] == Decimal("0.30")
        assert by_category[CostCategory.CACHE_WRITE] == Decimal("3.75")

    def test_a_zero_category_produces_no_line(self) -> None:
        usage = Usage(input_tokens=10, output_tokens=0, model="claude-sonnet-5")
        categories = [
            line.category for line in price_usage(usage, provider="a", usd_to_gbp=Decimal(1))
        ]
        assert categories == [CostCategory.LLM_INPUT]

    def test_the_exchange_rate_is_applied_and_recorded(self) -> None:
        usage = Usage(input_tokens=MILLION, output_tokens=0, model="claude-sonnet-5")
        line = price_usage(usage, provider="anthropic", usd_to_gbp=Decimal("0.79"))[0]

        assert line.amount_gbp == Decimal("3.00") * Decimal("0.79")
        # On the row, so last month's costs stay reconcilable when the rate changes.
        assert line.fx_rate == Decimal("0.79")

    def test_an_unknown_model_is_priced_at_the_dearest_known_one(self) -> None:
        """Overstating pauses a run for a decision; understating spends money nobody agreed to."""
        assert unknown_model_prices("claude-something-unreleased") == max(
            DEFAULT_PRICES.values(), key=lambda p: p.output_usd
        )

        # Opus 5's rate, $5/$25 — the dearest in the table.
        usage = Usage(input_tokens=MILLION, output_tokens=0, model="claude-unreleased")
        assert price_usage(usage, provider="a", usd_to_gbp=Decimal(1))[0].amount_usd == Decimal(
            "5.00"
        )

    def test_the_published_opus_rate_is_what_the_table_holds(self) -> None:
        """A literal, because the budget cap is only as honest as this number.

        The table said $15/$75 for over a month — Opus 4.7's rate, carried forward when the
        model ID changed. It overstated every planner call threefold, which is the safe
        direction and still wrong: a cap that trips at a third of the real spend stops runs
        that had money left.
        """
        assert DEFAULT_PRICES["claude-opus-5"].input_usd == Decimal("5.00")
        assert DEFAULT_PRICES["claude-opus-5"].output_usd == Decimal("25.00")

    def test_every_amount_is_a_decimal(self) -> None:
        """Money in floats is money that stops reconciling in the third decimal place."""
        usage = Usage(input_tokens=7, output_tokens=13, model="claude-opus-5", cache_read_tokens=3)
        for line in price_usage(usage, provider="a", usd_to_gbp=Decimal("0.79")):
            assert isinstance(line.amount_usd, Decimal)
            assert isinstance(line.amount_gbp, Decimal)
            assert isinstance(line.units, Decimal)

    def test_totalling_lines_sums_exactly(self) -> None:
        usage = Usage(input_tokens=333, output_tokens=333, model="claude-haiku-4-5")
        lines = price_usage(usage, provider="a", usd_to_gbp=Decimal(1))
        assert total_gbp(lines) == sum(line.amount_gbp for line in lines)

    def test_the_estimate_uses_the_same_rates_as_the_meter(self) -> None:
        """The gate's figure and the bill must come from one table, or the gate misleads."""
        estimated = estimate_gbp(
            model="claude-sonnet-5",
            input_tokens=MILLION,
            expected_output_tokens=MILLION,
            usd_to_gbp=Decimal(1),
        )
        metered = total_gbp(
            price_usage(
                Usage(input_tokens=MILLION, output_tokens=MILLION, model="claude-sonnet-5"),
                provider="anthropic",
                usd_to_gbp=Decimal(1),
            )
        )
        assert estimated == metered

    def test_cache_rates_are_derived_from_the_input_rate(self) -> None:
        prices = ModelPrices.from_input_rate("4.00", "20.00")
        assert prices.cache_read_usd == Decimal("0.4")
        assert prices.cache_write_usd == Decimal("5.00")


class TestTheImportBoundary:
    """Only ``aer.providers.anthropic`` may import the vendor SDK."""

    @staticmethod
    def _modules_importing_anthropic() -> set[Path]:
        offenders: set[Path] = set()
        for path in SRC_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name == "anthropic" or name.startswith("anthropic.") for name in names):
                    offenders.add(path)
        return offenders

    def test_no_module_outside_the_provider_imports_the_sdk(self) -> None:
        assert self._modules_importing_anthropic() <= {SDK_OWNER}

    def test_the_provider_module_really_does_import_it(self) -> None:
        """Guards the test above from passing because nothing imports the SDK at all."""
        assert SDK_OWNER in self._modules_importing_anthropic()

    def test_importing_the_application_does_not_load_the_sdk(self) -> None:
        """A subprocess, because the SDK may already be loaded in this one.

        The import is deferred into ``AnthropicProvider._build_client`` precisely so that
        a process which never makes a model call never pays for the SDK — and so that a
        deployment missing it still serves pages. Asserting on ``sys.modules`` in the test
        process could not tell the difference, because another test may have imported it.
        """
        script = "import sys; import aer.api.app; sys.exit(1 if 'anthropic' in sys.modules else 0)"
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [sys.executable, "-c", script],
            cwd=SRC_ROOT.parent,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (
            "importing aer.api.app pulled in the anthropic SDK: "
            f"{result.stdout.decode()}{result.stderr.decode()}"
        )


class TestScriptedResponse:
    def test_output_tokens_are_controllable(self) -> None:
        scripted = ScriptedResponse(Answer(verdict="yes"), output_tokens=1234)
        assert scripted.output_tokens == 1234
