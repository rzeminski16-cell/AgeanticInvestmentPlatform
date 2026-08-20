"""Configuration behaviour, secret safety and path-containment rules.

The secret-safety tests here are the ones worth reading. A credential that renders in a
log line or a traceback is the most likely way this application leaks one, and the tests
below assert it cannot happen for every secret field rather than for one example.
"""

from __future__ import annotations

import json
import logging
import os.path
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from aer.config import (
    DEFAULT_MODEL_ROUTES,
    ENV_PREFIX,
    PROVIDER_CREDENTIAL_FIELDS,
    SECRET_FIELDS,
    HouseStyle,
    ModelRoute,
    Settings,
    _contains,
    get_settings,
    load_settings,
)
from aer.core.dates import format_date
from aer.errors import ConfigError

CURRENT_MODEL_IDS = {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}

# Whether pathlib itself treats two paths differing only in case as equal — true on
# Windows, false on Linux and on a case-sensitive macOS volume. Detected rather than
# inferred from `sys.platform`, because the thing that actually matters is the comparison
# behaviour and not the operating system's name.
PATHS_FOLD_CASE = Path("A") == Path("a")

# Deliberately fake. Its only job is to be a distinctive string we can assert never
# appears in a repr, a log line or a traceback.
SECRET_VALUE = "sk-ant-api03-NOTAREALKEY"  # pragma: allowlist secret


def env(name: str) -> str:
    return f"{ENV_PREFIX}{name}"


class TestLoading:
    def test_loads_from_environment(self, settings_env, valid_user_agent, tmp_path):
        artefacts = tmp_path / "artefacts"
        settings_env.setenv(env("APP_ENV"), "test")
        settings_env.setenv(env("LOG_LEVEL"), "debug")
        settings_env.setenv(env("BIND_PORT"), "9001")
        settings_env.setenv(env("PER_RUN_BUDGET_GBP"), "3.75")
        settings_env.setenv(env("POINT_IN_TIME_DEFAULT"), "false")
        settings_env.setenv(env("ARTEFACT_ROOT"), str(artefacts))

        settings = load_settings()

        assert settings.app_env == "test"
        assert settings.log_level == "DEBUG"
        assert settings.bind_port == 9001
        assert settings.per_run_budget_gbp == Decimal("3.75")
        assert settings.point_in_time_default is False
        assert settings.artefact_root == artefacts
        assert settings.http_user_agent == valid_user_agent

    def test_defaults_are_applied(self, settings_env):
        settings = load_settings()

        assert settings.bind_host == "127.0.0.1"
        assert settings.bind_port == 8000
        assert settings.app_env == "development"
        assert settings.log_level == "INFO"
        assert settings.log_json is True
        assert settings.artefact_root == Path("./var/artefacts")
        assert settings.point_in_time_default is True
        # £12.00, not the £2.50 this was written with. The old figure predated any run to
        # measure: the first full live run spent £5.17 on the draft step alone, and got past
        # the cap only because that step carried no estimate for the guard to read.
        assert settings.per_run_budget_gbp == Decimal("12.00")
        assert settings.monthly_budget_gbp == Decimal("80.00")
        assert settings.max_artefact_bytes == 52_428_800
        assert settings.is_production is False

    def test_blank_optional_values_are_treated_as_unset(self, settings_env):
        # .env.example ships every optional key present but blank. A blank must mean
        # "not configured", not "configured to empty".
        for field in PROVIDER_CREDENTIAL_FIELDS:
            settings_env.setenv(env(field.upper()), "")
        settings_env.setenv(env("OBSIDIAN_VAULT_ROOT"), "")
        settings_env.setenv(env("OBSIDIAN_PERSONAL_ROOT"), "")
        settings_env.setenv(env("MODEL_ROUTES"), "")

        settings = load_settings()

        for field in PROVIDER_CREDENTIAL_FIELDS:
            assert getattr(settings, field) is None
        assert settings.obsidian_vault_root is None
        assert settings.obsidian_personal_root is None
        assert settings.model_routes == DEFAULT_MODEL_ROUTES

    def test_user_agent_whitespace_is_stripped(self, settings_env):
        settings_env.setenv(env("HTTP_USER_AGENT"), "  Jane Smith jane@example.invalid  ")
        assert load_settings().http_user_agent == "Jane Smith jane@example.invalid"

    def test_unknown_aer_variables_are_ignored(self, settings_env, valid_user_agent):
        # docker-compose.yml reads AER_POSTGRES_* from the same .env file; those are not
        # application settings and must not cause a startup failure.
        settings_env.setenv(env("POSTGRES_PASSWORD"), "irrelevant-to-settings")
        assert load_settings().http_user_agent == valid_user_agent


