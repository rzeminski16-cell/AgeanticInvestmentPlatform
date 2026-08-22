"""Foundation smoke tests: version identity, error contract, and secret redaction."""

from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from aer import __version__, build_identity, version
from aer.errors import (
    AerError,
    BudgetExceededError,
    ConfigError,
    ExternalServiceError,
    IntegrityError,
    ValidationError,
)
from aer.logging import (
    MASK,
    configure_logging,
    get_logger,
    is_sensitive_name,
    redact_secrets,
    redact_value,
)

SEMVER_ISH = re.compile(r"^\d+\.\d+\.\d+")

ERROR_TYPES = [
    AerError,
    ConfigError,
    ValidationError,
    ExternalServiceError,
    BudgetExceededError,
    IntegrityError,
]


class TestVersion:
    def test_version_is_non_empty_and_semver_ish(self):
        assert isinstance(__version__, str)
        assert __version__
        assert SEMVER_ISH.match(__version__), __version__

    def test_version_function_matches_module_attribute(self):
        assert version() == __version__

    def test_build_identity_includes_version(self):
        identity = build_identity()
        assert __version__ in identity


class TestErrors:
    @pytest.mark.parametrize("error_type", ERROR_TYPES)
    def test_every_error_has_a_code_and_context(self, error_type):
        kwargs = {"provider": "sec_edgar"} if error_type is ExternalServiceError else {}
        error = error_type("something went wrong", **kwargs)

        assert isinstance(error.code, str)
        assert error.code
        assert isinstance(error.context, dict)
        assert isinstance(error, AerError)

    def test_codes_are_unique(self):
        codes = [error_type.code for error_type in ERROR_TYPES]
        assert len(codes) == len(set(codes))

    def test_context_is_copied_not_aliased(self):
        original = {"ticker": "MSFT"}
        error = AerError("boom", context=original)
        error.context["ticker"] = "AAPL"
        assert original["ticker"] == "MSFT"

    def test_to_dict_round_trip(self):
        error = ValidationError("as_of_date is in the future", context={"field": "as_of_date"})
        payload = error.to_dict()
        assert payload == {
            "code": "validation_error",
            "message": "as_of_date is in the future",
            "context": {"field": "as_of_date"},
        }

    def test_external_service_error_carries_retry_signal(self):
        error = ExternalServiceError(
            "rate limited",
            provider="sec_edgar",
            retryable=True,
            status_code=429,
        )
        assert error.retryable is True
        assert error.provider == "sec_edgar"
        assert error.context["status_code"] == 429


