# 0049 — Tracing is off until somebody asks for it, and can never fail a run

Date: 2026-08-10
Status: Accepted

## Context

Gap A13: structured JSON logs were the whole observability story, which is why debugging a
live run meant reading worker output by hand. A run is a tree — steps inside a job, model
calls and fetches inside steps — and a log stream is precisely the format that loses it.

The gap named Langfuse and OTel/Grafana. Neither is reachable from the development
environment: the organisation's egress policy 403s external hosts, and no collector runs
locally. So whatever was built had to be verifiable without one, or it would be code nobody
could test.

## Decision

**OpenTelemetry, exporter off unless `AER_OTEL_ENDPOINT` is set.** With no endpoint the
tracer is `None`, `span()` is a bare yield, and nothing is imported, started or connected. A
local-first tool that needs a collector running before it will start is a tool nobody
starts, and a tracing layer that retries a dead endpoint on every span is worse than no
tracing at all.

Langfuse was the alternative and is the better fit for LLM-call tracing specifically. It was
declined here because it is a vendor dependency that cannot be exercised from this
environment, and an integration nobody can run is an integration nobody can trust. OTLP is
the open protocol; a Langfuse OTLP endpoint remains a configuration choice rather than a
code change.

**Tracing may never fail a run.** Every entry point is contained: a tracer that raises when
a span opens, an exporter that raises when it closes, a missing package, a bad endpoint —
each degrades to no tracing and a warning. The traced block still runs, and an exception
from the traced block is recorded and re-raised **unchanged**. Instrumentation that can take
the platform down is instrumentation worth deleting, so most of `tests/test_tracing.py`
breaks the tracing stack on purpose and asserts the work still completes.

One implementation note, because it caused a real bug on the way: `span` is a generator
context manager, and the obvious shape — one `try` around both the tracing machinery and the
`yield` — can reach a recovery path that yields a second time. A generator that yields twice
raises from `contextlib` itself, which would turn a tracing hiccup into a failure of whatever
was being traced. The tracer is therefore entered by hand so the two failure domains stay
separate, and every path yields exactly once.

**Spans carry identifiers, never payloads.** Names, ids, counts, model and effort. No prompt,
no document, no response. Traces leave the machine once an endpoint is set, so a span
attribute is data the operator has exported, and `aer.logging`'s redaction does not reach it.

Three levels are instrumented, matching the shape of a run: `step.<key>` in the workflow
engine (both the parallel and serial paths), and `model.<role>` around the provider call in
`Agent.run`.

## Consequences

`opentelemetry-sdk` and the OTLP HTTP exporter are now dependencies. They are imported
lazily inside `configure_tracing`, so an install that never sets an endpoint pays only the
disk cost.

**This has not been exercised against a real collector.** The span tree, attributes, nesting
and every containment property are tested through an in-memory exporter, which is genuine
verification of the code but not of the wire format or of what a Grafana or Langfuse view
makes of it. The first operator to set `AER_OTEL_ENDPOINT` is doing something this repository
has never done, and that is worth knowing before relying on it.

Fetches are not yet instrumented. `aer.fetch.client` is the obvious third level and was left
for when there is a collector to look at the result in — adding spans nobody can see is how
instrumentation grows without being checked.