class TestValidationErrors:
    def test_missing_user_agent_is_a_config_error_naming_the_variable(self):
        with pytest.raises(ConfigError) as excinfo:
            load_settings()

        assert excinfo.value.code == "config_error"
        assert env("HTTP_USER_AGENT") in str(excinfo.value)

    def test_blank_user_agent_is_rejected(self, settings_env):
        settings_env.setenv(env("HTTP_USER_AGENT"), "   ")
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert env("HTTP_USER_AGENT") in str(excinfo.value)

    def test_every_problem_is_reported_at_once(self, monkeypatch):
        # The point of aggregation: configuring a fresh machine should take one pass, not
        # one run per mistake. Three unrelated failures, all named in a single error.
        monkeypatch.setenv(env("BIND_PORT"), "70000")
        monkeypatch.setenv(env("LOG_LEVEL"), "CHATTY")
        # HTTP_USER_AGENT deliberately absent -- that is the third problem.

        with pytest.raises(ConfigError) as excinfo:
            load_settings()

        message = str(excinfo.value)
        assert env("HTTP_USER_AGENT") in message
        assert env("BIND_PORT") in message
        assert env("LOG_LEVEL") in message
        assert excinfo.value.context["problem_count"] == 3

    def test_invalid_log_level_lists_the_valid_ones(self, settings_env):
        settings_env.setenv(env("LOG_LEVEL"), "CHATTY")
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert "DEBUG" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("variable", "value"),
        [
            ("PER_RUN_BUDGET_GBP", "0"),
            ("MONTHLY_BUDGET_GBP", "-5"),
            ("BUDGET_WARN_RATIO", "1.5"),
            ("USD_TO_GBP", "0"),
            ("MAX_ARTEFACT_BYTES", "0"),
            ("BIND_PORT", "0"),
        ],
    )
    def test_out_of_range_numbers_are_rejected(self, settings_env, variable, value):
        settings_env.setenv(env(variable), value)
        with pytest.raises(ConfigError):
            load_settings()


class TestSecretSafety:
    @pytest.fixture
    def settings_with_secrets(self, settings_env) -> Settings:
        for field in SECRET_FIELDS:
            settings_env.setenv(env(field.upper()), SECRET_VALUE)
        return load_settings()

    def test_repr_never_reveals_a_secret(self, settings_with_secrets):
        assert SECRET_VALUE not in repr(settings_with_secrets)

    def test_str_never_reveals_a_secret(self, settings_with_secrets):
        assert SECRET_VALUE not in str(settings_with_secrets)

    def test_json_dump_masks_secrets(self, settings_with_secrets):
        dumped = settings_with_secrets.model_dump_json()
        assert SECRET_VALUE not in dumped
        assert "**********" in dumped

    def test_model_dump_keeps_secrets_wrapped(self, settings_with_secrets):
        # A plain model_dump must not unwrap to raw strings, or anything that serialises
        # settings for logging or debugging would leak every credential at once.
        dumped = settings_with_secrets.model_dump()
        for field in SECRET_FIELDS:
            assert not isinstance(dumped[field], str)
        assert SECRET_VALUE not in str(dumped)

    def test_secret_is_retrievable_deliberately(self, settings_with_secrets):
        assert settings_with_secrets.require_secret("anthropic_api_key") == SECRET_VALUE

    def test_require_secret_on_unset_key_names_the_variable(self, settings_env):
        settings = load_settings()
        with pytest.raises(ConfigError) as excinfo:
            settings.require_secret("eodhd_api_key")

        assert excinfo.value.context["env_var"] == env("EODHD_API_KEY")
        assert env("EODHD_API_KEY") in str(excinfo.value)

    def test_require_secret_rejects_a_whitespace_only_value(self, settings_env):
        settings_env.setenv(env("FRED_API_KEY"), "   ")
        settings = load_settings()
        with pytest.raises(ConfigError):
            settings.require_secret("fred_api_key")

    def test_require_secret_rejects_an_unknown_field(self, settings_env):
        settings = load_settings()
        with pytest.raises(ConfigError, match="not a known secret"):
            settings.require_secret("database_url")

    def test_exception_text_from_validation_contains_no_secret(self, settings_env):
        # A traceback is a leak path too: a validation failure elsewhere must not print
        # the credentials that happened to be set at the time.
        settings_env.setenv(env("ANTHROPIC_API_KEY"), SECRET_VALUE)
        settings_env.setenv(env("BIND_PORT"), "70000")
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert SECRET_VALUE not in str(excinfo.value)
        assert SECRET_VALUE not in repr(excinfo.value.context)


