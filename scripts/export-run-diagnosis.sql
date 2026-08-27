-- Export one run's drafting record, for diagnosing roadmap §2.1 off the machine that ran it.
--
-- Step two of two. Run `list-runs.sql` first to find the job id.
--
-- Reads only. Writes one JSON object to stdout and touches nothing.
--
-- **What it deliberately leaves out.** No artefact bytes, so no fetched filings, no model
-- prompts and no model responses -- `agent_runs` carries only *references* to those, and the
-- references are enough to see the shape of the failure. No section prose, only its size. No
-- `users`, no `attestations`, no `transactions`, no `portfolios`: the book is not part of this
-- question. Read the output before sending it; it is your data and this is only a default.

\set ON_ERROR_STOP on

WITH run AS (
    SELECT j.* FROM jobs j WHERE j.id = :'job_id'::uuid
)
SELECT jsonb_pretty(jsonb_build_object(
    'exported_for', '§2.1 — five sections fail to draft',
    'job', (SELECT to_jsonb(run) FROM run),

    -- The mandate, minus anything about the operator.
    'request', (
        SELECT to_jsonb(r) - 'user_id' - 'portfolio_context' - 'liquidity_constraint_gbp'
        FROM research_requests r WHERE r.id = (SELECT request_id FROM run)
    ),

    -- Every step, in order: which ran, which retried, what each attempt cost, and the
    -- error payload where there is one. `attempt` is the column the retry question turns on.
    'steps', (
        SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.sequence, s.attempt), '[]'::jsonb)
        FROM job_steps s WHERE s.job_id = (SELECT id FROM run)
    ),

    -- One row per model call. Token counts and `stop_reason` are the two fields that say
    -- whether a draft was starved or truncated; the payload refs stay as ids.
    'agent_runs', (
        SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.created_at), '[]'::jsonb)
        FROM agent_runs a
        JOIN job_steps s ON s.id = a.job_step_id
        WHERE s.job_id = (SELECT id FROM run)
    ),

    -- The sections themselves: status, confidence, the stated reason, and how big the
    -- content came out. `content` is dropped -- its size is the signal, not its prose.
    'sections', (
        SELECT coalesce(jsonb_agg(
            (to_jsonb(sec) - 'content')
            || jsonb_build_object('content_bytes', length(coalesce(sec.content::text, '')))
            ORDER BY sec.position
        ), '[]'::jsonb)
        FROM report_sections sec WHERE sec.job_id = (SELECT id FROM run)
    ),

    -- What the evidence pack actually had in it, which is the other half of the hypothesis.
    'evidence', jsonb_build_object(
        'source_documents', (
            SELECT count(*) FROM source_documents d WHERE d.job_id = (SELECT id FROM run)
        ),
        'claims', (
            SELECT count(*) FROM claims c
            JOIN report_sections sec ON sec.id = c.report_section_id
            WHERE sec.job_id = (SELECT id FROM run)
        ),
        'calculations', (
            SELECT count(*) FROM calculations c WHERE c.job_id = (SELECT id FROM run)
        )
    ),

    'costs', (
        SELECT coalesce(jsonb_agg(to_jsonb(c) ORDER BY c.occurred_at), '[]'::jsonb)
        FROM costs c WHERE c.job_id = (SELECT id FROM run)
    )
)) AS diagnosis;