class TestRedaction:
    def test_masks_anthropic_key_in_a_message_string(self, fake_anthropic_key):
        event = redact_secrets(None, "info", {"event": f"calling api with {fake_anthropic_key}"})
        assert fake_anthropic_key not in event["event"]
        assert MASK in event["event"]

    def test_masks_field_named_api_key(self, fake_anthropic_key):
        event = redact_secrets(None, "info", {"event": "configured", "api_key": fake_anthropic_key})
        assert event["api_key"] == MASK

    def test_leaves_ordinary_fields_untouched(self):
        event = redact_secrets(
            None,
            "info",
            {
                "event": "run started",
                "ticker": "MSFT",
                "as_of_date": "2026-07-27",
                "cost_gbp": 1.25,
            },
        )
        assert event["ticker"] == "MSFT"
        assert event["as_of_date"] == "2026-07-27"
        assert event["cost_gbp"] == 1.25
        assert event["event"] == "run started"

    @pytest.mark.parametrize(
        "field_name",
        [
            "api_key",
            "API_KEY",
            "anthropic_api_key",
            "password",
            "secret",
            "auth_token",
            "authorization",
            "private_key",
            "eodhd_apikey",
            "db_credential",
            "access_token",
            "refresh_token",
            "csrf_token",
            "token",
        ],
    )
    def test_sensitive_names_are_detected(self, field_name):
        assert is_sensitive_name(field_name)

    @pytest.mark.parametrize(
        "field_name",
        ["step_key", "section_key", "idempotency_key", "key", "ticker", "monkey_patch"],
    )
    def test_structural_key_fields_are_not_redacted(self, field_name):
        # These are identifiers, not credentials. Masking them would gut the run console
        # for no security benefit -- see the module docstring in aer/logging.py.
        assert not is_sensitive_name(field_name)

    @pytest.mark.parametrize(
        "field_name",
        [
            "max_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ],
    )
    def test_token_counts_are_not_redacted(self, field_name):
        """A substring match on "token" masked every number the budget is reconciled from.

        ``provider.completed`` reported its token counts as ``***REDACTED***``, and a failure
        telling the operator to raise a ``max_tokens`` ceiling hid the ceiling. Credential
        tokens are named in compound and are caught that way; no integer is a credential, and
        the plural is never one.
        """
        assert not is_sensitive_name(field_name)

    def test_bearer_token_is_masked_but_scheme_is_kept(self):
        redacted = redact_value("Authorization: Bearer abcdef1234567890xyz")
        assert "abcdef1234567890xyz" not in redacted
        assert "Bearer" in redacted
        assert MASK in redacted

    def test_redaction_reaches_nested_structures(self, fake_anthropic_key):
        event = redact_secrets(
            None,
            "info",
            {
                "event": "provider configured",
                "providers": [
                    {"name": "anthropic", "api_key": fake_anthropic_key},
                    {"name": "eodhd", "note": f"token is {fake_anthropic_key}"},
                ],
            },
        )
        first, second = event["providers"]
        assert first["api_key"] == MASK
        assert fake_anthropic_key not in second["note"]

    def test_recursion_is_depth_limited(self):
        # A deeply nested structure must not hang or blow the stack. Losing detail deep
        # in a log payload is always better than wedging the process writing it.
        payload: dict[str, object] = {"level": 0}
        cursor = payload
        for depth in range(1, 60):
            child: dict[str, object] = {"level": depth}
            cursor["child"] = child
            cursor = child

        result = redact_secrets(None, "info", {"event": "deep", "payload": payload})
        assert result["event"] == "deep"

    def test_non_string_scalars_pass_through(self):
        assert redact_value(42) == 42
        assert redact_value(None) is None
        assert redact_value(True) is True


class TestLoggingConfiguration:
    """End-to-end: the processor chain must actually be wired into emitted output.

    A redaction function that works in isolation but is not reached by real log calls is
    worse than none, because it invites false confidence.
    """

    def test_emitted_json_line_is_redacted(self, capsys, fake_anthropic_key):
        configure_logging(level="INFO", json_output=True)
        get_logger("test").info(
            "provider configured",
            api_key=fake_anthropic_key,
            ticker="MSFT",
            note=f"header was Bearer {'z' * 24}",
        )

        captured = capsys.readouterr().out.strip()
        payload = json.loads(captured)

        assert payload["api_key"] == MASK
        assert payload["ticker"] == "MSFT"
        assert fake_anthropic_key not in captured
        assert "z" * 24 not in captured
        assert payload["event"] == "provider configured"
        assert payload["level"] == "info"
        assert "timestamp" in payload

    def test_rejects_unknown_level(self):
        with pytest.raises(ValueError, match="Unknown log level"):
            configure_logging(level="VERBOSE")

    def test_console_renderer_still_redacts(self, capsys, fake_anthropic_key):
        configure_logging(level="INFO", json_output=False)
        get_logger("test").info("configured", api_key=fake_anthropic_key)

        captured = capsys.readouterr().out
        assert fake_anthropic_key not in captured
        assert MASK in captured

    def test_foreign_stdlib_records_are_redacted(self, capsys, fake_anthropic_key):
        # A third-party client logging a URL that carries a credential is a realistic
        # leak path. Bridging structlog onto stdlib logging is what closes it, so prove
        # the bridge actually redacts rather than trusting that it does.
        configure_logging(level="INFO", json_output=True)
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://example.test/data?token=%s", fake_anthropic_key
        )

        captured = capsys.readouterr().out.strip()
        assert fake_anthropic_key not in captured
        assert MASK in captured

    @pytest.mark.parametrize("parameter", ["api_key", "api_token", "token", "access_token"])
    def test_a_credential_that_looks_like_ordinary_text_is_still_redacted(self, capsys, parameter):
        """This test used to pass for the wrong reason and is why the parameter rule exists.

        The case above uses an Anthropic-shaped key, which the value-shape patterns catch
        wherever it appears — so it proved the bridge worked and said nothing about URLs. A
        FRED or EODHD key is a bare hex string that matches no shape at all, and one went out
        in full on every `fetch.completed` line and every `httpx` request line until the
        parameter-name rule was added.
        """
        ordinary = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
        configure_logging(level="INFO", json_output=True)
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://example.test/data?%s=%s&fmt=json", parameter, ordinary
        )

        captured = capsys.readouterr().out.strip()
        assert ordinary not in captured
        assert parameter in captured, "the parameter name should survive; only the value goes"
        assert "fmt=json" in captured, "the rest of the URL is what makes a fetch reproducible"

    def test_level_filtering_is_applied(self, capsys):
        configure_logging(level="WARNING", json_output=True)
        get_logger("test").info("should not appear")
        get_logger("test").warning("should appear")

        captured = capsys.readouterr().out
        assert "should not appear" not in captured
        assert "should appear" in captured


