"""The console baseline, driven offline: the model it names is the model it bills, and a
refusal voids the comparator rather than rerouting it.

No client and no key: the SDK's client is replaced with one that returns a scripted reply,
so what is exercised is everything this module does around the call — the request it
builds, the note it writes, the price it records, and what it does when the model declines.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from aer.providers.costs import DEFAULT_PRICES
from audit.baseline import anthropic_baseline as baseline
from audit.subjects import subject_for

USD_TO_GBP = Decimal("0.79")


class _Message:
    def __init__(self, *, stop_reason: str, text: str, category: str | None = None) -> None:
        self.stop_reason = stop_reason
        self.content = [SimpleNamespace(type="text", text=text, citations=[])]
        self.usage = SimpleNamespace(
            input_tokens=1_000,
            output_tokens=200,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=2),
        )
        self.stop_details = (
            SimpleNamespace(category=category, explanation="declined") if category else None
        )

    def to_dict(self) -> dict[str, Any]:
        return {"stop_reason": self.stop_reason, "text": self.content[0].text}


class _Stream:
    def __init__(self, message: _Message) -> None:
        self._message = message

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> _Message:
        return self._message


class _Client:
    """Stands in for `anthropic.AsyncAnthropic`, remembering the requests it was sent."""

    requests: ClassVar[list[dict[str, Any]]] = []
    reply: ClassVar[_Message]

    def __init__(self, **_: object) -> None:
        self.messages = self

    def stream(self, **request: Any) -> _Stream:
        type(self).requests.append(request)
        return _Stream(type(self).reply)


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> type[_Client]:
    _Client.requests = []
    monkeypatch.setattr(baseline.anthropic, "AsyncAnthropic", _Client)
    monkeypatch.setattr(
        baseline,
        "load_settings",
        lambda: SimpleNamespace(require_secret=lambda _name: "k", usd_to_gbp=USD_TO_GBP),
    )
    return _Client


async def test_the_model_named_is_the_model_asked_and_billed(
    scripted: type[_Client], tmp_path: Path
) -> None:
    scripted.reply = _Message(stop_reason="end_turn", text="# A note\n\nRevenue grew.")

    await baseline.run_baseline(
        subject_for("msft1"),
        as_of="2026-09-24",
        out_root=tmp_path,
        label="msft1-fresh",
        model="claude-opus-5-5",
    )

    out = tmp_path / "baseline" / "msft1-fresh"
    summary = json.loads((out / "summary.json").read_text())
    assert scripted.requests[0]["model"] == "claude-opus-5-5"
    assert summary["model"] == "claude-opus-5-5"
    assert (out / "note.md").read_text() == "# A note\n\nRevenue grew."

    prices = DEFAULT_PRICES["claude-opus-5-5"]
    tokens_usd = (1_000 * prices.input_usd + 200 * prices.output_usd) / Decimal(1_000_000)
    searches_usd = Decimal("0.02")
    expected = ((tokens_usd + searches_usd) * USD_TO_GBP).quantize(Decimal("0.0001"))
    assert Decimal(summary["price"]["total_gbp"]) == expected

    ledger = json.loads((tmp_path / "ledger.json").read_text())
    [row] = ledger["baseline"]
    assert row["label"] == "msft1-fresh"
    assert row["detail"]["model"] == "claude-opus-5-5"


async def test_septembers_route_is_the_default(scripted: type[_Client], tmp_path: Path) -> None:
    scripted.reply = _Message(stop_reason="end_turn", text="note")

    await baseline.run_baseline(subject_for("azn"), as_of="2026-09-24", out_root=tmp_path)

    assert scripted.requests[0]["model"] == baseline.MODEL == "claude-opus-5"
    assert scripted.requests[0]["output_config"] == {"effort": "high"}


async def test_a_refusal_voids_the_comparator_and_reroutes_nothing(
    scripted: type[_Client], tmp_path: Path
) -> None:
    scripted.reply = _Message(stop_reason="refusal", text="", category="cyber")

    with pytest.raises(baseline.BaselineRefusedError, match="nothing was rerouted"):
        await baseline.run_baseline(
            subject_for("msft1"),
            as_of="2026-09-24",
            out_root=tmp_path,
            label="msft1-fresh",
            model="claude-opus-5-5",
        )

    out = tmp_path / "baseline" / "msft1-fresh"
    refused = json.loads((out / "refused.json").read_text())
    assert refused["model"] == "claude-opus-5-5"
    assert refused["category"] == "cyber"
    assert not (out / "note.md").exists()
    assert len(scripted.requests) == 1, "a declined brief is not sent to another model"
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert [row["label"] for row in ledger["baseline"]] == ["msft1-fresh-refused"]
