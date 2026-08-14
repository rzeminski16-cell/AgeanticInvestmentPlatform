"""The wire contract, against the real API. **Billable, and excluded from every default run.**

`tests/test_anthropic_provider.py` asserts the request shape against the SDK's own types with
the client stubbed, which is the only layer where a mistake is catchable for free. Its
docstring has always named this file as the complement — *"the only thing that can prove the
contract has not moved underneath us"* — and until now this file did not exist. The gap cost a
live report: the batch path was sending `output_format`, a field the API deprecated in favour
of `output_config.format`, and nothing offline could see it because the fake provider is an
alternative implementation of the protocol rather than a fake transport. It never sees a
payload at all.

The Batches API made it worse by validating at result-fetch time rather than at submission, so
the mistake surfaced as an errored *item* after the whole step had run, on the far side of an
hour of work and about five pounds of drafting.

**Both request paths, and nothing else.** No agent, no workflow, no evidence — those have
offline tests that are cheaper and stricter. What only the network can answer is whether the
two payloads this codebase builds are still payloads the API accepts. The models are the
cheap ones and the answers are a few tokens; a full pass is a fraction of a penny.

Run with `just test-live` (or `pytest -m live_llm`). `pyproject.toml` excludes the marker from
`addopts`, so an ordinary run cannot reach this file even by naming it.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel, ConfigDict, Field

from aer.providers.anthropic import AnthropicProvider
from aer.providers.protocol import BatchRequest, Message

pytestmark = pytest.mark.live_llm

# Read at import, which happens during collection — before `hermetic_environment` strips every
# `AER_*` variable for the duration of each test. That fixture is what keeps the offline suite
# from ever reaching a real credential, and it is not being worked around here so much as
# stepped in front of, once, deliberately, in the one file that needs a key.
_API_KEY = os.environ.get("AER_ANTHROPIC_API_KEY", "").strip()

# Cheap on both paths, and different on purpose: haiku takes no `effort` parameter, sonnet
# does, so between them the two calls prove the payload is accepted with the key and without
# it. `low` keeps thinking short — the answer wanted here is four words.
SINGLE_MODEL = "claude-haiku-4-5"
BATCH_MODEL = "claude-sonnet-5"

# A live batch is asynchronous by contract (results within 24h) even though a single small
# item is normally back in under a minute. Bounded so a stuck batch fails the test rather
# than holding the terminal an operator is waiting at.
BATCH_DEADLINE_SECONDS = 15 * 60

SYSTEM = "You answer with the shortest true answer."
QUESTION = "What is the capital city of France? Answer with the city name only."


class Answer(BaseModel):
    """Deliberately carries the constraint kinds the API's schema mode rejects outright.

    `max_length` and `ge`/`le` are exactly what `transform_schema` has to move into
    descriptions before sending. A schema without them would pass whether or not the
    translation still happened, which would make this test agree with a 400.
    """

    model_config = ConfigDict(extra="forbid")

    city: str = Field(min_length=1, max_length=60)
    confidence: float = Field(ge=0, le=1)


def _provider() -> AnthropicProvider:
    if not _API_KEY:
        pytest.skip("AER_ANTHROPIC_API_KEY is not set; the live contract cannot be checked")
    return AnthropicProvider(api_key=_API_KEY, batch_deadline_seconds=BATCH_DEADLINE_SECONDS)


class TestTheApiStillAcceptsWhatWeSend:
    async def test_a_single_structured_call_comes_back_validated_and_metered(self) -> None:
        result = await _provider().complete_structured(
            Answer,
            system=SYSTEM,
            messages=[Message(role="user", content=QUESTION)],
            model=SINGLE_MODEL,
            effort="low",
            max_tokens=2_048,
        )

        assert "Paris" in result.value.city
        # The usage is the reason the schema goes to the wire as a dict rather than as the
        # class: handed the class, the SDK validates during accumulation and can raise out of
        # a stream whose usage is still the placeholder, so a billed call writes no cost row.
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0
        assert result.usage.stop_reason == "end_turn"
        assert result.request_payload["model"] == SINGLE_MODEL

    async def test_a_batch_call_is_accepted_with_the_schema_under_output_config(self) -> None:
        """A30's regression check, and the one that had no offline equivalent.

        The failure this reproduces was not a rejection at submission — it was an accepted
        batch whose single item came back `errored` when the results were read, saying
        `output_format: This field is deprecated`. So the assertion that matters is simply
        that a result arrives at all: reaching `results[0]` means the item succeeded, and the
        item succeeding means the field names were the current ones.
        """
        results = await _provider().complete_structured_batch(
            Answer,
            requests=[
                BatchRequest(system=SYSTEM, messages=(Message(role="user", content=QUESTION),))
            ],
            model=BATCH_MODEL,
            effort="low",
            max_tokens=2_048,
        )

        assert len(results) == 1
        assert "Paris" in results[0].value.city
        assert results[0].usage.output_tokens > 0
        # `effort` is sent for this model, so a fix that had assigned `output_config` outright
        # would have dropped it — silently, and only on the batch path.
        assert results[0].request_payload["output_config"]["effort"] == "low"