class TestTheDefaultRunCannotSpendMoney:
    """`CLAUDE.md`: "Tests must run with no network access and no model spend by default."

    The `live_llm` marker's own description in `pyproject.toml` says it is "excluded from the
    default suite" — and for as long as no test carried the marker, nothing had to be true for
    that sentence to look true. There was no exclusion anywhere; the first live test added
    would have billed on every developer's run and on every CI run, and the description would
    have gone on reading correctly.

    So the promise is asserted against the mechanism that has to keep it. This is the same
    shape of defect as a budget ceiling that is stored and never compared: a claim nobody
    encoded cannot fail.
    """

    def test_addopts_deselects_the_billable_marker(self) -> None:
        config = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        )
        addopts = config["tool"]["pytest"]["ini_options"]["addopts"]

        assert "not live_llm" in addopts, (
            "pyproject declares a marker for billable tests and promises they are excluded "
            "by default; addopts is where that promise is kept"
        )

    def test_the_marker_is_declared_so_a_typo_cannot_silently_include_them(self) -> None:
        # `--strict-markers` turns a misspelt marker into an error rather than an unmarked
        # test. Without it, `@pytest.mark.live_lm` would run — and bill — in every default run.
        config = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        )
        ini = config["tool"]["pytest"]["ini_options"]

        assert "--strict-markers" in ini["addopts"]
        assert any(marker.startswith("live_llm:") for marker in ini["markers"])


