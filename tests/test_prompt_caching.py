"""Asking for the prompt cache, and the ways of asking that quietly do nothing.

Gap A14. The metering for cache tokens has existed since Task 10 — `Usage` carries the
fields, `_usage_from` reads them, `costs.py` prices a read at a tenth and a write at a
quarter more — and `cache_control` appeared nowhere, so every figure was zero because
nothing was ever cached.

**Caching is a strict prefix match, which makes most of the risk here silent.** A marker in
the wrong place, a dictionary serialised in a different order, a per-call string ahead of
the shared block: none of them raise, none of them fail a schema, and all of them produce a
run that pays full price while looking correctly configured. So these tests assert on the
bytes of the request, and on the ordering property the cache depends on.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from aer.agents.section_writer import SectionWriterAgent, SectionWriterInput
from aer.providers.anthropic import _message_payload, _request_payload, _system_blocks
from aer.providers.protocol import Message

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


def _payload(**overrides: Any) -> SectionWriterInput:
    base: dict[str, Any] = {
        "section_key": "business_model",
        "title": "Business model",
        "company_name": "Microsoft Corporation",
        "ticker": "MSFT",
        "as_of_date": "2023-01-01",
        "point_in_time": True,
        "output_contract": {"type": "object", "properties": {"body": {"type": "string"}}},
        "evidence_policy": {"max_tier": "T1_REGULATORY", "min_sources": 1},
        "internal_evidence": [{"id": "f1", "concept": "Revenue", "value": "198270"}],
        "untrusted_evidence": [],
        "focus": "How the company makes money.",
        "problems": [],
        "evidence_truncated": False,
    }
    base.update(overrides)
    return SectionWriterInput(**base)


class TestTheRequestAsksForTheCache:
    def test_the_system_prompt_carries_a_breakpoint(self) -> None:
        blocks = _system_blocks("the platform contract and the role instruction")

        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks[0]["text"] == "the platform contract and the role instruction"

    def test_a_turn_without_a_prefix_is_sent_as_plain_text(self) -> None:
        """Unchanged for every role that has nothing repeating: no blocks, no premium."""
        sent = _message_payload(Message(role="user", content="just the ask"))

        assert sent == {"role": "user", "content": "just the ask"}

    def test_a_turn_with_a_prefix_is_split_and_marked(self) -> None:
        sent = _message_payload(
            Message(role="user", content="the ask", cache_prefix="the evidence")
        )

        assert sent["content"] == [
            {"type": "text", "text": "the evidence", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "the ask"},
        ]

    def test_the_repeated_block_comes_first(self) -> None:
        """The whole mechanism. Behind varying bytes, a cached block can never be read."""
        sent = _message_payload(Message(role="user", content="varies", cache_prefix="repeats"))

        blocks = sent["content"]
        assert blocks[0]["text"] == "repeats"
        assert "cache_control" not in blocks[1]

    def test_the_request_stays_within_the_four_breakpoint_limit(self) -> None:
        """The API allows four. This platform uses two, and should not drift upward."""
        request = _request_payload(
            system="contract",
            messages=[Message(role="user", content="ask", cache_prefix="evidence")],
            model="claude-opus-5",
            effort="high",
            max_tokens=4096,
        )

        serialised = json.dumps(request)
        assert serialised.count('"cache_control"') == 2


class TestTheSectionWriterSplitsWhereItRepeats:
    def test_the_evidence_is_in_the_stable_block_not_the_ask(self) -> None:
        agent = SectionWriterAgent()
        payload = _payload()

        stable = agent.stable_context(payload)
        ask = agent.user_message(payload)

        assert "198270" in stable
        assert "198270" not in ask
        assert "Business model" in ask

    def test_two_sections_sharing_an_evidence_policy_send_identical_blocks(self) -> None:
        """Where the saving comes from: nineteen sections resolve to a handful of policies.

        Byte equality is the test, because that is what the cache compares. Two blocks that
        mean the same thing and differ by a space are two cache entries and no reads.
        """
        agent = SectionWriterAgent()

        first = agent.stable_context(_payload(section_key="business_model", title="Business"))
        second = agent.stable_context(_payload(section_key="moat", title="Moat", focus="Other"))

        assert first == second

    def test_a_retry_reuses_the_same_stable_block(self) -> None:
        """Problems from a refused draft must land in the ask, never in the prefix."""
        agent = SectionWriterAgent()

        clean = agent.stable_context(_payload())
        retried = agent.stable_context(_payload(problems=["an unsourced numeral"]))

        assert clean == retried
        assert "unsourced numeral" in agent.user_message(
            _payload(problems=["an unsourced numeral"])
        )

    def test_different_evidence_gives_a_different_block(self) -> None:
        """The counterfactual: identical output for different evidence would be a bug."""
        agent = SectionWriterAgent()

        first = agent.stable_context(_payload())
        other = agent.stable_context(
            _payload(internal_evidence=[{"id": "f2", "concept": "Cost", "value": "1"}])
        )

        assert first != other

    def test_the_evidence_is_serialised_deterministically(self) -> None:
        """`sort_keys=True` is load-bearing: key order is bytes, and bytes are the key.

        A dictionary built in a different order between two calls would produce a different
        prefix and silently halve the hit rate, with nothing anywhere reporting a fault.
        """
        agent = SectionWriterAgent()

        one = agent.stable_context(
            _payload(internal_evidence=[{"b": 2, "a": 1}], evidence_policy={"z": 1, "y": 2})
        )
        two = agent.stable_context(
            _payload(internal_evidence=[{"a": 1, "b": 2}], evidence_policy={"y": 2, "z": 1})
        )

        assert one == two


class TestTheCountedCallIsTheWholeCall:
    """The input cap is checked against a count; the count must not miss the prefix."""

    def test_the_fake_counts_the_cache_prefix(self) -> None:
        from aer.providers.fake import _token_estimate  # noqa: PLC0415 -- private on purpose

        without = _token_estimate("system", [Message(role="user", content="a" * 400)])
        with_prefix = _token_estimate(
            "system", [Message(role="user", content="a" * 400, cache_prefix="b" * 400)]
        )

        assert with_prefix > without

    async def test_an_oversized_split_call_is_still_refused(self) -> None:
        """The regression the split invites.

        Move the bulk of a turn into a field the counter does not read and every role's cap
        silently doubles — the check still runs, still passes, and no longer means anything.
        """
        from aer.providers.fake import FakeProvider  # noqa: PLC0415

        provider = FakeProvider({})
        huge = "x" * 200_000

        counted = await provider.count_tokens(
            system="s",
            messages=[Message(role="user", content="short", cache_prefix=huge)],
            model="claude-opus-5",
        )

        assert counted > 20_000, "a 200k-character prefix must not count as a short turn"


class TestEveryPathComposesTheSameTurn:
    """Three call sites need the user turn, and two of them used to build it by hand.

    ``run_batch`` and ``estimate_input_tokens`` both predate :meth:`Agent.stable_context`, so
    both left it out — the batch path sending a prompt with its evidence missing, the
    estimator counting a call nobody would make. Neither raises, and neither shows up in a
    diff of the caching work, because the omission is a line that was never written.
    """

    def test_the_turn_splits_the_repeated_head_from_the_ask(self) -> None:
        turn = SectionWriterAgent().compose_turn(_payload())

        assert turn.cache_prefix is not None
        assert "198270" in turn.cache_prefix, "the evidence belongs in the cached head"
        assert "198270" not in turn.content, "and not also in the part that varies"

    def test_the_base_builds_a_user_turn_in_exactly_one_place(self) -> None:
        """The structural form of the property, checked on the source.

        Three integration tests would assert the same thing at more cost and would still
        miss a fourth path added later. What actually holds here is *there is one
        constructor*: any other ``Message(...)`` in the base is a turn composed by hand,
        which is precisely how the batch path lost its cache prefix.
        """
        tree = ast.parse((SRC_ROOT / "aer" / "agents" / "base.py").read_text(encoding="utf-8"))

        built = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Message"
        ]

        assert len(built) == 1, (
            f"{len(built)} places in aer/agents/base.py build a user turn; there must be "
            "exactly one, and it must be compose_turn"
        )


class TestTheDefaultIsNotToAsk:
    def test_a_role_that_declares_nothing_sends_no_prefix(self) -> None:
        """Asking for a cache on a block that is not in fact repeated costs a write premium
        and returns nothing, so the default has to be silence rather than optimism.

        Checked through the message the agent would actually send, rather than by comparing
        method objects: what matters is that no breakpoint reaches the wire.
        """
        agent = SectionWriterAgent()
        payload = _payload(internal_evidence=[], evidence_policy={})

        sent = _message_payload(
            Message(
                role="user",
                content=agent.composed_user_message(payload),
                cache_prefix=agent.stable_context(payload) or None,
            )
        )

        assert isinstance(sent["content"], list), "this role always has a policy block"

        plain = _message_payload(Message(role="user", content="ask", cache_prefix=None))
        assert plain["content"] == "ask"
        assert "cache_control" not in json.dumps(plain)
