-- Which run was it? Step one of two; `export-run-diagnosis.sql` is step two.
--
-- Reads only. Twenty most recent runs, worst first within each date, with the one number
-- that identifies the run roadmap §2.1 is about: how many of its sections did not generate.

\set ON_ERROR_STOP on

SELECT
    j.id AS job_id,
    r.ticker,
    r.company_name,
    j.started_at::date AS ran_on,
    j.status,
    j.total_cost_gbp AS cost_gbp,
    (SELECT count(*) FROM report_sections s WHERE s.job_id = j.id) AS sections,
    (SELECT count(*) FROM report_sections s
      WHERE s.job_id = j.id AND s.status <> 'generated') AS not_generated,
    (SELECT count(*) FROM job_steps st WHERE st.job_id = j.id AND st.attempt > 0) AS retried_steps
FROM jobs j
LEFT JOIN research_requests r ON r.id = j.request_id
ORDER BY j.started_at DESC NULLS LAST
LIMIT 20;