class TestNothingUnderSrcIsIgnored:
    """No file the application needs may be excluded from the repository.

    This is not hypothetical. A `reports/` line in `.gitignore`, meant for generated
    output, was unanchored — so it also matched `src/aer/web/templates/reports/` and kept
    the report page out of the repository entirely. Every test passed, because the file
    was present locally; a fresh checkout would have rendered every page except the one
    the whole platform exists to produce.

    Asked of git rather than reimplemented: `.gitignore` semantics are more subtle than
    they look, and a second implementation of them would be wrong in a different way.
    """

    def test_git_ignores_nothing_under_src(self) -> None:
        root = Path(__file__).resolve().parent.parent
        candidates = [
            path
            for path in (root / "src").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
        assert candidates, "no source files found; the path is wrong"

        # `--no-index` is load-bearing. Without it, `check-ignore` says nothing about a
        # file that is already tracked — so once the missing template had been committed,
        # the check that was supposed to catch the rule would go quiet, and the next
        # source directory the same rule swallowed would be missed in exactly the same way.
        #
        # `check-ignore` exits 0 when it matched something, 1 when it matched nothing.
        result = subprocess.run(
            ["git", "check-ignore", "--stdin", "--no-index"],  # noqa: S607 -- git is on PATH
            input="\n".join(str(p) for p in candidates),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip() == "", (
            f"these files are git-ignored and would be missing from a checkout:\n{result.stdout}"
        )


class TestPortability:
    """The platform's own machines are Linux; the operator's is Windows.

    What belongs here is anything that runs fine on every Linux CI pass and breaks the
    first time the operator's machine executes it. Those cannot be caught by running the
    code — this suite *is* the Linux pass — so they are caught by reading it.
    """

    def test_no_source_uses_a_glibc_only_strftime_code(self):
        """`%-d` and friends strip the leading zero on glibc and raise on Windows.

        Found live: `archive_request` formatted its refusal date with `%-d`, so archiving
        twice — a path with a test, passing green on Linux — returned a 500 instead of the
        intended 409 on the operator's machine. Format the day with `.day` instead.

        **This catches call sites, and the second live failure was not one.** The house
        style's default `date_format` is `"%-d %B %Y"` — a plain string that travels as
        configuration and meets `strftime` several modules away, matching neither shape
        below. Every report on Windows died on it. Pattern *values* are now expanded by
        `aer.core.dates.format_date` before the C library sees them (see
        `tests/test_core_dates.py`, which emulates a strftime that refuses the flag); this
        test remains the guard for the direct-call shape, which that expansion does not
        cover.
        """
        # Two shapes reach strftime: an explicit `.strftime("%-d ...")` call, and an
        # f-string format spec, where the code sits directly after the colon. Matched as
        # usage rather than as a bare `%-d`, so prose about the hazard does not trip it;
        # this file is excluded because its examples must be allowed to show the shapes.
        used_as_format = re.compile(r"strftime\([^)]*%-[a-zA-Z]|:%-[a-zA-Z]")

        # `format_date` is the sanctioned path: it expands the flag itself, so a pattern
        # handed to *it* is portable by construction and a line calling it is not an
        # offender. Without this the rule would forbid the very function that fixes it.
        made_portable = re.compile(r"\bformat_date\(")

        offenders = []
        for tree in ("src", "tests", "migrations"):
            for path in (Path(__file__).parent.parent / tree).rglob("*.py"):
                if path == Path(__file__):
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if used_as_format.search(line) and not made_portable.search(line):
                        offenders.append(f"{path}:{number}: {line.strip()}")

        assert not offenders, (
            "glibc-only strftime codes break on Windows; use `.day` or a zero-padded "
            "code instead:\n" + "\n".join(offenders)
        )


class TestTheMandateClockHasOneWriter:
    """ADR 0068 duplicates five columns onto ``research_requests`` for one revision.

    ``as_of_date``, ``point_in_time``, ``max_cost_gbp``, ``status`` and ``archived_at``
    exist on the mandate *and* on the work order it is a detail of, and both copies are
    read: ``scope_for_request`` takes the run's clock from the work order, while some
    thirty acquisition and drafting call sites still read the mandate's. Duplicated columns
    diverge unless something stops them, and here that something is one function —
    ``services.requests._mirror_to_work_order`` — run after every edit, archive and restore.

    A second writer anywhere else would be a silent divergence: the report's front page
    printing one as-of date while the evidence filter used another. Found exactly that way,
    in ``tests/conftest.py``, where six fixtures moved one of these on a hand-built row and
    a glance block showed FY2021 figures for a run dated September 2022. The listener there
    now mirrors too, and its right to do so rests on this: there is no other writer for it
    to paper over.

    The rule is scoped to receivers named ``request``, which is what every one of those
    thirty call sites calls it. A writer that named its variable something else would
    escape — the guard is for the ordinary shape, and the columns leave in the next
    revision anyway.
    """

    COLUMNS = frozenset({"as_of_date", "point_in_time", "max_cost_gbp", "status", "archived_at"})
    OWNER = Path("src/aer/services/requests.py")

    def test_only_the_requests_service_assigns_them(self) -> None:
        root = Path(__file__).parent.parent
        offenders = []

        for path in (root / "src").rglob("*.py"):
            if path.relative_to(root) == self.OWNER:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, ast.AugAssign | ast.AnnAssign)
                    else []
                )
                offenders.extend(
                    f"{path.relative_to(root)}:{target.lineno}: request.{target.attr}"
                    for target in targets
                    if isinstance(target, ast.Attribute)
                    and target.attr in self.COLUMNS
                    and isinstance(target.value, ast.Name)
                    and (target.value.id == "request" or target.value.id.endswith("_request"))
                )

        assert not offenders, (
            "these columns are duplicated onto work_orders and are mirrored by "
            f"{self.OWNER}._mirror_to_work_order; a writer elsewhere diverges the two "
            "copies silently:\n" + "\n".join(offenders)
        )

    def test_the_owner_really_does_write_them(self) -> None:
        # Otherwise the test above passes by asserting nothing, which is the failure mode
        # of every grep-shaped test.
        tree = ast.parse((Path(__file__).parent.parent / self.OWNER).read_text(encoding="utf-8"))
        written = {
            target.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "request"
        }

        assert self.COLUMNS - {"status"} <= written