class TestBindHostWarning:
    def test_public_bind_logs_a_warning(self, settings_env, caplog):
        caplog.set_level(logging.WARNING, logger="aer.config")
        settings_env.setenv(env("BIND_HOST"), "0.0.0.0")  # noqa: S104

        settings = load_settings()

        assert settings.bind_host == "0.0.0.0"  # noqa: S104
        assert any(record.levelno == logging.WARNING for record in caplog.records)
        assert "exposes this application" in caplog.text

    def test_loopback_bind_is_silent(self, settings_env, caplog):
        caplog.set_level(logging.WARNING, logger="aer.config")
        settings_env.setenv(env("BIND_HOST"), "127.0.0.1")
        # Set the signing key so its own "generating an ephemeral one" warning does not
        # count as a bind-host warning.
        settings_env.setenv(env("SECRET_KEY"), SECRET_VALUE)

        load_settings()

        assert not caplog.records


class TestSigningKey:
    """A signing key must exist, must never be a committed constant, and must be real
    in production."""

    def test_a_configured_key_is_used_verbatim(self, settings_env):
        settings_env.setenv(env("SECRET_KEY"), SECRET_VALUE)
        assert load_settings().signing_key == SECRET_VALUE.encode()

    def test_an_absent_key_is_generated_in_development(self, settings_env):
        settings = load_settings()
        assert settings.secret_key is not None
        assert len(settings.signing_key) >= 32

    def test_a_generated_key_is_announced(self, settings_env, caplog):
        caplog.set_level(logging.WARNING, logger="aer.config")
        load_settings()
        assert env("SECRET_KEY") in caplog.text

    def test_generated_keys_differ_between_processes(self, settings_env):
        # Two loads stand in for two processes. If these ever matched, the "key" would be
        # derived from something constant, which is the failure mode worth catching.
        first = load_settings().signing_key
        second = load_settings().signing_key
        assert first != second

    def test_production_refuses_to_start_without_one(self, settings_env):
        settings_env.setenv(env("APP_ENV"), "production")
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert env("SECRET_KEY") in str(excinfo.value)

    def test_production_starts_with_one(self, settings_env):
        settings_env.setenv(env("APP_ENV"), "production")
        settings_env.setenv(env("SECRET_KEY"), SECRET_VALUE)
        settings = load_settings()
        assert settings.is_production
        assert settings.signing_key == SECRET_VALUE.encode()

    def test_a_generated_key_still_never_renders(self, settings_env):
        settings = load_settings()
        assert settings.secret_key is not None
        assert settings.secret_key.get_secret_value() not in repr(settings)
        assert settings.secret_key.get_secret_value() not in settings.model_dump_json()


class TestObsidianContainment:
    """The generated vault is rewritten wholesale, so overlap risks personal writing."""

    def _load(self, settings_env, vault: Path, personal: Path) -> Settings:
        settings_env.setenv(env("OBSIDIAN_VAULT_ROOT"), str(vault))
        settings_env.setenv(env("OBSIDIAN_PERSONAL_ROOT"), str(personal))
        return load_settings()

    def test_separate_directories_are_accepted(self, settings_env, isolated_paths):
        settings = self._load(settings_env, isolated_paths["vault"], isolated_paths["personal"])
        assert settings.obsidian_vault_root == isolated_paths["vault"]
        assert settings.obsidian_personal_root == isolated_paths["personal"]

    def test_vault_inside_personal_root_is_rejected(self, settings_env, tmp_path):
        personal = tmp_path / "notes"
        with pytest.raises(ConfigError, match="overlap"):
            self._load(settings_env, personal / "research", personal)

    def test_personal_root_inside_vault_is_rejected(self, settings_env, tmp_path):
        # The reverse direction matters just as much: the exporter regenerates whole
        # directories inside the vault, so personal notes nested underneath are exposed.
        vault = tmp_path / "generated"
        with pytest.raises(ConfigError, match="overlap"):
            self._load(settings_env, vault, vault / "my-thoughts")

    def test_identical_roots_are_rejected(self, settings_env, tmp_path):
        same = tmp_path / "vault"
        with pytest.raises(ConfigError, match="overlap"):
            self._load(settings_env, same, same)

    def test_deeply_nested_overlap_is_rejected(self, settings_env, tmp_path):
        personal = tmp_path / "notes"
        with pytest.raises(ConfigError, match="overlap"):
            self._load(settings_env, personal / "a" / "b" / "c", personal)

    def test_similar_prefix_is_not_treated_as_containment(self, settings_env, tmp_path):
        # "notes-generated" starts with "notes" as a string but is not inside it. A naive
        # startswith check would wrongly reject this.
        self._load(settings_env, tmp_path / "notes-generated", tmp_path / "notes")

    def test_only_one_root_configured_is_allowed(self, settings_env, tmp_path):
        settings_env.setenv(env("OBSIDIAN_VAULT_ROOT"), str(tmp_path / "vault"))
        settings = load_settings()
        assert settings.obsidian_vault_root is not None
        assert settings.obsidian_personal_root is None


