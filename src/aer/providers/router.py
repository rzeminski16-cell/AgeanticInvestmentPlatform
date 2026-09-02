"""Role to model: the single choke point for every model call.

**No model identifier appears at a call site.** An agent asks for a role — ``planner``,
``red_team``, ``source_triage`` — and this module answers with a model and an effort level
from configuration.

That indirection is worth one extra lookup for three reasons.

**Cost is a configuration decision.** The difference between routing ``source_triage`` to
Haiku and to Opus is roughly thirty-fold on a step that runs dozens of times per report.
Making that a config edit rather than a code change is what keeps a £100/month budget
adjustable without touching the agents.

**"Which model produced this?" has one answer per role.** With identifiers at call sites,
the answer is one per file, and it drifts. Every ``agent_run`` row records the role and the
resolved model, so a report's provenance says which model wrote which section.

**A missing route fails loudly.** An agent asking for a role nobody configured raises
rather than falling back to a default. A silent default is how a run ends up costing
thirty times what the operator expected, and looking entirely normal while it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aer.config import ModelRoute, Settings
from aer.errors import ConfigError

__all__ = ["AgentRole", "ModelChoice", "Router"]

# The roles the platform routes. A closed vocabulary rather than free strings: a typo in a
# role name should be a startup error, not a silent fallback to whatever the default was.
AgentRole = str

_KNOWN_ROLES: Final[frozenset[str]] = frozenset(
    {
        "planner",
        # The plan's adversary (ADR 0091): the red team's posture, one step earlier.
        "plan_critic",
        "source_triage",
        "extraction",
        "analysis",
        "assumption_proposal",
        "valuation_interpretation",
        "red_team",
        "validator",
        "custom_section",
        "report_writer",
        "peer_proposal",
        "theme_proposal",
        # The authored half of the review verdict (ADR 0087): one sentence over a frozen
        # subject, so the cheapest route is the right default.
        "verdict",
        # The cheaper route a descriptive section's definition row may name (gap O1).
        # A route, not a capability: the writer keeps the report_writer role's registry
        # definition and only its bill changes.
        "section_writer_workhorse",
        # What each side of an unsettled challenge assumes and implies (ADR 0095). Six
        # short fields over arguments that have already stopped changing, so the cheapest
        # route is the right default — it decides nothing and reaches no report.
        "challenge_brief",
        # One premise read against the facts that arrived after it was written (ADR 0079).
        # Code has already measured the crossing; the model interprets within the bounds
        # it is handed (ADR 0103).
        "thesis_monitor",
        # The model that carries a web search (ADR 0092). A route, not an agent role: the
        # call runs one server-side search and code reads the listing, so the model's
        # only judgement is none at all — which is why the default is the cheapest model.
        "web_search",
    }
)


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """Which model, at what effort, for which role."""

    role: str
    model: str
    effort: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "model": self.model, "effort": self.effort}


class Router:
    """Resolves a role to a model. Constructed once and passed down.

    A class rather than a module function so that a test can route every role to a cheap
    model without mutating global configuration, and so the settings dependency is visible
    in the signature of whatever holds one.
    """

    __slots__ = ("_routes",)

    def __init__(self, settings: Settings) -> None:
        self._routes: dict[str, ModelRoute] = dict(settings.model_routes)

    def resolve(self, role: str) -> ModelChoice:
        """The model and effort configured for a role.

        Raises:
            ConfigError: If the role has no route. Deliberately not a fallback: an
                unrouted role silently served by an expensive model is exactly the failure
                that makes a budget cap useless.
        """
        route = self._routes.get(role)
        if route is None:
            known = ", ".join(sorted(self._routes))
            message = (
                f"No model route is configured for the role {role!r}. Configured roles: "
                f"{known}. A role with no route must not fall back to a default — a "
                "silent default is how a run costs thirty times what was expected."
            )
            raise ConfigError(message, context={"role": role, "configured": sorted(self._routes)})

        return ModelChoice(role=role, model=route.model, effort=route.effort)

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(self._routes)

    def unknown_roles(self) -> frozenset[str]:
        """Configured roles this platform does not recognise.

        A route for ``planer`` is a typo that would otherwise sit in configuration doing
        nothing while the real ``planner`` role failed to resolve. Surfaced at startup.
        """
        return frozenset(self._routes) - _KNOWN_ROLES

    def missing_roles(self) -> frozenset[str]:
        """Recognised roles with no route configured."""
        return _KNOWN_ROLES - frozenset(self._routes)
