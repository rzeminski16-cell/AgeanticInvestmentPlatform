"""Typed application configuration.

One validated object, built once, imported everywhere. The alternative — reading
``os.environ`` at each call site — leaks credentials into logs, lets defaults drift apart
between modules, and makes "fail fast with a clear message" impossible to add later.

Three properties this module is responsible for:

* **Secrets never render.** Every credential is a :class:`~pydantic.SecretStr`, which masks
  itself in ``repr()`` and ``str()``. A stray f-string in a log line cannot leak a key.
* **Every problem is reported at once.** A fresh machine should take one pass to configure,
  not one error per run.
* **Construction has no side effects.** Building a ``Settings`` never touches the
  filesystem; :meth:`Settings.ensure_directories` does that, explicitly, at startup.

This module lives outside ``aer.core`` because it reads the environment, and ``aer.core``
is required to stay pure.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from aer.core.dates import format_date
from aer.errors import ConfigError

__all__ = [
    "DEFAULT_MODEL_ROUTES",
    "ENV_PREFIX",
    "PROVIDER_CREDENTIAL_FIELDS",
    "SECRET_FIELDS",
    "AppEnv",
    "Effort",
    "HouseStyle",
    "ModelRoute",
    "Settings",
    "get_settings",
    "load_settings",
]

_log = logging.getLogger(__name__)

ENV_PREFIX: Final = "AER_"

AppEnv = Literal["development", "test", "production"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

_VALID_LOG_LEVELS: Final = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

# Credentials for third-party services. All optional at startup: requiredness belongs at
# the point of use, so a missing EODHD key does not stop you working on SEC ingestion.
PROVIDER_CREDENTIAL_FIELDS: Final[tuple[str, ...]] = (
    "anthropic_api_key",
    "eodhd_api_key",
    "fred_api_key",
    "companies_house_api_key",
)

# Every secret field, listed once so tests, redaction and `require_secret` agree on the
# set. `secret_key` is ours rather than a provider's, and unlike the others it is filled
# in when absent rather than left None -- see _secret_key_must_exist_in_production.
SECRET_FIELDS: Final[tuple[str, ...]] = (*PROVIDER_CREDENTIAL_FIELDS, "secret_key")

# 48 random bytes, url-safe encoded. Comfortably above the 32 bytes HMAC-SHA256 needs.
_GENERATED_SECRET_BYTES: Final = 48


class ModelRoute(BaseModel):
    """Which model answers for a given agent role, and how hard it thinks.

    Routing is configuration rather than code so that rebalancing cost against quality is
    an environment change, never an edit to the workflow engine.
    """

    model_config = {"protected_namespaces": (), "frozen": True}

    model: str
    effort: Effort = "medium"


class HouseStyle(BaseModel):
    """How the finished note presents numbers, dates and itself.

    Presentation decisions made once, here, rather than once per section by whichever
    writer happens to be drafting — the live AAPL report mixed "$39.5 billion" with
    "39,544 USD millions" on facing pages because nothing owned this choice. Applied at
    render and in the writers' style instructions; **never** to stored values. A style
    changes how a figure is displayed, and invariant 3 keeps the stored value exact
    underneath it (ADR 0056).
    """

    model_config = {"frozen": True}

    prose_money: Literal["auto", "millions"] = "auto"
    """How prose renders large money. ``auto`` switches to billions at the threshold
    below ("revenue of $109.4bn"); ``millions`` never scales ("revenue of $109,417m").
    Tables always render in millions either way — columns line up in one scale."""

    billions_from: Decimal = Field(default=Decimal("1000000000"), gt=0)
    """The magnitude, in the report currency's base units, at which ``auto`` prose
    switches to billions. At the default, $999m stays millions and $1.2bn does not."""

    date_format: str = Field(default="%-d %B %Y", validate_default=True)
    """A ``strftime`` pattern for dates in prose and headings. The default reads
    "27 December 2025" — UK order, no ordinal suffix, no zero padding.

    ``validate_default=True`` because pydantic does not validate a default otherwise, and
    the default is precisely the value that broke: it carries ``%-d``, the check below
    never ran against it, and the pattern's first contact with a ``strftime`` was inside a
    finished report."""

    voice: Literal["impersonal", "first_person_plural"] = "impersonal"
    """The note's register: ``impersonal`` writes "the evidence supports";
    ``first_person_plural`` writes "we estimate", as most sell-side research does.
    (Named ``voice`` because pydantic's ``BaseModel`` already owns ``register``.)"""

    @field_validator("date_format", mode="after")
    @classmethod
    def _pattern_formats_a_date(cls, value: str) -> str:
        """Refuse a pattern that does not actually render the date, at configuration time.

        A bad pattern would otherwise surface in the renderer, half way through a
        paid-for report. Raising is not enough to catch one: glibc passes an unknown
        directive through as literal text (``%Q`` renders as ``"%Q"``), so the check is
        behavioural — two different probe dates must render differently, or the pattern
        is ignoring the date it was given.

        Through :func:`aer.core.dates.format_date`, which is what the renderer uses. A
        check that formatted dates differently from the code it is checking would pass a
        pattern the report then dies on — which is exactly what ``%-d`` did on Windows.
        """
        probes = (date(2025, 12, 27), date(2024, 3, 1))
        try:
            rendered = [format_date(probe, value) for probe in probes]
        except ValueError as exc:
            message = f"is not a valid strftime pattern: {exc}"
            raise ValueError(message) from exc
        if rendered[0].strip() and rendered[0] != rendered[1]:
            return value
        message = "does not render the date; every date would print as the same text"
        raise ValueError(message)


# Opus 5 for judgement, Sonnet 5 as the workhorse, Haiku 4.5 for triage.
# Rationale and the cost model behind it: docs/archive/PLAN.md section 1.8.
DEFAULT_MODEL_ROUTES: Final[dict[str, ModelRoute]] = {
    "planner": ModelRoute(model="claude-opus-5", effort="high"),
    # The plan's adversary (ADR 0091). The judgement class of call, like the red team:
    # once per run, small input, and what it catches is a whole run aimed wrong.
    "plan_critic": ModelRoute(model="claude-opus-5", effort="high"),
    "source_triage": ModelRoute(model="claude-haiku-4-5", effort="low"),
    "extraction": ModelRoute(model="claude-sonnet-5", effort="medium"),
    "analysis": ModelRoute(model="claude-sonnet-5", effort="medium"),
    # Two numbers the whole valuation rests on, twice per report at most (ADR 0046).
    "assumption_proposal": ModelRoute(model="claude-opus-5", effort="high"),
    "valuation_interpretation": ModelRoute(model="claude-opus-5", effort="high"),
    "red_team": ModelRoute(model="claude-opus-5", effort="high"),
    "validator": ModelRoute(model="claude-sonnet-5", effort="medium"),
    "custom_section": ModelRoute(model="claude-sonnet-5", effort="medium"),
    "report_writer": ModelRoute(model="claude-opus-5", effort="high"),
    # Comparable companies by ticker (ADR 0059). The workhorse rather than the judgement
    # model: the answer is a short list drawn from general knowledge of the market, every
    # ticker in it is resolved against EDGAR in code, and a person confirms the set at a
    # gate — so the expensive route would be buying certainty this role is not trusted for
    # anyway.
    "peer_proposal": ModelRoute(model="claude-sonnet-5", effort="medium"),
    # Gap O1: the draft step was £5.61 of a £7.34 live run, sixteen sections on Opus at
    # high effort. Descriptive sections — their definition rows name this route — take
    # the workhorse; the judgement sections stay on report_writer's route above.
    "section_writer_workhorse": ModelRoute(model="claude-sonnet-5", effort="medium"),
    "theme_proposal": ModelRoute(model="claude-sonnet-5", effort="medium"),
}


def _normalised(path: Path) -> Path:
    """Resolve a path for comparison, tolerating parts that do not exist yet.

    ``normcase`` matters: on Windows ``C:\\Notes`` and ``c:\\notes`` are the same
    directory, and a containment check that missed that would be worse than useless
    because it would look like it was protecting something.
    """
    resolved = path.expanduser().resolve(strict=False)
    return Path(os.path.normcase(str(resolved)))


def _contains(parent: Path, child: Path) -> bool:
    """Whether ``child`` is ``parent`` or sits underneath it."""
    normalised_parent = _normalised(parent)
    normalised_child = _normalised(child)
    return normalised_child == normalised_parent or normalised_child.is_relative_to(
        normalised_parent
    )


class Settings(BaseSettings):
    """Validated application configuration, read from ``AER_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Unrecognised AER_* variables are ignored rather than rejected: docker-compose.yml
        # reads AER_POSTGRES_* from the same .env file, and those are not application
        # settings.
        extra="ignore",
        protected_namespaces=(),
    )

    # -- Identity ----------------------------------------------------------------------

    http_user_agent: str = Field(
        min_length=1,
        description=(
            "User-Agent sent on every outbound request. Required, with no default: the "
            "SEC mandates a descriptive User-Agent identifying the operator as a "
            "condition of using its APIs, and a shared placeholder default would get "
            "every user of it rate-limited or blocked together."
        ),
    )

    # -- Provider credentials ----------------------------------------------------------
    # Optional here on purpose. Requiredness belongs at the point of use, so that a
    # missing EODHD key does not stop you working on SEC ingestion. Call
    # `require_secret()` where the credential is actually needed.

    anthropic_api_key: SecretStr | None = None
    eodhd_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    companies_house_api_key: SecretStr | None = None

    # -- Signing ------------------------------------------------------------------------

    # Signs CSRF tokens, and any signed cookie added later. Left unset it is generated per
    # process; see _secret_key_must_exist_in_production for why that is safe locally and
    # refused in production.
    secret_key: SecretStr | None = None

    # -- Application -------------------------------------------------------------------

    app_env: AppEnv = "development"
    log_level: str = "INFO"
    log_json: bool = True
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8000, ge=1, le=65535)

    # -- Infrastructure ----------------------------------------------------------------

    # The embedded password is the local development credential from docker-compose.yml,
    # not a secret: the port is loopback-only and the database holds no production data.
    # Keep it in step with AER_POSTGRES_PASSWORD in docker-compose.yml if either changes.
    # Reasoning recorded in docs/adr/0004-postgres-redis-local-first.md.
    database_url: str = (
        "postgresql+asyncpg://aer:aer_local_dev@127.0.0.1:5432/aer"  # pragma: allowlist secret
    )
    redis_url: str = "redis://127.0.0.1:6379/0"

    # -- Storage -----------------------------------------------------------------------

    artefact_root: Path = Path("./var/artefacts")
    max_artefact_bytes: int = Field(default=52_428_800, gt=0)

    # -- Extraction ----------------------------------------------------------------------

    # Deliberately separate from `max_artefact_bytes` despite sharing its default. Archiving a
    # large filing is cheap and sometimes necessary; *parsing* one is where a decompression
    # bomb goes off, so the two ceilings answer different questions and should be movable
    # independently.
    max_parse_bytes: int = Field(default=52_428_800, gt=0)

    # A wall-clock budget per document. Generous against a real filing — a 300-page annual
    # report is seconds — and short enough that a pathological input does not stall a run.
    parse_timeout_seconds: float = Field(default=30.0, gt=0)

    # Address-space ceiling for a parse child, applied on POSIX only; Windows has no
    # equivalent without a native extension. See `aer.extract._child._apply_memory_cap` for
    # why the gap is narrower than it looks.
    max_parse_memory_bytes: int = Field(default=1_073_741_824, gt=0)

    # -- Network -----------------------------------------------------------------------

    # Permit the plain http:// scheme for outbound fetches. Off, and meant to stay off: an
    # unencrypted response can be altered in transit, and evidence that may have been
    # altered is not evidence.
    #
    # It relaxes the *scheme* rule only. The address rules are untouched, so loopback and
    # private addresses stay refused whatever this is set to — which means it does not
    # make a server on localhost reachable, and is not a way to point the fetch layer at
    # one. Its only real use is a public host that serves plain HTTP.
    allow_insecure_http: bool = False

    # -- Obsidian ----------------------------------------------------------------------

    obsidian_vault_root: Path | None = None
    obsidian_personal_root: Path | None = None

    # -- Research defaults -------------------------------------------------------------

    point_in_time_default: bool = True

    # -- Cost control ------------------------------------------------------------------

    # £2.50 until the first full live run measured one: the draft step alone came to £5.17,
    # and the whole run to something over eight. The old figure was chosen before any run
    # existed to measure, and it never stopped that run — because the draft step carried no
    # estimate, so the guard could not see the largest thing in the workflow at all (the
    # `estimated_cost_gbp` comment in `vertical_slice_v1`). Now that it can, £2.50 would stop
    # *every* run at the draft step instead, which is the same wrong number failing loudly
    # rather than silently.
    #
    # £12.00 admits a measured run with headroom and leaves the monthly ceiling as the thing
    # that actually bounds the total. **Tune it.** It is the operator's money and the right
    # figure depends on how many reports a month they want; `AER_PER_RUN_BUDGET_GBP` sets it
    # without a code change, and the plan gate shows the projected cost before anything is
    # spent.
    per_run_budget_gbp: Decimal = Field(default=Decimal("12.00"), gt=0)
    monthly_budget_gbp: Decimal = Field(default=Decimal("80.00"), gt=0)
    budget_warn_ratio: float = Field(default=0.75, gt=0, le=1)
    usd_to_gbp: Decimal = Field(default=Decimal("0.79"), gt=0)

    # The per-custom-section token ceiling the additive-only composer clamps requests to
    # (docs/archive/PLAN.md §2.12, §1.8: "12k each (cap)"). Config rather than code because it is
    # a cost decision; the *floor* rules a skill cannot relax are code, in
    # aer.core.skill_policy.
    custom_section_token_ceiling: int = Field(default=12_000, gt=0)

    # NoDecode: pydantic-settings would otherwise JSON-decode this at the source layer,
    # before any validator runs, so a blank `AER_MODEL_ROUTES=` would raise an opaque
    # SettingsError instead of falling back to the defaults. Parsing it ourselves keeps
    # blank-means-unset consistent with every other optional setting.
    model_routes: Annotated[dict[str, ModelRoute], NoDecode] = Field(
        default_factory=lambda: dict(DEFAULT_MODEL_ROUTES)
    )

    # Same NoDecode reasoning as model_routes: blank means the defaults, decoded here.
    house_style: Annotated[HouseStyle, NoDecode] = Field(default_factory=HouseStyle)

    # -- Validation --------------------------------------------------------------------

    @field_validator(
        "anthropic_api_key",
        "eodhd_api_key",
        "fred_api_key",
        "companies_house_api_key",
        "secret_key",
        "obsidian_vault_root",
        "obsidian_personal_root",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: Any) -> Any:
        """Treat ``AER_FOO=`` as unset rather than as an empty value.

        ``.env.example`` ships every optional key present but blank, which is the clearest
        way to document what exists. Without this, a blank line would produce
        ``SecretStr('')`` — a credential that is present, empty, and fails confusingly at
        the point of use instead of obviously at startup.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="after")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in _VALID_LOG_LEVELS:
            valid = ", ".join(sorted(_VALID_LOG_LEVELS))
            message = f"must be one of: {valid}"
            raise ValueError(message)
        return upper

    @field_validator("http_user_agent", mode="after")
    @classmethod
    def _user_agent_is_meaningful(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            message = "must not be blank; identify yourself, e.g. 'Jane Smith jane@example.com'"
            raise ValueError(message)
        return stripped

    @field_validator("model_routes", mode="before")
    @classmethod
    def _parse_model_routes(cls, value: Any) -> Any:
        """Decode the JSON ourselves, treating blank as unset (see NoDecode above)."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return dict(DEFAULT_MODEL_ROUTES)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                message = f"must be valid JSON: {exc}"
                raise ValueError(message) from exc
        return value

    @field_validator("house_style", mode="before")
    @classmethod
    def _parse_house_style(cls, value: Any) -> Any:
        """Decode the JSON ourselves, treating blank as unset (see NoDecode above).

        No merge validator to pair with it: unlike a routing table, whose absent keys
        are absent roles, ``HouseStyle`` declares a default per field, so a partial
        object — ``{"register": "first_person_plural"}`` — validates with the rest of
        the style intact.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return HouseStyle()
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                message = f"must be valid JSON: {exc}"
                raise ValueError(message) from exc
        return value

    @field_validator("model_routes", mode="after")
    @classmethod
    def _merge_with_default_routes(cls, value: dict[str, ModelRoute]) -> dict[str, ModelRoute]:
        """Overlay a partial override onto the defaults rather than replacing them.

        Replacement semantics would mean that overriding one role's effort silently
        removes routing for the other nine — a misconfiguration that would only surface
        much later, mid-run, as a missing role.
        """
        merged = dict(DEFAULT_MODEL_ROUTES)
        merged.update(value)
        return merged

    @field_validator("bind_host", mode="after")
    @classmethod
    def _warn_on_public_bind(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            _log.warning(
                "Binding to %s exposes this application to the network. It has no "
                "authentication and can reach your database, artefacts and provider "
                "credentials. Use 127.0.0.1 unless you have deliberately secured it.",
                value,
            )
        return value

    @model_validator(mode="after")
    def _secret_key_must_exist_in_production(self) -> Settings:
        """Fill in a per-process signing key, or refuse to start without a real one.

        There is deliberately no committed default. A signing key shipped in source is not
        a key: anyone with the repository could mint a valid CSRF token, so the protection
        would be decorative.

        Locally, generating one per process is the right trade. It costs nothing except
        that tokens issued before a restart stop verifying afterwards, which for a
        single-user tool on loopback means a form open across a restart needs reloading.
        In production that same behaviour would log every user out on each deploy and
        would differ between workers, so there it is a startup error instead.

        Generating a value inside a validator makes ``Settings`` non-deterministic, which
        is unusual enough to call out. The alternative — deriving it lazily at first use —
        puts mutable state behind a module global and makes "is this key stable?" a
        question you have to trace through call sites rather than read here.
        """
        if self.secret_key is not None:
            return self

        if self.is_production:
            message = (
                f"{ENV_PREFIX}SECRET_KEY must be set when {ENV_PREFIX}APP_ENV=production. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
            raise ValueError(message)

        _log.warning(
            "%sSECRET_KEY is not set; generating an ephemeral one for this process. CSRF "
            "tokens will stop verifying when the application restarts. Set it in .env to "
            "avoid that.",
            ENV_PREFIX,
        )
        # Assigning through __dict__ avoids re-running this validator, which
        # `validate_assignment` would otherwise do on a normal attribute set.
        self.__dict__["secret_key"] = SecretStr(secrets.token_urlsafe(_GENERATED_SECRET_BYTES))
        return self

    @model_validator(mode="after")
    def _obsidian_roots_must_not_nest(self) -> Settings:
        """Refuse to run if the generated vault and personal notes overlap.

        Checked in both directions. The exporter regenerates whole directories inside the
        generated vault, so personal notes nested underneath it are exposed to being
        overwritten; and a generated vault nested inside a personal folder makes it
        impossible to tell machine-written notes from your own. Neither is recoverable
        once it has happened, so it is a startup error rather than a warning.
        """
        vault = self.obsidian_vault_root
        personal = self.obsidian_personal_root
        if vault is None or personal is None:
            return self

        if _contains(personal, vault) or _contains(vault, personal):
            message = (
                f"{ENV_PREFIX}OBSIDIAN_VAULT_ROOT ({vault}) and "
                f"{ENV_PREFIX}OBSIDIAN_PERSONAL_ROOT ({personal}) overlap. The generated "
                "vault is rewritten wholesale, so these must be separate directories, "
                "neither containing the other."
            )
            raise ValueError(message)
        return self

    # -- Behaviour ---------------------------------------------------------------------

    def require_secret(self, field_name: str) -> str:
        """Return a credential's value, or raise a :class:`ConfigError` naming the setting.

        Use this at the point a credential is actually needed. The error names the
        environment variable to set, so the fix never requires reading the source.
        """
        if field_name not in SECRET_FIELDS:
            message = f"{field_name!r} is not a known secret setting"
            raise ConfigError(message, context={"known_secrets": list(SECRET_FIELDS)})

        secret: SecretStr | None = getattr(self, field_name)
        if secret is None or not secret.get_secret_value().strip():
            env_var = f"{ENV_PREFIX}{field_name.upper()}"
            message = f"{env_var} is not set, and is required for this operation."
            raise ConfigError(message, context={"setting": field_name, "env_var": env_var})
        return secret.get_secret_value()

    def ensure_directories(self) -> None:
        """Create the directories the application writes to.

        Called explicitly at startup, never from a validator: constructing a settings
        object should not touch the filesystem, or merely importing a module could create
        directories and every test would need a temporary path.
        """
        self.artefact_root.mkdir(parents=True, exist_ok=True)

    @property
    def signing_key(self) -> bytes:
        """The HMAC key for CSRF tokens and signed cookies.

        Always populated once validation has run; see
        :meth:`_secret_key_must_exist_in_production`.
        """
        if self.secret_key is None:  # pragma: no cover -- the validator guarantees this
            message = f"{ENV_PREFIX}SECRET_KEY was not resolved during validation."
            raise ConfigError(message)
        return self.secret_key.get_secret_value().encode("utf-8")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def _env_var_for(location: tuple[int | str, ...]) -> str:
    if not location:
        return "(configuration)"
    head = location[0]
    if isinstance(head, str):
        return f"{ENV_PREFIX}{head.upper()}"
    return str(head)


def load_settings(**overrides: Any) -> Settings:
    """Build :class:`Settings`, converting validation failures into one clear error.

    pydantic already gathers every failure into a single exception; this reports all of
    them together, named by environment variable, so configuring a new machine takes one
    pass rather than one run per mistake.
    """
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        problems = [f"  {_env_var_for(error['loc'])}: {error['msg']}" for error in exc.errors()]
        detail = "\n".join(problems)
        count = len(problems)
        noun = "problem" if count == 1 else "problems"
        message = (
            f"Configuration is invalid ({count} {noun}). Copy .env.example to .env and "
            f"correct the following:\n{detail}"
        )
        raise ConfigError(
            message,
            context={"problem_count": count, "problems": [p.strip() for p in problems]},
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, built once.

    Cached so that configuration is read and validated a single time. Tests must call
    ``get_settings.cache_clear()`` when they change the environment.
    """
    return load_settings()