class TestContainmentHelper:
    """Direct tests of the comparison, including the Windows case-insensitivity path.

    Driving this through the filesystem would make the result depend on whether the
    developer's filesystem happens to be case-sensitive -- which is exactly the platform
    difference the helper exists to paper over. Simulating ``normcase`` instead tests the
    logic deterministically everywhere.
    """

    def test_directory_contains_itself(self, tmp_path):
        assert _contains(tmp_path, tmp_path)

    def test_directory_contains_a_descendant(self, tmp_path):
        assert _contains(tmp_path, tmp_path / "a" / "b")

    def test_descendant_does_not_contain_its_parent(self, tmp_path):
        assert not _contains(tmp_path / "a", tmp_path)

    def test_shared_prefix_is_not_containment(self, tmp_path):
        assert not _contains(tmp_path / "notes", tmp_path / "notes-generated")

    def test_case_differences_matter_on_a_case_folding_filesystem(self, monkeypatch, tmp_path):
        # Simulate Windows: normcase lowercases, so "Notes" and "notes" are one directory
        # and the containment check must catch the overlap.
        monkeypatch.setattr(os.path, "normcase", str.lower)
        assert _contains(tmp_path / "Notes", tmp_path / "notes" / "research")

    @pytest.mark.skipif(
        PATHS_FOLD_CASE,
        reason=(
            "pathlib's Windows flavour compares paths case-insensitively in its own "
            "right, whatever os.path.normcase does, so a case-sensitive filesystem "
            "cannot be simulated by patching normcase here. The behaviour that matters "
            "on Windows -- Notes and notes being one directory -- is the test above, "
            "which does run there."
        ),
    )
    def test_case_differences_are_distinct_on_a_case_sensitive_filesystem(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(os.path, "normcase", lambda value: value)
        assert not _contains(tmp_path / "Notes", tmp_path / "notes" / "research")

    def test_the_helper_folds_case_wherever_the_platform_does(self, tmp_path):
        """No simulation, no patching: whatever this machine does, containment agrees.

        The two tests above each cover one half of the platform split and one of them is
        always skipped. This one runs everywhere and would catch a helper that disagreed
        with its own filesystem — which is the failure that would actually let personal
        notes sit inside a directory the exporter regenerates.
        """
        assert _contains(tmp_path / "Notes", tmp_path / "notes" / "research") is PATHS_FOLD_CASE


class TestDirectories:
    def test_construction_creates_nothing(self, settings_env, tmp_path):
        target = tmp_path / "artefacts"
        settings_env.setenv(env("ARTEFACT_ROOT"), str(target))

        load_settings()

        assert not target.exists(), "constructing Settings must not touch the filesystem"

    def test_ensure_directories_creates_the_artefact_root(self, settings_env, tmp_path):
        target = tmp_path / "nested" / "artefacts"
        settings_env.setenv(env("ARTEFACT_ROOT"), str(target))

        settings = load_settings()
        settings.ensure_directories()

        assert target.is_dir()

    def test_ensure_directories_is_idempotent(self, settings_env, tmp_path):
        target = tmp_path / "artefacts"
        settings_env.setenv(env("ARTEFACT_ROOT"), str(target))
        settings = load_settings()

        settings.ensure_directories()
        settings.ensure_directories()

        assert target.is_dir()


class TestModelRoutes:
    def test_defaults_cover_every_role(self, settings_env):
        routes = load_settings().model_routes
        assert set(routes) == set(DEFAULT_MODEL_ROUTES)

    def test_every_default_route_uses_a_current_model(self, settings_env):
        for role, route in load_settings().model_routes.items():
            assert route.model in CURRENT_MODEL_IDS, f"{role} routes to unknown {route.model}"

    def test_partial_override_merges_rather_than_replaces(self, settings_env):
        # Replacement would silently leave nine roles unrouted, surfacing much later as a
        # mid-run failure.
        settings_env.setenv(
            env("MODEL_ROUTES"),
            json.dumps({"planner": {"model": "claude-opus-5", "effort": "max"}}),
        )

        routes = load_settings().model_routes

        assert routes["planner"] == ModelRoute(model="claude-opus-5", effort="max")
        assert set(routes) == set(DEFAULT_MODEL_ROUTES)
        assert routes["report_writer"] == DEFAULT_MODEL_ROUTES["report_writer"]

    def test_invalid_effort_is_rejected(self, settings_env):
        settings_env.setenv(
            env("MODEL_ROUTES"),
            json.dumps({"planner": {"model": "claude-opus-5", "effort": "maximum"}}),
        )
        with pytest.raises(ConfigError):
            load_settings()

    def test_routes_are_immutable(self, settings_env):
        route = load_settings().model_routes["planner"]
        with pytest.raises(ValidationError):
            route.model = "claude-haiku-4-5"  # type: ignore[misc]


class TestHouseStyle:
    def test_the_defaults_are_the_agreed_style(self, settings_env):
        style = load_settings().house_style
        assert style.prose_money == "auto"
        assert style.billions_from == Decimal("1000000000")
        assert style.voice == "impersonal"

    def test_the_default_date_format_reads_uk(self, settings_env):
        """Through `format_date`, which is what the renderer uses. A raw `strftime` here
        was the last place the glibc-only `%-d` reached the C library directly, and it
        failed the suite on Windows while every Linux gate stayed green."""
        rendered = format_date(date(2025, 12, 27), load_settings().house_style.date_format)
        assert rendered == "27 December 2025"

    def test_a_partial_object_keeps_every_default_it_omits(self, settings_env):
        settings_env.setenv(env("HOUSE_STYLE"), json.dumps({"prose_money": "millions"}))

        style = load_settings().house_style

        assert style.prose_money == "millions"
        assert style.voice == "impersonal"
        assert style.billions_from == Decimal("1000000000")

    def test_blank_means_the_defaults(self, settings_env):
        settings_env.setenv(env("HOUSE_STYLE"), "")
        assert load_settings().house_style == HouseStyle()

    def test_an_unknown_voice_is_rejected(self, settings_env):
        settings_env.setenv(env("HOUSE_STYLE"), json.dumps({"voice": "royal_we"}))
        with pytest.raises(ConfigError):
            load_settings()

    def test_a_pattern_strftime_cannot_render_is_rejected(self, settings_env):
        # A bad pattern must fail at configuration, not in the renderer half way through
        # a paid-for report.
        settings_env.setenv(env("HOUSE_STYLE"), json.dumps({"date_format": "%Q"}))
        with pytest.raises(ConfigError):
            load_settings()

    def test_the_style_is_immutable(self, settings_env):
        style = load_settings().house_style
        with pytest.raises(ValidationError):
            style.voice = "first_person_plural"  # type: ignore[misc]


class TestEnvExampleStaysInStep:
    """`.env.example` is the setup documentation; drift makes it actively misleading.

    A new setting added without a corresponding entry leaves a fresh machine unable to
    discover it. Cheaper to catch here than in a confused half-hour six months from now.
    """

    @pytest.fixture
    def env_example(self) -> str:
        path = Path(__file__).parent.parent / ".env.example"
        return path.read_text(encoding="utf-8")

    def test_every_setting_is_documented(self, env_example):
        undocumented = [
            f"{ENV_PREFIX}{name.upper()}"
            for name in Settings.model_fields
            if f"{ENV_PREFIX}{name.upper()}" not in env_example
        ]
        assert not undocumented, f"settings missing from .env.example: {undocumented}"

    def test_the_required_setting_is_present_but_blank(self, env_example):
        # Present, so it is discoverable; blank, so a fresh checkout fails loudly with a
        # message naming it rather than silently using someone else's identity.
        assert f"{ENV_PREFIX}HTTP_USER_AGENT=\n" in env_example

    def test_no_secret_is_populated(self, env_example):
        for field in SECRET_FIELDS:
            line = f"{ENV_PREFIX}{field.upper()}="
            assert f"{line}\n" in env_example, f"{line} should be present and empty"


class TestGetSettingsCaching:
    def test_returns_the_same_object(self, settings_env):
        assert get_settings() is get_settings()

    def test_cache_clear_rebuilds(self, settings_env):
        first = get_settings()
        get_settings.cache_clear()
        assert get_settings() is not first

    def test_cache_clear_picks_up_changed_environment(self, settings_env):
        assert get_settings().bind_port == 8000
        get_settings.cache_clear()
        settings_env.setenv(env("BIND_PORT"), "9999")
        assert get_settings().bind_port == 9999
