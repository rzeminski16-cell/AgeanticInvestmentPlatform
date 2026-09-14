# Tracework — page specifications

> Implementation handover for Claude. Read this with 00-design-direction.md and 01-design-system.md. The design system is normative for exact tokens, dimensions, breakpoints, and component behaviour. This document resolves page hierarchy and UX questions; it does not replace the source domain rules.

## How to use this document

Build the pages and fragments named here as server-rendered Jinja templates. Treat each route as an addressable working paper, not as a client-side screen. Use the existing handlers and data where possible. Any item labelled **[NEW SERVER DATA]**, **[NEW SERVER BEHAVIOUR]**, or **[NEW ROUTE]** is a deliberate proposal, not permission to fabricate it in the template.

- **[EXISTING]** means the source requirements say the value or behaviour already exists, although the current template may not receive it.
- **[NEW SERVER DATA]** means the handler needs a new view-model field or query.
- **[NEW SERVER BEHAVIOUR]** means the server flow, validation, or composition needs a small change.
- **[NEW ROUTE]** means a new full-page fallback or fragment endpoint is proposed.
- If a proposed value is unavailable, omit its module or say why it is unavailable. Never replace it with a guessed value, zero, or client-side calculation.

## Product-wide implementation contract

### One hierarchy

Every operational page uses the same reading order:

1. Location and object identity.
2. A plain-language verdict describing what is true now.
3. Any decision the operator must make.
4. Cost and time in context.
5. Evidence connected through the evidence spine.
6. Technical record and hashes on demand.

Raw enum values, workflow keys, UUIDs, hashes, and versions remain available but never become the primary label.

### One responsive frame

- At 960px and wider, use the 232px persistent index and a fluid work area. At 1280px and wider, important research pages may divide the work area into a reading column and the 304px decision column.
- Below 960px, the same navigation DOM becomes a native details/summary disclosure in a compact header. Do not render a second copy of the navigation. The badge slot therefore appears exactly once.
- The page body never scrolls horizontally. Put genuinely wide tables in labelled, focusable scroll regions with a visible edge cue. Preserve the real table rather than turning financial data into unrelated cards.
- At 320px and at 200% zoom, the reading and focus order remains object → verdict → evidence → decision.
- Important objects use Barlow Semi Condensed; reading text uses Source Sans 3; figures and record identifiers use IBM Plex Mono. All files are vendored and hashed. No runtime request leaves the application.

### One evidence language

Use the evidence spine whenever a conclusion has lineage. On wide screens it is a quiet 152px margin rule with linked nodes such as Judgement → Calculated → Source fact. Below 960px it becomes a horizontal, wrapping sequence before the evidence. A provenance class is always a link. Keep provenance class and confirmation state as two separate controls.

### One state language

- **Loading:** authoritative content is server-rendered, so ordinary pages do not show speculative skeletons. For a delayed fragment, retain its labelled container and say “Loading preview…” or leave an optional count slot empty. Never paint a guessed status.
- **Empty:** say what was checked, why nothing is present, and the next available action. Never use “No results”.
- **Partial:** name exactly what is missing and suppress any total made incomplete by it.
- **Error:** name the failed dependency or operation and the recovery. Preserve any valid, independent content.
- **Refused:** explain the governing rule and the safe next action. Never make a refusal look like a field-format error.
- **Not found / not yours:** evidence and report resources return the same 404 for both cases.

### Interaction rules

- Every state-changing control is a real form and server POST. Every approval is followed by a redirect. There is no optimistic approval state.
- Every enhanced drawer trigger is a working link first. With scripting off it opens the full page.
- Search and table filters only hide rows already rendered. Render the enhanced control hidden and let the existing table script reveal it. With scripting off the full table remains.
- JavaScript may own focus, drawer chrome, a running dot, an elapsed clock, and row filtering. It may not calculate, total, round, format, or infer a figure.
- A view worth returning to uses a GET and records its state in the URL.
- Use UK English in all interface copy.
- Navigation remains registry data rendered by a loop. Its presence never grants access; each route keeps its own authorisation/dependency rules.
- Theme is server-stamped from the preference cookie before first paint. System remains the absence of the attribute, so no head script or palette flash is introduced.

### Accessibility acceptance criteria

- Meet WCAG 2.2 AA in light, dark, and system modes. Small text reaches 4.5:1; large text, control boundaries, focus indicators, and meaningful graphics reach 3:1.
- Give every interactive element a designed two-colour focus indicator visible against both the component and its surround. Do not rely on the browser default.
- Minimum pointer target is 24 by 24 CSS pixels; primary actions are at least 44px high.
- One non-empty h1 per page; headings do not skip levels. A variable object name must have a fallback.
- Form summaries link to associated field errors. Keep the aria-live error container itself in the DOM and replace only its contents.
- Status and sign are written as well as coloured. Nothing depends on hover.
- Evidence precedes gate decision controls in DOM and focus order, even when CSS makes the decision column sticky.
- Respect reduced motion. The running heartbeat becomes a static “Working” marker and loses no information.

---

# 1. Shell and menu

**Routes/scope:** every page; shell badge and drawer fragments; theme and guidance POSTs.

## Job

Keep the operator oriented and one action from any top-level destination on a wide screen, while carrying navigation, preferences, disclaimer, badge slot, and the single accessible drawer at every width.

## Wide layout and hierarchy

Use one persistent 232px left index:

- Top: Tracework wordmark and the small descriptor “Equity research instrument”.
- Middle: Overview; Research with Requests, Reports, Skills, Knowledge; Portfolio; Platform with Settings, Costs, Health, API.
- Requests carries the single out-of-band count slot.
- Bottom: Appearance disclosure and guidance toggle.

After the main content, the shared shell footer carries the full disclaimer once, application version, and optional build identity. No page owns or duplicates it.

The work area begins with a 44–52px context bar containing the visible breadcrumb, page-level primary action when one exists, and no duplicate product name. The breadcrumb is not a substitute for h1; it provides location while the page provides identity.

Render the current nav item with verification ink, a left ledger rule, aria-current="page", and written context. Do not use a filled rounded pill.

## Narrow layout

Render the same nav inside a native details/summary menu in a sticky compact header. Summary copy is “Menu — {current section}”. The open panel occupies the viewport below the header, scrolls internally, and contains navigation followed by preferences. The page breadcrumb remains visible beneath the header; the shared disclaimer stays in the page footer.

## Interactions

- Nav links always navigate normally.
- Theme has three submit buttons: Light, Dark, System. System remains the absence of a theme attribute.
- Guidance is a submit button, not save-on-change. Guidance content is visible inline content, not a title attribute.
- Drawer triggers remain ordinary href links with htmx enhancement. One right-side drawer, one focus trap, one overlay. Escape and Close return focus to the exact trigger.
- Badge request fills every registered slot out of band, but this shell contains the Requests slot only once.
- A zero count renders no badge. Badge fetch/cache failure leaves the slot empty and never delays navigation.

## States and sample copy

- **Badge loading/failure:** empty slot; navigation stays complete. Spoken label after success: “3 runs waiting for your approval.”
- **Menu open:** current item and current section are both apparent.
- **Drawer loading:** drawer title from the trigger, body says “Loading preview…”. If the fragment fails, keep the full-page link: “The preview did not load. Open the run instead.”
- **Database unavailable:** shell and registry navigation still render.
- **Refused preference redirect:** ignore an unsafe next destination and return to Overview.
- **Sample disclaimer:** “This is not investment advice.”

## Responsive/no-JavaScript

The menu remains details/summary and both preferences remain ordinary forms. Drawer links become page loads. No duplicate desktop/mobile nav markup. A wide-screen CSS rule presents the details content persistently; narrow CSS restores disclosure behaviour.

## Accessibility

Give the nav a label, sections real headings, the breadcrumb an aria-label, the badge slot aria-live="polite", and the drawer role="dialog", aria-modal, and aria-labelledby in server markup. The overlay is never focusable. Do not put legal copy between the wordmark and first navigation item.

## Data contract

- **[EXISTING]** shell navigation registry, active key, current path, theme, guidance, disclaimer, app version, CSRF token.
- **[EXISTING]** badge count, spoken label, title.
- **[NEW SERVER DATA]** breadcrumb segments with label and href, derived from route/object context rather than parsed in the browser.
- **[NEW SERVER DATA]** optional page action label and href or form descriptor supplied by the page handler.

---

# 2. Overview

**Routes:** / and permanent redirect /overview.

## Job

Answer “what needs me now?” above the fold, while still explaining the platform’s shape to a first-time operator and still rendering useful content without the database.

## Wide layout and hierarchy

1. h1 “Today’s work” and a verdict line such as “Two decisions are stopping research; one run needs diagnosis.”
2. **Attention ledger**, grouped in this fixed order: Waiting for you, Needs diagnosis, Not started. Never merge them into a date-sorted feed.
3. A restrained context strip for spend this month and other counts that do not merely duplicate the visible ledger.
4. **Working tools** as compact launcher rows with direct actions.
5. **Planned instruments** as a quieter requirements index. Preserve every specific needs explanation; do not use progress bars or “Coming soon”.
6. Build identity in the shared footer.

Each attention row shows the object, the named reason, age, spend against ceiling, current phase, Preview, and Open. Preview opens the shared drawer.

The current registry has two working and seven planned tools. The working section must grow cleanly to six; the context strip must hold three to eight distinct counts; each severity group remains bounded to eight rows and states the exact remainder.

## Narrow layout

Attention groups stay first. Each row becomes a ledger block with title and reason first, then age/cost/phase in a two-column definition list, followed by Preview and Open. Counts scroll neither horizontally nor as a carousel. Working tools precede a collapsed native details section titled “Planned instruments”.

## Interactions

All controls are links. The page has no data-entry form. A bounded group ends with a real link such as “6 more runs are waiting at a gate”.

## States and sample copy

- **Ordinary:** “Contoso plc is waiting for you to confirm the peer set.” Secondary line: “Waiting 2 days · £6.40 of £8.00 · Valuation phase.”
- **Caught up:** “Nothing is waiting. No run is stopped at a gate, nothing failed, and every saved request has been started.” Action: “Commission research”.
- **First run:** “Start with two things: connect a model provider, then commission your first report.” Actions link to Settings and New request.
- **Partial provider failure:** render an item in the affected severity group: “Research attention could not be checked. The database query failed; reload or check Health.” Never silently imply zero work.
- **Database not reachable:** launcher remains. “The database is not reachable. Start it with just up, then reload. Health shows which dependencies are answering.”
- **Schema behind:** name the missing/outdated objects, not a count.
- **Loading:** launcher renders immediately; optional count slots can be blank until filled.
- **Refused:** not applicable to this read-only front door. A registry tool with no usable route is planned, not refused.

## Responsive/no-JavaScript

All rows and actions are ordinary links. Drawer previews navigate to their full destination without scripting. The launcher depends only on the registry and remains above the database error boundary.

## Accessibility

Each severity group has an h2 and descriptive count. Do not announce the same count twice. Preview and Open labels include the company in their accessible name. Time and spend are text, not tooltips.

## Data contract

- **[EXISTING]** registry tool label, status, href, summary, needs, action label/href.
- **[EXISTING]** attention title, detail, href, action, preview href, tool, severity; bounded remainder count.
- **[EXISTING]** spend this month and period label; run spend and request ceiling exist in the domain.
- **[NEW SERVER DATA]** overview_verdict assembled from complete attention-provider results.
- **[NEW SERVER DATA]** waiting_since and server-rendered waiting_duration label.
- **[NEW SERVER DATA]** spent_display, ceiling_display, and spent_fraction_semantic such as “near ceiling”; all formatted server-side.
- **[NEW SERVER DATA]** current_phase_label and decisions_remaining_range, with conditional gates described as a range rather than a false exact count.
- **[NEW SERVER DATA]** first_run boolean based on the absence of requests, runs, portfolio, and spend, plus provider_configured.

---

# 3. Research requests

## 3.1 Requests list

**Routes:** /requests and /requests?archived=1.

### Job

Help the operator find a commission by company, conclusion-relevant status, date, and cost, then offer the next valid action without opening every row.

### Wide layout and hierarchy

Lead with h1 “Research requests”, a one-line verdict (“4 active · 1 waiting for you”), and “New request”. Follow with GET filters for Active/Archived, company search, status, and date order. The ledger table columns are Company, Mandate date, Latest run, What is true now, Spend, and Actions. Ticker/exchange sit beneath company; depth sits beneath mandate date.

Use sentence statuses such as “Waiting for your plan decision”, “Report approved”, and “Draft — never run”. Keep raw enums in an expandable technical record only.

Archive is a small POST button and is immediately reversible. Remove is a link to the confirmation page. Row click is not the only way in; the company/request title is a real link.

### Narrow layout

Keep one semantic table in a focusable bounded scroll region if comparison is the task. At very narrow width, visually prioritise Company, What is true now, Spend, and Actions while allowing the remaining columns to be reached by horizontal scroll inside the table. Never make the page itself scroll sideways.

### States and sample copy

- **Populated:** “Contoso plc — Waiting for your review of the draft · Last activity 24 Aug · £6.40.”
- **Empty active:** “No active requests. Commission a company when you have a question worth answering.”
- **Empty archive:** “The archive is empty. Archived requests will remain recoverable here.”
- **Partial:** if latest-run cost cannot be read, show “Spend unavailable” on that row and keep independent mandate facts.
- **Error:** fail the page loudly if the request list itself cannot be trusted; do not render an empty table.
- **Refused:** ownership failure uses the same not-found response as an unknown id where applicable.
- **Loading:** full server response; no speculative rows.

### Interactions

Filters are GET parameters so the view is bookmarkable. If enhanced row filtering is retained, it only hides rendered rows and is hidden until script reveals it. Archive is POST with CSRF and a clear “Archived” result; Restore uses the same model.

### Responsive/no-JavaScript and accessibility

GET filters and all actions work without scripting. Give the table a caption that explains archived versus active. Use row headers for company names. Archive/Remove targets meet 24px minimum and have object-specific accessible names.

### Data contract

- **[EXISTING]** company, ticker, exchange, as-of date, depth, request status, actions.
- **[NEW SERVER DATA]** created_at_display, latest_run_at_display, latest_run_plain_status.
- **[NEW SERVER DATA]** cumulative_spend_display and whether spend is complete.
- **[NEW SERVER DATA]** attention_reason and pending_gate_label.
- **[NEW SERVER BEHAVIOUR]** server GET filters/order if only client filtering exists today.

## 3.2 New and edit request

**Routes:** /requests/new and /requests/{id}/edit.

### Job

Let a new operator commission a defensible run by making the consequential choices first, while keeping refinements available and preserving every typed character after rejection.

### Wide layout and hierarchy

Use a 7/5 split working sheet:

- Main column: “Company”, “Date and hindsight”, “Depth and spending”, and the large “Questions this report must answer” field.
- Margin column: a live-looking but server-rendered mandate summary, cost guidance, supported universe, and the submit action.
- Below the essentials: native details titled “Refine this mandate — optional”, containing portfolio context, risk/ESG/liquidity, excluded domains, reporting currency, and horizon label.

The essential path visibly contains:

1. Company identity: name, ticker, and supported exchange.
2. As-of date and a two-option point-in-time decision. Default: “Use only information available by this date — recommended.” Alternative: “Allow later-published sources”, followed by a consequence statement.
3. Depth and cost ceiling together, with typical cost range.
4. Base currency and investment horizon, prefilled but editable.
5. Questions, optional but visually promoted because they shape the plan.

Do not call required supporting fields optional. If base currency or horizon has a server default, label the default and retain the field in the essential sheet.

### Narrow layout

Use one column in the same order. The mandate summary follows the essential fields and precedes the submit button. Keep “Refine this mandate” as native details; it is closed only when every hidden field is optional or already holds an explicit server default.

### Interactions

- Server validation only; retain novalidate.
- On rejection, scroll/focus to an error summary whose links target fields. Return every typed value, including textarea line breaks.
- Present schema errors together. If the next submission reaches service rules, say “The form is now structurally complete. Two mandate rules still need attention” so the second round reads as progress.
- Submit label: “Save request”. Starting a run remains a separate explicit POST from detail.
- Point-in-time uses radio buttons with explanatory labels, not a lone checkbox.
- Consequence copy for the non-default point-in-time choice is explicit: “Allow later-published sources. This run can no longer be treated as a clean historical replay.”

Preserve the complete field contract:

- Company: required name, ticker, and exchange; optional ISIN. Ticker accepts letters, digits, dot, and hyphen. ISIN check digit is validated when present. The exchange list contains US and UK main markets only and says that OTC venues are unsupported.
- Timing/currency: required as-of date and base currency; optional reporting currency. The as-of date cannot be in the future.
- Mandate: required horizon of 1–240 months and depth; optional horizon label; point-in-time defaults on.
- Portfolio context: optional current weight, maximum weight, and benchmark.
- Priorities: optional risk tolerance, ESG weighting, liquidity constraint, questions, and excluded sources.
- Cost ceiling: required and enforced by the server.
- Percentage controls accept human percentages such as 2.5 and store fractions such as 0.025. Never ask for fractions. Questions and excluded domains remain one item per line.

### States and sample copy

- **Blank:** “Four decisions shape the run. Everything else refines it.”
- **Edit:** “Editing is available until the first run starts.”
- **Rejected, round one:** “Check 3 fields before this request can be saved.”
- **Rejected, round two:** “The details are valid. Resolve these 2 mandate rules.”
- **CSRF expired:** preserve all input and say “This page expired before it was saved. Review the unchanged form and save again.”
- **Immutable/refused:** “This request has a run, so its mandate is now part of the audit record. Start a new request to change it.”
- **Partial cost guidance:** “Typical cost is unavailable on this setup. Your ceiling will still be enforced.”
- **Loading:** full server form; no client-resolved company is required.

### Responsive/no-JavaScript

All disclosures are native. The same POST returns an error fragment with htmx or the full page without it. Do not require an autocomplete to identify a company; native text/select fields remain sufficient.

### Accessibility

Fieldsets have legends that describe a decision, not a database group. Associate hints and errors with aria-describedby. Radio consequence text is part of each accessible description. Never hide required fields behind a closed disclosure without a valid server default.

### Data contract

- **[EXISTING]** all twenty request fields and their service/schema validation.
- **[EXISTING]** depth-specific historic costs exist in the platform.
- **[NEW SERVER DATA]** typical_cost_low_display, typical_cost_high_display, sample_count, and period/setup qualifier, all server-rendered.
- **[NEW SERVER DATA]** default_base_currency and default_horizon_months with visible source, if defaults are adopted.
- **[NEW SERVER BEHAVIOUR]** map the existing point-in-time boolean to two explicit radio values without changing its default-on meaning.
- **[NEW SERVER DATA]** validation_stage: schema or mandate, used only to explain two-round progress.

## 3.3 Request detail

**Route:** /requests/{id}.

### Job

State what this commission is, what happened most recently, what it cost, and the one next valid action.

### Wide layout and hierarchy

Lead with company identity and the verdict: “Draft — no data fetched and nothing spent”, “Run waiting for your peer-set decision”, or “Report approved on 24 August”. Put the current run sheet first, with spend/ceiling, phase, last activity, and Open/Start action. Put the saved mandate beneath as grouped definition lists. Technical id and immutable record details sit in a closed “Technical record” disclosure.

Edit is visible only before any run. Delete links to a full confirmation page only for a never-run draft. For a completed/failed run, “Start a new run” is explicit about superseding and expected repeat cost.

### Narrow layout

Verdict, next action, cost, and run status precede the mandate. Definition lists stack label over value. Avoid a grid of twenty equally loud facts.

### States and sample copy

- **Never run:** “Saved as a draft. Nothing has been fetched and nothing has been spent.” Action: “Start the run”.
- **Running:** “Research is working on the draft. £3.85 of £8.00 spent.”
- **Awaiting approval:** “Nothing else happens or is spent until you decide.”
- **Failed:** “The run stopped while challenging the thesis. Starting again repeats the run and its cost.”
- **Succeeded:** “Approved report available.” Actions: “Read report”, “Open run”.
- **Archived:** retain read access and offer Restore.
- **Partial:** if spend is unavailable, say so without suppressing the status.
- **Error:** if the request cannot be loaded, do not show a shell-only empty detail.
- **Not found/refused:** dedicated, plain-language page; ownership must not leak.

### Interactions

Start and supersede are POST forms. Edit and Remove are links. Preview may use the shared drawer but must have a full page destination.

### Responsive/no-JavaScript and accessibility

All state changes are forms; all reads are links. Status sentence is the h1’s immediate description. Use definition lists for mandate values and preserve line breaks in questions/exclusions.

### Data contract

- **[EXISTING]** saved mandate, last-run status, start/edit/delete/supersede eligibility.
- **[NEW SERVER DATA]** plain_status, current_phase_label, last_activity_display.
- **[NEW SERVER DATA]** spent_display, ceiling_display, and repeat_cost_guidance for superseding.
- **[NEW SERVER DATA]** report conclusion summary when an approved report exists.

## 3.4 Remove request

**Route:** /requests/{id}/remove.

### Job

Make the irreversible boundary legible: what disappears, what survives, and why the server may refuse.

### Layout and hierarchy

Use a narrow reading sheet, not a modal. Lead with “Remove the Contoso plc request?” and a consequence verdict. Two lists follow:

- **Will be removed:** request and the exact dependent counts supplied by the server.
- **Will remain:** audit chain, spend ledger, and artefacts.

Place “Remove request” in a danger region after the lists and optional explanatory text. “Keep request” is the first/safer action and returns to detail. Do not require typing a magic phrase unless the existing service requires one.

### States and sample copy

- **Confirmation:** “This cannot be undone. It does not erase the audit chain, spend ledger, or stored artefacts.”
- **Refused:** “This request has a run and is now an audit record. It cannot be removed; archive it instead.”
- **Stale:** “The request changed after this page was opened. Nothing was removed. Review its current state.”
- **Partial/error:** if counts cannot be established, refuse to present the destructive POST. “Impact could not be counted, so removal is unavailable.”
- **Not found/not yours:** same response.
- **Loading:** full server page only.

### Responsive/no-JavaScript and accessibility

The action is a CSRF-protected POST and works without scripting. Move focus to the confirmation heading after navigation. Describe the danger in text, not red alone. The cancel path is a normal link.

### Data contract

- **[EXISTING]** destruction counts and surviving-record guarantee.
- **[NEW SERVER DATA]** request_version or equivalent stale-confirmation token if removal does not already re-check current eligibility.

---

# 4. Run console

**Route:** /runs/{job_id}.

## Job

Answer, in under two seconds, whether the run is alive, whether it needs a decision, and how much has been spent against the limit.

## State-adaptive wide layout

Keep one URL and one DOM, but change emphasis by authoritative server state:

- **Running:** the current-work verdict dominates. Show “Drafting the report”, elapsed time in this step, server last heard from, normal duration guidance, and £3.85 of £8.00. The full step ledger follows.
- **Awaiting approval:** the decision request dominates. Show what is being decided, why the run stopped, nothing-further-spent reassurance, and “Review {gate}”. The journey and evidence remain beside it.
- **Terminal:** the outcome dominates. On success, lead to the report and evidence. On failure, lead with the failed step, stable error code, recovery route, and repeat-cost consequence.

Within the shell’s 232px index layout, use:

1. Object identity: company, ticker/exchange, as-of date.
2. Plain-language verdict and state sentence.
3. A **run journey** showing real phases and actual decision interruptions. Plan and Review are known anchors. Financials, Sector, Peers, Themes, and Assumptions appear as decision nodes only when triggered; before then they are grouped in plain copy as possible checks, never drawn as five guaranteed future steps.
4. Current work or current decision.
5. Spend ledger: spent, ceiling, remaining, and cap scope when stopped.
6. Evidence index with honest counts.
7. Full declared-step ledger.
8. Technical record and cancellation.

At 1280px and wider, use the design-system reading column plus 304px sticky run margin. The left is current work plus declared steps; the right contains decision, cost, liveness, and evidence counts. From 960–1279px the margin follows the work in document flow. The journey spans the working area near the top.

## Narrow layout

Use one column: verdict → current decision/work → cost → journey → evidence → steps → cancellation. The phase journey becomes a wrapping horizontal ledger, not a progress percentage. All nineteen declared steps remain present; each row opens a native details block for key, timing, cost, and error.

## Interactions

- Event stream may update the running marker, elapsed clock, and server-seen label. When authoritative status diverges, reload; do not build banners or rows in JavaScript.
- Evidence entries are links with counts: “Sources 18”, “Claims 42”, “Valuation ready”. A zero/early entry says “Sources not gathered yet” and remains a link to the honest empty page if useful.
- Cancellation form asks for an optional reason and posts “Request stop”. The result state is “Stopping after the current step”, never “Stopped”.
- “Review gate” is a normal link. “Start a replacement run” is a POST from the request flow and warns about repeated cost.
- “Reproduce this run” stays a POST because it writes verifier results.

## States and sample copy

- **Queued:** “Queued. A worker normally begins within a few seconds.” Every declared step is visible and not started.
- **Running:** “Working: drafting the report · 04:18 in this step · server last heard from 12 seconds ago.”
- **Long-running but healthy:** “This step often takes 2–5 minutes. It has been running for 7 minutes; the server is still responding.”
- **Heartbeat missing:** “The page has not heard from the server for 2 minutes. The recorded run state is still ‘Drafting’; check the worker terminal before restarting anything.”
- **Awaiting approval:** “Your peer-set decision is required. Nothing else happens or is spent until you decide.”
- **Stopped on per-run budget:** “The next step would take this run past its £8.00 ceiling, so it stopped before making the call. Raise this request’s cap or stop here.”
- **Stopped on monthly budget:** “The monthly platform budget stopped this run. Changing this request’s cap will not release it.”
- **Stopping:** “Stop requested at 14:32. The current filing fetch will finish before the run stops.”
- **Cancelled:** show recorded time and reason.
- **Failed:** “Challenging the thesis failed. Code MODEL_TIMEOUT. A replacement repeats completed work and may cost about £6–£9.”
- **Succeeded:** “Report approved and frozen. Read the report or inspect its evidence.”
- **Partial:** unknown evidence count says “Count unavailable”, not zero. Unknown spend suppresses remaining-budget arithmetic.
- **Error:** a console that cannot load authoritative run state fails loudly; never show a fabricated queued state.
- **Refused/not found:** same non-enumerating response.
- **Loading:** server first paint is complete. The only moving state is explicitly labelled live enhancement.

## Responsive/no-JavaScript

Keep the meta refresh inside noscript. It reloads running/non-terminal pages on a slower interval. If the event stream fails, use the same reload fallback. Every banner, step row, action, and figure exists in the first server response.

## Accessibility

The live marker includes the word “Working”; pulsing is supplementary. Update elapsed time no more often than needed and do not announce every tick. Announce only meaningful state changes through a polite live region. Step keys are secondary monospace text; human step names are headings. The journey is an ordered list with written states. Reduced motion removes the pulse.

## Data contract

- **[EXISTING]** company, ticker, as-of date, workflow version, status, total spend, all declared steps, per-step status/time/cost/error, pending gate, budget-stop scope, cancellation details.
- **[EXISTING]** request cost ceiling exists but is not currently shown.
- **[NEW SERVER DATA]** plain_status, human_step_label, phase_label, human_error_recovery.
- **[NEW SERVER DATA]** ceiling_display, remaining_display, and budget_utilisation_label; values formatted server-side.
- **[NEW SERVER DATA]** evidence counts/readiness for sources, claims, and valuation.
- **[NEW SERVER DATA]** gate_journey entries with state: passed, current, conditional_possible, skipped_or_not_required, upcoming_always.
- **[NEW SERVER DATA]** expected_step_duration_copy and replacement_cost_range with provenance/qualifier.

---

# 5. Research gates

## 5.1 Shared gate anatomy

**Routes:** /runs/{id}/plan, /financials, /sector, /peers, /themes, /assumptions, /review.

### Job

Let the operator decide one consequential question from sufficient evidence, with the decision bound to exactly the payload they reviewed.

### Shared wide layout

All seven gates use the same skeleton:

1. Breadcrumb and company/run identity.
2. **Decision question** as h1, phrased in the operator’s language.
3. One-sentence current verdict and “why this matters”.
4. Gate journey: actual passed decisions, this decision, and the next known phase. Conditional gate types are described as possibilities within their phase and are not rendered as a fixed seven-step wizard.
5. Attention summary: material exceptions first, cost context second.
6. Evidence body with the page-specific ordering below.
7. A 304px sticky decision column at 1280px and wider. From 960–1279px it follows the evidence in the main flow.

The decision column contains:

- “Decision required” and a one-sentence consequence.
- £spent of £ceiling, plus server-supplied likely additional cost where available.
- A verification statement: “Your decision is bound to exactly the content shown here. If it changes before the server records your choice, this approval will be refused.”
- Optional notes, maximum 4,000 characters.
- Primary “Approve and continue”.
- Secondary but clearly consequential “Reject and stop this run”.
- A link back to the run.

Evidence remains before the decision form in DOM and focus order. CSS grid places the later form in the sticky column. Buttons either live inside the real gate form or reference it through the form attribute; CSRF and payload hash travel with the POST.

### Shared narrow layout

Use one column. After the verdict, provide a plain anchor “Jump to the decision”, but render the actual form after all evidence. The gate journey wraps above the evidence. Cost repeats only once, inside the final decision block.

### Shared interactions

- A gate remains its own bookmarkable URL, never a modal or a client-held wizard step.
- Gate POST returns server truth and redirects to the console.
- Approval records the decision and queues the run; it never executes remaining research inline in the browser request.
- No optimistic checkmark, toast, or disabled-page state claims success.
- A decided gate has no decision form.
- Technical payload digest is available under “What this decision proves”, abbreviated visually and expandable without hiding the full value from copy/select.
- Page-specific row actions use separate sibling forms. Never nest a form inside the outer gate form.
- Filters are progressively revealed and only hide rendered rows.

### Shared states and copy

- **Pending:** “Decision required. The run is paused and is not spending.”
- **Already approved:** “Approved by {name} on {date}. A decision is not a state to re-assert; changing it needs a new run.”
- **Already rejected:** “Rejected and stopped by {name} on {date}.”
- **Not reached:** “This decision is not available yet. Return to the run to see what is happening.”
- **Nothing to approve:** name the missing producer output; never show a live approval form.
- **Stale payload/refused:** “The proposal changed after this page was opened. Nothing was approved. Review the current version and decide again.”
- **Partial:** show the missing evidence and disable the server-side eligibility for approval; do not merely grey the button in the template.
- **Error:** “The decision record could not be loaded, so no action is available.” Link back to run/Health as appropriate.
- **Loading:** full server page. A button may show “Recording decision…” only as htmx chrome; without scripting the normal POST/redirect occurs.
- **Not found/not yours:** identical response.

### Shared accessibility

Use a real ordered list for the journey, a fieldset/legend for approve versus reject, and an explicit label/counter description for notes. The rejection control is not made hard to find; its lower visual weight comes from placement and fill, not target size or contrast. Stale-payload errors receive focus at the summary heading. Full rationales are reading-width text.

### Shared data contract

- **[EXISTING]** gate kind, exact proposal payload, payload hash, decision state, notes, CSRF, run status.
- **[NEW SERVER DATA]** gate_question, consequence_copy, why_it_matters_copy, plain gate state.
- **[NEW SERVER DATA]** gate journey entries and decision attribution/time.
- **[NEW SERVER DATA]** spent_display, ceiling_display, likely_increment_display and method/uncertainty label.
- **[NEW SERVER DATA]** approval_eligibility plus explicit missing prerequisites; eligibility remains server-owned.

## 5.2 Plan gate — approve the research plan

**Route:** /runs/{id}/plan. Always fires.

### Decision question and hierarchy

h1: “Does this plan investigate the right things?” Lead with the source plan because this is the cheapest moment to catch a missing filing.

Evidence order:

1. **Sources the run intends to use**, grouped by filing, market data, and secondary source. Flag expected core filings that are absent.
2. What the run intends to answer, including the operator’s own questions.
3. Proposed report sections and built-in sections.
4. Risks and what the planner may get wrong.
5. Applied skills, each linked to its effective policy.
6. Cost/time estimate and what approving commits.

### Interaction and states

Rows are links to source descriptions or skill details where available. If the source list is empty or lacks an expected filing, the attention summary says so without deciding on the operator’s behalf.

- **Sample copy:** “Read the source plan first. If the annual report or latest interim filing is missing, this is the least expensive point to stop.”
- **Partial:** missing cost estimate is named; the plan may still be reviewable if server rules allow.
- **Refused:** stale plan hash follows shared stale-payload state.

### Responsive/no-JavaScript and accessibility

Source groups are headings and lists, not a dense multi-column card grid. Native details may collapse built-in sections and skill policy, but intended sources and risks stay open. All links and the gate form work without scripting.

### Additional data

- **[EXISTING]** plan prose, selected/built-in sections, intended sources, planner risks, applicable skills, cost/time estimate.
- **[NEW SERVER DATA]** source_kind_label and expected_core_source flag/reason.
- **[NEW SERVER DATA]** approving_commits_estimate_display, clearly labelled estimate rather than ceiling.

## 5.3 Financials gate — proceed with unmapped filing tags

**Route:** /runs/{id}/financials. Conditional.

### Decision question and hierarchy

h1: “Can this analysis proceed without these filing lines?” Lead with the unmapped rows sorted by materiality.

1. Verdict: “3 unmapped tags; the largest is 6.2% of the biggest mapped line.”
2. Table of unmapped tag label, largest figure, period, and share of biggest mapped line.
3. “What did map” in native details, closed by default.
4. Extractor complaints.
5. Decision.

Never substitute latest observation for largest observation.

### Interaction and states

Progressively reveal a filter for long rendered tables. Approval means the operator accepts proceeding without the unmapped lines; it does not assert that the tags are immaterial in general.

- **Empty:** if there are no unmapped tags, this gate should not fire. If opened from a stale link, say “No financial extraction decision is required.”
- **Sample copy:** “The largest unresolved line is 6.2% of the largest mapped line. Decide whether that gap could change the analysis.”
- **Partial/refused:** if materiality comparison could not be computed, show the cause and withhold the gate form until server eligibility is restored.

### Responsive/no-JavaScript and accessibility

The table scrolls inside its region. Keep the Materiality column visible first after the row label. Use a caption explaining “largest, not latest”. Scripting off shows all rows and no filter.

### Additional data

- **[EXISTING]** unmapped tag label, largest value, period, materiality share; mapped comparison; extractor complaints.
- **[NEW SERVER DATA]** human complaint category and recommended inspection link, if not already supplied.

## 5.4 Sector gate — confirm the analysis mandate

**Route:** /runs/{id}/sector. Conditional.

### Decision question and hierarchy

h1: “Should this company be analysed as a {proposed sector}?” Lead with consequences, not the label.

1. Verdict and classification.
2. **What approving changes:** methods enabled, methods refused, and why.
3. Why the classification was proposed, with linked evidence spine.
4. What analysis remains available.
5. Decision.

For a bank, state directly that discounted cash flow and classified-current-asset analysis are refused rather than weakened.

### Interaction and states

Methods use written availability: “Available”, “Refused for banks”, “Not applicable”. There is no control to selectively override a method because confirmation grants one bounded mandate.

- **Sample copy:** “Confirming Bank permits residual-income valuation. Discounted cash flow will be refused, because its cash-flow premises do not apply.”
- **Partial:** missing rationale prevents approval if the server cannot bind the operator to an explained classification.
- **Refused:** no UI suggests bypassing a prohibited method.

### Responsive/no-JavaScript and accessibility

Use a definition list for methods, not colour-only chips. Consequences precede classification evidence at every width.

### Additional data

- **[EXISTING]** proposed sector, rationale, analysis consequences, methods remaining.
- **[NEW SERVER DATA]** method name, availability label, and refusal reason as structured rows if currently embedded in prose.

## 5.5 Peer gate — confirm the comparison set

**Route:** /runs/{id}/peers. Conditional.

### Decision question and hierarchy

h1: “Is this the right comparison set?” Lead with accepted peer count and coverage limitation.

1. Proposed peers, one reading-width row each: identity, full rationale, selection basis.
2. “Proposed but not used”, grouped by refusal reason and visually adjacent but outside the approved-payload boundary.
3. Scope notice: confirming records the set; it fetches no peer filings or prices.
4. Decision.

Make the hashed boundary visible with a labelled rule around “What you are approving”. Put exclusions under “Context — not part of this approval”.

### Interaction and states

No rationale truncation, hover expansion, or “read more” that hides the decision basis. Links may open peer/company context in the drawer with full-page fallback.

- **Empty accepted set:** “No proposed peer could be resolved. You may approve an empty set only if the server says the workflow can proceed; no multiples will be implied.”
- **All excluded:** explain that this can be expected when filings/prices were not acquired.
- **Sample copy:** “Eight peers were proposed; none has the filings and prices needed to compute a multiple. Confirming records the empty usable set.”
- **Partial/refused:** unresolved identity carries its reason; never silently drop it.

### Responsive/no-JavaScript and accessibility

Peer rationales stay full length and reading width. Each peer is an article with a heading; refusals are grouped lists. Drawer enhancement is optional and link-first.

### Additional data

- **[EXISTING]** proposed peers, full rationales, basis, exclusions/refusal reasons.
- **[NEW SERVER DATA]** approved_payload_boundary descriptor so the template can label what the hash covers without recomputing it.
- **[NEW SERVER DATA]** per-peer evidence availability summary if the handler can distinguish filing and price coverage.

## 5.6 Themes gate — confirm how the company is filed

**Route:** /runs/{id}/themes. Conditional.

### Decision question and hierarchy

h1: “Do these themes describe how this company should be found?” This is the smallest gate; do not inflate it.

1. One-sentence consequence for future knowledge/library use.
2. Theme entries with full rationale and evidence link.
3. Decision.

### States and sample copy

- **Empty:** “No themes were proposed, so there is nothing to confirm.” No live form unless an empty set is a valid explicit proposal.
- **Sample copy:** “These themes shape how later searches and comparisons find the company; they do not change the current valuation.”
- **Partial/refused/error:** use shared states. A theme without rationale is visible and ineligible rather than silently omitted.

### Responsive/no-JavaScript and accessibility

Single reading column plus shared decision area. Keep all rationales open; no cards or carousels.

### Additional data

- **[EXISTING]** theme label and full rationale.
- **[NEW SERVER DATA]** future_use_copy or affected_surface labels if not already expressible from the theme model.

## 5.7 Assumptions gate — confirm valuation inputs

**Route:** /runs/{id}/assumptions, with the parallel per-request assumptions surfaces.

### Decision question and hierarchy

h1: “May the valuation rest on these assumptions?” Lead with unresolved counts and block approval when values or confirmations required by policy are absent.

1. Attention summary: outstanding, unconfirmed, refused, and not-derived counts.
2. Proposed assumptions table/ledger. Each row shows name, server-rendered value, justification, proposer, provenance-class link, and separate confirmation state.
3. Outstanding inputs with Create action.
4. Refused and Not derived with reasons.
5. Outer gate decision, only after row-level work.

### Forms and interactions

Do not nest forms. Render each Confirm, Amend, and Create as its own sibling POST form. Render the outer gate form after all rows. After a row action, the server re-renders from current assumption rows, never from frozen step output.

- Confirm carries the hash of the list displayed for that row decision.
- Amend changes a value but does not silently confirm it.
- Create produces a proposal, never a confirmation; the operator then confirms separately.
- A value editor may appear in the shared drawer only if its trigger is a real full-page link and the full route remains complete without scripting.

### States and sample copy

- **Ready:** “All 12 required inputs have values; 2 still need your confirmation.”
- **Outstanding:** “3 inputs still have no value. The valuation cannot be approved yet.”
- **Refused:** show the assumption name and rule; never default it.
- **Not derived:** explain why derivation was unavailable.
- **Saved row action:** “Value proposed. It is not confirmed yet.”
- **Stale row list:** “Assumptions changed while this page was open. No confirmation was recorded.”
- **Partial/error:** suppress outer approval whenever the server cannot establish current rows.
- **Sample copy:** “Creating a value and agreeing that the valuation may rely on it are separate decisions.”

### Responsive/no-JavaScript

At narrow width, each row is a definition list followed by its relevant form. At wide width, use a table only if the full justification remains readable; otherwise use ledger rows. All forms post and redirect without scripting.

### Accessibility

Associate each row action with the assumption name in its accessible name. Keep two provenance axes separate and linked. After a row POST, focus the result summary, then offer an anchor back to that assumption.

### Additional data

- **[EXISTING]** current assumption rows, categories, value, justification, proposer, proposal/confirmation hashes, eligibility.
- **[NEW SERVER DATA]** server-rendered category counts and plain eligibility reason.
- **[NEW SERVER DATA]** return_anchor for row actions so a full-page redirect can restore reading context without client state.

### Per-request assumptions deviation

On /requests/{id}/assumptions and /requests/{id}/assumptions/{id}, reuse the same assumption rows, provenance, Confirm/Amend/Create forms, and current-row rendering. The job is to prepare and inspect request assumptions outside a stopped run, so omit the outer gate decision, gate journey, and “run is paused” copy. Lead with “Assumptions available to the next run” and state whether each is proposed, confirmed, outstanding, refused, or not derived. A single-assumption page shows its full justification and history. Empty, partial, error, stale, and refusal behaviour follows the gate rows; no client state or nested forms are introduced.

## 5.8 Review gate — approve the draft

**Route:** /runs/{id}/review. Always fires.

### Decision question and hierarchy

h1: “Is this draft ready to become the approved report?” The page must be triageable in thirty seconds without removing any of its nine evidence blocks.

1. **Review verdict:** “2 items need a decision; 3 red-team challenges are available to read; £6.40 of £8.00 spent.”
2. **Attention index** with anchor links:
   - Failed or warning validation.
   - Unsettled source disagreements.
   - Sections not generated/refused.
   - Cost alert.
   - Red-team challenges, explicitly framed as value received rather than faults.
3. Source coverage.
4. Disagreements and their settlement forms.
5. Red-team challenges at reading width, with basis and cited evidence.
6. Section outcomes and evidence tally.
7. Calculations in native details, closed by default; progressive filter over rendered rows.
8. Full draft document.
9. Shared decision form.

Keep validation, source disagreements, and adversarial challenges conceptually separate. The trigger banner means an actual problem; it never fires merely because the red team challenged the thesis.

### Local navigation and interactions

Use an in-page nav of ordinary anchors: Attention, Validation, Sources, Disagreements, Challenge, Sections, Calculations, Draft. It may be sticky at wide width but follows the document at narrow width.

Disagreement settlement uses separate sibling POST forms. Labels name the actual positions, such as “Keep the draft’s position” and “Accept the challenge”, with required rationale. A settlement records the operator’s choice without overwriting the escalation rule. Unsettled conflicts continue to publish both sides.

Preview and one-page summary are ordinary links. Preview is assembled by the same call and same inputs as render. The full draft is never replaced by a summary.

### States and sample copy

- **Ready:** “No validation failure blocks approval. Read the 3 challenges before deciding.”
- **Needs attention:** “Two source disagreements remain unsettled. If left unsettled, both positions will be published.”
- **Nothing drafted:** “This run has drafted nothing yet. There is no document to approve.” No form.
- **Section not generated:** write “Not generated” across that section’s coverage row, never 0%.
- **Calculation absent:** state what the run did not produce.
- **Stale after settlement:** re-render current review and require a fresh outer approval.
- **Partial/error:** if the draft, validation, or hash boundary is incomplete, no approval form is available.
- **Sample red-team heading:** “Challenge received — the bear case depends on a margin reversal the evidence does not yet support.”

### Responsive/no-JavaScript

Anchors, details, filters, preview, settlement forms, and decision form all work without scripting. The filter is hidden until revealed. The full document keeps print styles but remains inside the application reading frame.

### Accessibility

The attention index states counts and links to real headings. Do not put entire challenges in table cells. Give each settlement fieldset a unique legend. Maintain document heading levels when the draft is embedded beneath the page h1.

### Additional data

- **[EXISTING]** validation metrics/verdicts, coverage, disagreements, challenges, cost, section outcomes/tallies/attempts/refusals, calculations, draft, preview/summary.
- **[NEW SERVER DATA]** review_verdict and attention_index entries with count, severity, label, target id.
- **[NEW SERVER DATA]** cost-to-ceiling summary and alert reason in the shared gate shape.
- **[NEW SERVER DATA]** challenge_value_label so challenges never inherit fault styling.

---

# 6. Evidence surfaces

## 6.1 Shared evidence anatomy

### Job

Answer “does this check out?” immediately, then let the reader reach the archived bytes or input lineage in no more than two meaningful clicks.

### Shared layout and hierarchy

Every evidence page begins with:

1. Breadcrumb back to report/run and object identity.
2. A **verdict first**: Confirmed, Unconfirmed, Verification failed, Reproduces, Has findings, Not produced, or Refused.
3. A plain sentence saying what code did or did not check.
4. The evidence spine showing the current node and links above/below it.
5. The page-specific evidence.
6. “Technical proof” in native details: complete digest, UUID, artefact path/identity, verifier code.

At wide widths, the spine occupies the design system’s 152px margin beside a reading column. At narrow widths it becomes a wrapping sequence above the evidence. Do not use generic dashboard cards; use document sheets, ledger rules, quotations, and linked lineage.

### Shared states and copy

- **Confirmed:** “Confirmed — code re-read the archived artefact and located this excerpt.”
- **Unconfirmed:** “Not confirmed — the excerpt is stored, but no successful verifier result is recorded.”
- **Failed:** “Verification failed — the stored claim did not match the archived artefact.”
- **Early/empty:** “Sources have not been gathered yet. This page will remain empty until acquisition finishes.”
- **Not produced:** “This run did not produce a discounted-cash-flow value.” Never blank or zero.
- **Quarantined/refused:** say which source rule excluded it and keep it visible.
- **Partial:** distinguish missing lineage from failed verification.
- **Error:** do not collapse a verifier error into “Failed”; show stable error kind and recovery.
- **Loading:** full server response; drawer fragments may say “Loading evidence…” before swap.
- **Not found/not yours:** identical 404.

### Shared interactions

Origins are real links. The drawer may preview an excerpt, source record, or calculation inputs, but every trigger has a full-page href. Hashes are labelled by what they prove and visually shortened; native details reveals the full digest. Filters only hide rendered rows.

### Shared responsive/no-JavaScript and accessibility

Everything navigates normally without scripting. Quotations use blockquote/cite semantics where appropriate; tables have captions and row headers. The verdict is text and has a suitable heading, not a colour-only chip. Focused spine nodes and footnote targets have a visible focus state. Preserve verbatim excerpt whitespace and wording without rewriting it.

### Shared data contract

- **[EXISTING]** provenance nodes and origins, artefact digests, source/claim/calculation identifiers, verifier state.
- **[NEW SERVER DATA]** plain_verdict, verdict_explanation, and ordered lineage_nodes for a reusable evidence-spine macro.
- **[NEW SERVER DATA]** technical_proof_rows with human labels, so templates do not expose schema names as interface copy.

## 6.2 Sources acquired

**Route:** /runs/{id}/sources.

### Job

Show every document the run acquired, including what it refused to use and why.

### Layout and hierarchy

Lead with a verdict such as “18 sources acquired · 14 admissible · 4 quarantined”. Split the ledger into “Used or admissible” and “Quarantined”, without removing either from the same complete table/record. Columns: Document, Tier, Published, Retrieved, Artefact, Admissibility. Provider-plus-kind tier is written in human terms; unknown pairs say “Not citable”.

Use an admissibility rule at the left edge of each row plus written status. Artefact link copy is “Inspect archived bytes”, not the hash itself.

### Interactions and states

Filter by document title/provider over rendered rows. Link each source to its claim/excerpt context or archived bytes as permitted.

- **Empty early:** “Acquisition has not completed, so no source record exists yet.”
- **All quarantined:** “The run acquired 6 documents and refused all 6. No admissible source evidence is available.”
- **Partial:** a missing licence/tier is explicitly “Unknown — not citable”.
- **Sample copy:** “Quarantined — published after the request’s as-of date.”

### Responsive/no-JavaScript and accessibility

Use a bounded table scroll, with Document and Admissibility as the first and last high-priority columns. Full table remains with scripting off. Status text is included in each row’s accessible name/context.

### Data contract

- **[EXISTING]** document, provider/kind/tier, published/retrieved dates, artefact, admissible flag, quarantine reason.
- **[NEW SERVER DATA]** source_summary counts and human_tier_label if not already provided.

## 6.3 Claims index

**Route:** /runs/{id}/claims.

### Job

Let a reader find any assertion and open the exact evidence behind it.

### Layout and hierarchy

Lead with “42 claims · 38 confirmed · 3 unconfirmed · 1 failed”. Rows show the claim sentence at reading width, figure if any, verdict, source/calculation origin, and section. Group/filter by report section and verdict. Do not lead with claim ids.

### States and sample copy

- **Empty early:** “No claims have been recorded yet; drafting has not produced assertions.”
- **Partial:** claims with no resolved origin remain visible as “Origin unresolved”.
- **Failed:** place failed verification first within its group without mixing it with ordinary unconfirmed state.
- **Sample copy:** “Confirmed — revenue was £12.4bn for the year ended 31 December 2025.”

### Responsive/no-JavaScript and accessibility

Use ledger articles rather than forcing long sentences into narrow table cells. Filters are hidden until script reveals them. Claim sentence is the link and has a visible verdict beside it.

### Data contract

- **[EXISTING]** claim sentence, figure, verifier verdict, origin, section.
- **[NEW SERVER DATA]** claim_summary counts and plain origin label.

## 6.4 Claim detail

**Route:** /claims/{id}.

### Job

Put the stored words and their verification verdict at the centre of the page.

### Layout and hierarchy

1. Verdict: “Confirmed” or the honest alternative.
2. The figure/claim sentence.
3. A large evidence quotation containing the verbatim stored excerpt, with source title, publication date, located position, and licence note.
4. Evidence spine from claim to source artefact.
5. Verifier record and technical proof.

The excerpt is visually the largest reading block. Do not paraphrase, tidy, smart-quote, or re-wrap its stored content beyond safe CSS line wrapping.

### States and sample copy

- **Confirmed:** “Code located this exact excerpt in the archived annual report.”
- **Unconfirmed:** “The excerpt is stored, but a successful re-check is not recorded.”
- **Failed:** “The archived bytes no longer yield this excerpt under the recorded locator.”
- **Missing artefact/partial:** keep the claim and say “Archived bytes are unavailable; this claim cannot currently be proved.”
- **Refused:** quarantined source status remains visible.

### Responsive/no-JavaScript and accessibility

Quotation line length stays 55–75 characters. On narrow screens metadata stacks after the verdict and before the quote. Provide a skip link from metadata to the excerpt. Do not use a textarea for evidence.

### Data contract

- **[EXISTING]** sentence, figure, exact excerpt, source, verifier verdict.
- **[NEW SERVER DATA]** located_position_label and verifier_action_sentence if currently raw.

## 6.5 Footnote resolution

**Route:** /runs/{id}/footnotes/{n}.

### Job

Continue from a document marker to either its source proof, calculation walk, or an honest unresolvable statement.

### Layout and hierarchy

Show “Footnote {n}” and the originating sentence/context first. Then:

- **Calculation marker:** verdict and direct continuation to formula/inputs.
- **Source marker:** source/licence and every claim in this run checked against it.
- **Unresolvable marker:** the exact words used by the document and why no further path exists.

Keep the report context/back link visible so the marker never feels like an isolated id.

### States and sample copy

- **Resolved source:** “Resolved to the FY2025 annual report; 4 claims in this run were checked against it.”
- **Resolved calculation:** “Calculated — continue to the formula and its 6 inputs.”
- **Unresolvable:** preserve the report’s wording verbatim.
- **Partial/error/refused:** follow shared evidence states and never redirect to a plausible but different source.

### Responsive/no-JavaScript and accessibility

Ordinary links complete the walk. A fragment drawer can preview, but the full route is canonical. The footnote number is not the only accessible label.

### Data contract

- **[EXISTING]** document-assembled marker mapping, source/calculation outcome, licence, associated claims.
- **[NEW SERVER DATA]** originating_context excerpt and return_to_report_href if not supplied.

## 6.6 Calculation detail

**Route:** /calculations/{id}.

### Job

Explain the arithmetic, the choices it embeds, and every input’s origin recursively.

### Layout and hierarchy

Lead with the server-rendered result and verdict. Then show:

1. Formula in readable mathematical/monospace notation.
2. Structural choices — what was judged or selected rather than derived.
3. “What it rests on”: Input, Kind, server-rendered Value, Origin link.
4. Formula assumptions/caveats.
5. Evidence spine and technical proof.

Inputs use two separate provenance/confirmation indicators where relevant. Never collapse “Calculated” and “Confirmed” into one chip.

### States and sample copy

- **Complete:** “Calculated — enterprise value £4.2bn from 7 recorded inputs.”
- **Unconfirmed input:** keep calculation outcome but state the weaker confirmation chain.
- **Missing input/not produced:** “This run did not produce the input; no value was substituted.”
- **Error:** distinguish arithmetic replay error, tolerance failure, and unit mismatch.
- **Refused/not yours:** ownership through the calculation’s job and non-enumerating response.

### Responsive/no-JavaScript and accessibility

Formula scrolls in its own code region if necessary. Input table uses row headers. Origins are ordinary links and drawer-enhanced only optionally.

### Data contract

- **[EXISTING]** result, formula, choices, inputs, kinds, values, origins, taken-for-granted notes, job ownership.
- **[NEW SERVER DATA]** plain calculation verdict and structured replay/problem kind labels.

## 6.7 Valuation

**Route:** /runs/{id}/valuation.

### Job

State what valuation the run actually produced, expose both terminal methods and comparables, and make sensitivity legible without recomputing anything.

### Layout and hierarchy

Lead with the valuation verdict/range and method applicability. Follow with:

1. Terminal-method outcomes and caveats.
2. A deterministic, server-rendered sensitivity heatmap plus its full 9×9 data table.
3. Comparables and their evidence availability.
4. Evidence spine to calculation inputs.

Anchor the central case with a labelled outline and “Base case”, not colour alone. Label both axes with the assumptions they vary. Colour may encode ordered value but every cell retains a server-rendered number.

### States and sample copy

- **Produced:** “Base case £42.10 per share; sensitivity range £31.80–£55.60.”
- **Bank:** “No discounted-cash-flow sensitivity grid was produced. This run used residual income because the company was confirmed as a bank.”
- **Method absent:** name that the run did not produce it.
- **Partial:** if one method failed, show the other with an explicit partial verdict; never average available outcomes.
- **Error/refused:** ledger read failure blocks the page rather than triggering recomputation.

### Responsive/no-JavaScript and accessibility

The heatmap is an image/inline SVG generated server-side with a concise text alternative and an adjacent full table. The table scrolls inside its container on narrow screens. No client canvas, arithmetic, formatting, or tooltip-only values.

### Data contract

- **[EXISTING]** ledger-read terminal values, grid cells, comparables, caveats.
- **[NEW SERVER DATA]** server-generated deterministic heatmap SVG or image plus alt_summary; this is a server figure and must be tested byte-for-byte for the same rows.
- **[NEW SERVER DATA]** base-cell coordinates and plausible-cell semantics if those judgements are recorded; do not infer plausibility in CSS.

## 6.8 Replay result

**Trigger:** POST /runs/{id}/replay, followed by its server-rendered result.

### Job

Say whether the recorded run still reproduces and list each exception by its actual kind.

### Layout and hierarchy

Lead with “Reproduces” or “Has findings”, run identity, replay time, and a statement that no source was fetched and no model called. Then group findings by Re-derivation outside tolerance, Unit mismatch, Citation verification, and Replay error. Each finding links into its claim/calculation evidence.

### States and sample copy

- **Everything holds:** “Reproduces — all recorded derivations and citations still hold.”
- **Findings:** “3 findings: 1 unit mismatch and 2 citations that could not be re-verified.”
- **Partial:** “Replay completed with 2 checks unavailable”; do not call this a pass.
- **Error:** distinguish replay operation failure from a substantive finding.
- **Refused:** CSRF/ownership response; same id privacy rule.
- **Loading:** with htmx, button copy may change to “Reproducing…”; no optimistic result. Without scripting, normal navigation waits for the POST response.

### Responsive/no-JavaScript and accessibility

The trigger remains a form. Findings are headings/lists, not colour-only rows. After POST, focus the result h1.

### Data contract

- **[EXISTING]** replay findings and persisted verifier updates.
- **[NEW SERVER DATA]** replay_summary counts, completed_at_display, checks_unavailable_count, and grouped human finding labels.

---

# 7. Reports

## 7.1 Report history

**Route:** /reports.

### Job

Let the operator find past work by what it concluded and see how the conclusion changed across reports on the same company.

### Wide layout and hierarchy

Lead with h1 “Reports” and a GET search/status filter. Group by company, newest company activity first. Each company group has a concise current verdict and a vertical history:

- as-of date and produced date;
- Approved or Draft;
- conclusion/rating in words;
- valuation or range, with method;
- spend;
- “Changed since prior report” summary.

Drafts are clearly marked and appear only here among history surfaces. Do not visually merge them with approved history. The most recent approved report anchors the company group; older entries are quieter but fully reachable.

### Narrow layout

Company groups become stacked timelines. Each report entry uses a definition list; conclusion remains first. Avoid a horizontal report-comparison table at 320px.

### Interactions

Search/filter may use GET parameters. Report title is a link. “Compare with previous” appears only when the server supplies a valid prior approved report relation.

### States and sample copy

- **Populated:** “Contoso plc · Hold · £42.10 base case · approved 24 Aug · £6.40.”
- **Change:** “Since March: rating unchanged; base-case value down 8%; margin risk increased.”
- **Empty:** “No reports yet. Commission research, run it, and approve the draft to create the first report.”
- **Draft:** “Draft — not part of approved company history.”
- **Partial:** absent conclusion says “No conclusion produced”; never guess from prose.
- **Error:** fail loudly if history cannot be established.
- **Refused/not found:** not applicable to index; individual ownership remains non-enumerating.
- **Loading:** full server response.

### Responsive/no-JavaScript and accessibility

All filters and links work without scripting. Company headings structure the page. Draft status is text. Change summaries do not depend on arrows/colour alone.

### Data contract

- **[EXISTING]** reports grouped by company, newest first; draft/approved distinction.
- **[NEW SERVER DATA]** conclusion_label, valuation_display, valuation_method_label, run_spend_display.
- **[NEW SERVER DATA]** prior_approved_report_href and server-authored change_summary with structured rating/value/risk changes.

## 7.2 Report detail

**Route:** /reports/{id}.

### Job

Make the approved document the page, while keeping proof, evidence, export, and disclaimer within reach.

### Wide layout and hierarchy

1. Company/report identity and conclusion line.
2. A compact proof statement: “Approved on 24 August 2026. The archived Markdown is byte-for-byte what was approved.”
3. Two-column reading frame: a slim sticky section/evidence index and the report document at 62–76 character line length.
4. Secondary actions: Preview/print view, Evidence, Export to Obsidian.
5. Technical proof disclosure with full hash and archived-bytes link.
6. The shell’s full disclaimer remains present once on this route. It stays owned by the shell and satisfies the report requirement without a second page-owned copy.

Sections are navigation within the document, not a block before it. Export is an action panel or drawer, not a quarter-page section.

### Narrow layout

Section index becomes native details “In this report”. Proof statement and conclusion precede it. Document remains first-class and uses the full width. Actions follow the conclusion and repeat only at the end if necessary.

### Interactions

Obsidian export is an explicit CSRF-protected POST. Nothing exports on view. Evidence markers are ordinary links, optionally drawer-enhanced. The archived-bytes link is labelled by its guarantee, not its digest.

### States and sample copy

- **Approved:** “Approved and frozen — archived bytes match approval hash 8af2…”
- **Draft reached from this account’s report list:** “Draft — not approved and not part of company history.” Do not describe it as frozen.
- **Export succeeded:** “Exported to {server-supplied contained path}.”
- **Export refused:** “Export refused: the destination is outside the reserved personal tree.”
- **Partial:** missing archived bytes is a proof failure and must dominate; do not quietly show the reading surface as fully verified.
- **Error:** report assembly error is distinct from hash mismatch.
- **Not found/not yours:** identical response.
- **Loading:** full server document.

### Responsive/no-JavaScript and accessibility

Section anchors, footnotes, evidence links, and export form work without scripting. Preserve a coherent heading hierarchy inside the embedded report by offsetting document headings beneath the page h1. Print styles suppress application navigation but retain title, produced date, disclaimer, and footnotes.

### Data contract

- **[EXISTING]** sections, assembled report, approval status/date, hash, archived bytes, export rules, full disclaimer.
- **[NEW SERVER DATA]** conclusion_label and proof_statement fields.

## 7.3 Chrome-free report notation

**Route:** /reports/{id}/preview.

### Job

Present the report as a printable document with its actual produced date and one unobtrusive route home.

### Layout and hierarchy

Use a centred document sheet with title, as-of date, produced date from the report row, conclusion, body, footnotes, and disclaimer. Put “Return to report record” before the title in a small screen-only line. No application navigation, scripts, export controls, or live state.

Assemble the reading surface from the run’s stored rows, as today; do not replay from the archive. The archived Markdown remains the hashed record of what was approved.

### States and sample copy

- **Approved:** proof/date line is present.
- **Draft:** visibly “Draft — not approved”.
- **Partial/error:** do not print a document whose assembly failed; show a plain error record and return link.
- **Refused/not found:** same response as detail.
- **Loading:** server response only.

### Responsive/no-JavaScript and accessibility

The route is inherently no-script. At narrow widths the paper loses its decorative outer margin, not its content padding. The return link is suppressed in print. Use the date produced, never the viewing date.

### Data contract

- **[EXISTING]** report view and row-recorded produced date.
- **[NEW SERVER DATA]** canonical report-detail return href if not derivable safely.

---

# 8. Skills

## 8.1 Skills library

**Routes:** /skills and /skills/new.

### Job

Show which operator-authored methods are active, valid, and affecting runs, and make creating or testing one an obvious next action.

### Wide layout and hierarchy

Lead with the boundary statement:

> Skills may add requirements. They cannot remove citations, set a rating, or relax point-in-time rules.

Follow with Enabled and Disabled sections. Each skill row shows name/key, current version, validity, enabled state, concise purpose, last changed, runs affected, and actions: Edit, Dry run, Enable/Disable, Export. Use sentence states, not raw version/hash emphasis. “New skill”, “Import”, and “Examples” are page actions.

### Narrow layout

Rows stack into ledger articles; name and active/valid state precede version and effect. The boundary statement stays open at every width.

### Interactions

Enable/Disable are forms. Export is a link/download of exact stored bytes. Examples and Import remain separate routes. Search may filter rendered rows only.

### States and sample copy

- **Empty:** “No skills yet. Start from a worked example or write a method from scratch. Every import is reviewed before it is saved.”
- **Invalid:** “Not active — 2 issues prevent this skill from composing.”
- **Disabled:** “Valid but disabled; new runs will not use it.”
- **No affected runs:** “Not used by a completed run yet.”
- **Partial:** if effect history cannot be loaded, say “Run history unavailable” without changing enabled/valid state.
- **Error:** a library failure is not an empty library.
- **Refused:** containment or permission refusal names the additive-only rule.
- **Loading:** full server list.

### Responsive/no-JavaScript and accessibility

All actions are links/forms. State is written. Each enable/disable button includes the skill name in its accessible name. The containment statement is normal text, not a tooltip.

### Data contract

- **[EXISTING]** key, version, enablement, validity/issues, stored source, routes.
- **[NEW SERVER DATA]** purpose_summary, last_changed_display, affected_run_count, last_affected_run_href.

## 8.2 Skill editor and validation

**Route:** /skills/{key}, also used for a new skill where appropriate.

### Job

Let the operator edit exact source bytes while seeing the effective policy and containment boundary before saving or spending.

### Wide layout and hierarchy

Use a paired working-paper layout:

- Left, 55–60%: “Source you wrote” with the raw-byte textarea, source-format guide, Save and Validate.
- Right, sticky: “What runs receive” with composed effective policy, additive-only boundary, current issues, and Dry run selector/action.

The composed policy is the verdict, not an appendix. Place issues immediately above it when invalid. Keep exact raw source as the only editable representation so comments, key order, and whitespace round-trip byte for byte. A structured frontmatter reference can sit beside the textarea, but do not rebuild the stored file from separate browser fields.

### Narrow layout

Boundary → issues → raw source → Validate/Save → composed policy → Dry run. Textarea uses a practical minimum height and horizontal scroll for long unbroken source lines; the page itself stays fixed-width.

### Interactions

- Validate POST writes nothing and returns issues/composed policy.
- Save POST writes only after all structural rules pass.
- Dry run selects a finished run and executes this skill in its own run against recorded evidence.
- Enable/Disable remains a separate explicit POST.
- After Save, show the exact resulting content hash in technical proof, not as the main success message.

### States and sample copy

- **Valid:** “Valid — this skill adds 3 requirements and relaxes none.”
- **Issues:** “Not saved. Line 8 attempts to remove citation requirements; skills can only add requirements.”
- **Unsaved validation:** “Validated only. Nothing was written.”
- **Dry-run output:** show result, chosen finished run, and clear “This did not change the original run.”
- **No finished run:** “Dry run needs a finished run with recorded evidence. Complete one first.”
- **Concurrent version/refused:** “This skill changed after you opened it. Nothing was overwritten. Compare the current version before saving.”
- **Partial:** composition unavailable means Save is withheld; raw bytes remain intact.
- **Error:** preserve entered source after any rejection.
- **Loading:** full server form.

### Responsive/no-JavaScript

Every action is a distinct submit button/form path. The same page response carries validation and dry-run results without scripting. Do not require a client code editor or syntax parser.

### Accessibility

Textarea has a visible label and format instructions. Error list links to line/field context where the server can identify it. The two-pane visual order does not change DOM order: boundary and source precede composed result and actions. Do not rely on monospace colour syntax.

### Data contract

- **[EXISTING]** exact source bytes, content hash, issues, composed policy, finished-run options, dry-run result.
- **[NEW SERVER DATA]** composition_summary with added requirement count and forbidden_relaxation count.
- **[NEW SERVER DATA]** base_version_hash for a non-destructive concurrent-edit refusal if not already checked.
- **[NEW SERVER DATA]** line/column issue locations when the validator already knows them; do not parse in the browser.

## 8.3 Skill import, examples, and export

**Routes:** /skills/import, /skills/examples, /skills/{key}/export.

### Job

Make every incoming method inspectable before it is written and every outgoing file byte-identical to its stored source.

### Layout and hierarchy

**Import:** Step 1 upload/source choice; Step 2 server-rendered diff and composed-policy impact; Step 3 explicit Confirm import. The diff is the centre of the page. State “Nothing has been written” until confirmation.

**Examples:** list worked examples with purpose, constraints added, and “Review import” action. Never label an example Installed until it passed the ordinary import path.

**Export:** direct byte-for-byte download; the library/editor provides the action, so a decorative export page is unnecessary unless the route must show an error.

### Interactions and states

- Import confirmation carries/rechecks the hash of the current base and incoming source.
- If the base moved: “The saved skill changed after this diff was created. Nothing was imported. Generate a new diff.”
- Invalid/containment refusal appears before confirm.
- Empty example list names the packaging issue rather than presenting an empty library.
- Upload/parse error preserves selected-file identity where browser security permits, but never pretends it retained file bytes when it did not.
- Partial composition or diff data withholds Confirm and says which comparison could not be established.
- Succeeded: “Imported {name} as version {n}.”
- Loading: ordinary POST response; optional htmx progress is chrome only.

### Responsive/no-JavaScript and accessibility

Diff lines use semantic added/removed labels in addition to colour and remain horizontally scrollable inside their region. All stages are server POST/redirect pages. File input, confirm form, and example links work without scripting.

### Data contract

- **[EXISTING]** incoming exact bytes, server diff, base/incoming hashes, confirmation, examples, byte-exact export.
- **[NEW SERVER DATA]** composed_policy_delta summary for the import review.

---

# 9. Knowledge

## 9.1 Knowledge overview

**Route:** /knowledge.

### Job

Say whether accumulated knowledge is becoming more useful and what needs to be revisited now.

### Wide layout and hierarchy

Lead with a global-scope label: “Platform knowledge — all approved reports in this database”. Then:

1. Verdict: “Useful but ageing — 4 companies have stale research and 3 catalyst windows need outcomes.”
2. **Close the loop** work list: stale companies and closed catalyst windows, each with a direct company action.
3. Quality sheet: coverage, assumption accuracy, freshness trend.
4. Shape sheet: graph size/connectivity and what those figures mean.
5. Vault status and technical record.

Do not lead with six equal statistic blocks. Actions precede measurements.

### Narrow layout

Verdict and work list remain first. Metrics become definition lists with short interpretations, not a two-column wall of tiles. Keep graph link visible.

### Interactions

Everything is a link. Filters for stale/catalyst work use GET parameters if introduced. Knowledge is deliberately unscoped; do not add a misleading “My data” filter.

### States and sample copy

- **Healthy:** “Knowledge is current enough to use: no catalyst windows await an outcome and 90% of covered companies were revisited within six months.”
- **Empty:** “Knowledge is empty. Approved research reports create companies, claims, themes, and relations here.”
- **Sparse:** “The graph has 3 companies and 1 confirmed relation — too little to interpret as a network yet.”
- **Stale:** “4 companies have not been revisited recently.”
- **Partial:** if one measurement fails, show a named unavailable block and do not roll it into the headline as healthy.
- **Error:** graph measurement failure is an error, not zeros.
- **Refused:** not applicable to the unscoped overview; route access rules remain server-owned.
- **Loading:** full server page.

### Responsive/no-JavaScript and accessibility

No metric depends on a chart alone; every figure has a text interpretation and server-rendered value. Work items are real links. Scope statement appears near h1 and is announced before metrics.

### Data contract

- **[EXISTING]** size, shape, coverage, assumption accuracy, freshness, stale items, closed catalyst windows, vault.
- **[NEW SERVER DATA]** knowledge_verdict assembled only when required providers succeeded.
- **[NEW SERVER DATA]** server-authored interpretation per metric and freshness trend.
- **[NEW SERVER DATA]** company/action href on stale and catalyst items if not currently supplied.

## 9.2 Knowledge graph

**Route:** /knowledge/graph.

### Job

Make confirmed relations explorable without moving layout or figures into the browser.

### Wide layout and hierarchy

Lead with scope, node/edge counts, and a legend. The main figure is the deterministic server-laid-out SVG. Every company node is an anchor to /companies/{id}; relation labels/edges expose an accessible description. Put a server GET filter above the graph for company/theme/relation if the graph becomes dense, with filtered state in the URL.

Keep supporting lists beneath: selected/filter-matching nodes and relation explanations. This makes the graph usable for readers who cannot perceive the visual topology.

### Narrow layout

The SVG keeps its server coordinates and responsive viewBox. Allow a bounded viewport with browser scrolling if necessary, followed by the complete node/relation list. Do not shrink labels below readable size.

### Interactions

Minimum build: linked SVG nodes and GET filtering. Do not add pan, zoom, drag, or live client filtering in this implementation. Those require the pre-authorised JavaScript-island ADR; if added later, the server still supplies placed nodes and the island owns only the viewport transform.

### States and sample copy

- **Empty:** “No confirmed relations yet. Approve reports to build the graph.”
- **Sparse:** “This graph is too small for connectivity measures to be meaningful.”
- **Filtered empty:** “No confirmed relation matches these filters. Clear filters.”
- **Partial:** an omitted node/edge is never silently drawn as absent; the server must provide a complete filtered result or an error.
- **Error:** “The graph could not be laid out from its confirmed relations.”
- **Refused:** route access rule on server.
- **Loading:** static server figure.

### Responsive/no-JavaScript and accessibility

The graph works with no script. Provide an accessible title/description and a complete adjacent relation list. Node focus is visible within the SVG. Colour is not the sole relation encoding; use labels/line patterns and text.

### Data contract

- **[EXISTING]** server-computed deterministic node coordinates and confirmed edges.
- **[NEW SERVER DATA]** node href, accessible edge descriptions, and GET filter options if not present.
- **[NEW SERVER BEHAVIOUR]** server-side filtered layout with query parameters only if filtering is adopted.

## 9.3 Company history

**Route:** /companies/{id}.

### Job

State the last approved view, what has changed since, and which real-world catalyst loop the operator needs to close.

### Wide layout and hierarchy

1. Company identity and verdict: “Last view: Hold at £42.10, approved 14 March. One catalyst window has since closed.”
2. Closed catalysts awaiting outcome, with “Record what happened”.
3. Timeline of approved reports and material events.
4. Deterministic valuation-history chart with an adjacent data table.
5. Approved reports.
6. Prior catalyst outcomes.

The catalyst form is a primary completion surface, not a footer form. It lists only catalyst labels proposed by approved reports. Reason is required.

### Narrow layout

Verdict and catalyst action remain first. Timeline becomes a vertical ledger. Chart is responsive with complete table beneath. Catalyst outcome form stacks.

### Interactions

“Record what happened” opens an in-page native details section or full-page anchored form. It POSTs server validation. No model suggestion appears in the operator’s answer field.

### States and sample copy

- **One report:** “One approved view exists; change over time is not available yet.”
- **Several reports:** show server-authored change line.
- **Open catalyst:** “Window open until 30 September; no outcome is due.”
- **Closed awaiting outcome:** “Window closed 12 days ago. Record what happened to complete the research loop.”
- **Recorded:** “Outcome recorded by you on 24 August.”
- **Refused:** “Choose a catalyst proposed by an approved report and provide a reason.”
- **Partial:** chart/report history remains but verdict names missing catalyst status if unavailable.
- **Error/not found:** no fabricated empty company.
- **Loading:** full server page.

### Responsive/no-JavaScript and accessibility

The POST form, report links, and chart table work without scripting. Chart is deterministic server output with a text alternative. Field errors associate with catalyst and reason controls. Approval status is explicit.

### Data contract

- **[EXISTING]** approved report timeline, valuation history, catalysts/windows/outcomes, outcome validation.
- **[NEW SERVER DATA]** latest_approved_conclusion, changed_since_last summary, days_since_window_close display.
- **[NEW SERVER DATA]** deterministic chart alt_summary and table rows if not already exposed.

---

# 10. Portfolio

## 10.1 Portfolio book

**Route:** /portfolio with as-of date in the query string.

### Job

Answer “what is the book worth and how has it performed as at this date?” while showing the grade and lineage of every figure and making any incomplete valuation impossible to mistake for a total.

### Wide layout and hierarchy

1. Book identity and a prominent **as-at lens**: “As at 21 August 2026 · last market close held · reported in GBP.” The GET date form is part of the heading, not a minor filter.
2. Verdict: “Book fully valued · net assets £1,234,567.89 · all figures typed/self-certified” or the incomplete alternative.
3. Performance sheet: time-weighted and money-weighted returns for selected server periods, with a visible explanation that deposits/withdrawals are flows, not gains.
4. Exposure sheet: holding, sector, currency, country, and top-five concentration. Report known coverage and name unclassified holdings; never bucket unknown sector as Other.
5. Exact-value ledger: Net assets, Securities, Cash, Unrealised. These four stand or fall together.
6. Holdings table including cash rows.
7. Transaction record and “Record a transaction”.

Use exact pennies everywhere on this tool. The book-level grade is stated once near the verdict. Per-row grade links remain but are quieter; a documented row must still be distinguishable.

Positions remain calculations made from transactions during the read. Loading this page creates no position or calculation rows. Every displayed aggregate carries the weakest grade beneath it.

### Holdings table

Columns: Security, Quantity, Pooled cost, Price at date, Value, Unrealised, Weight, Grade. Ticker/exchange and listing currency sit with Security. Unrealised is signed as well as coloured. Cash occupies sunken rows in the same table.

An unpriced row puts the full reason across the cells where price/value/unrealised/weight would be. A closed position says “Closed”. Each open holding has “Inspect transactions”, link-first to a full page and enhanced into the shared drawer.

### Narrow layout

Verdict and as-at lens remain first. Performance periods become a compact definition list; exposure charts are server-rendered figures with adjacent lists. Holdings stay a semantic table in a bounded scroll area because cross-row comparison matters. Pinning the first column is optional CSS chrome, but it must not obscure keyboard focus. Transaction entry follows the table.

### Interactions

- Date is a GET capped at today; malformed URLs fall back to the last close held.
- Default is last close held, never today.
- Exposure choices and period choices, if interactive, are GET parameters and use server-rendered figures.
- Holding inspection is a real link with optional drawer.
- Loading the page never writes positions or calculations.

### States and sample copy

- **No book:** “Create your book with a name and reporting currency. Both can be changed later.” Two-field POST form, not a wizard.
- **Book empty:** “Nothing recorded yet. Record a deposit, then a trade. Researching a ticker first makes a priced listing available.”
- **Ordinary:** “Fully valued as at the last close held.”
- **One unpriceable holding:** all four exact-value figures show an em dash with “Unavailable while one position cannot be valued.” The verdict names the affected holding and reason.
- **Whole book broken:** “The transaction record could not be computed. An empty table would incorrectly imply you hold nothing, so no position view is shown.”
- **No priced listings:** “Cash transactions are available. Commission research on a ticker to add a priced listing.”
- **Partial exposure:** “Sector known for 72% of value. Unclassified: ABC, XYZ.” Do not show a 100% chart.
- **Returns unavailable:** explain insufficient history or incomplete prices; do not use 0%.
- **Error:** distinguish pricing, FX, and transaction-walk failure.
- **Refused:** dual listing names both choices; unknown ticker says how to identify venue; hand-entered grade cannot be upgraded.
- **Loading:** server response only; no client totals.

### Responsive/no-JavaScript and accessibility

Date form, holding links, and transaction route work without scripting. All charts are deterministic server figures with tables/text alternatives. Table has caption, row headers, tabular numerals, and signed values. Provenance class badges are links; grade/confirmation stays a separate axis where both apply.

### Data contract

- **[EXISTING]** book/date/currency, last close default, holdings and cash, exact totals, grade, price/refusal reasons, transactions as calculation source.
- **[NEW SERVER DATA]** server-calculated time_weighted_return and money_weighted_return by period, with method note and completeness.
- **[NEW SERVER DATA]** server-calculated exposures by holding/sector/currency/country, coverage, named unknowns, top_five_concentration.
- **[NEW SERVER DATA]** book_grade_summary and weakest_input links.
- **[NEW SERVER DATA]** deterministic exposure/performance figure assets or SVG plus accessible summaries. No figure is generated in JavaScript.
- **[NEW ROUTE]** /portfolio/holdings/{listing}?as_of={date} as the canonical full-page holding inspection fallback, plus an optional fragment endpoint for the shared drawer.

## 10.2 Holding detail and transaction history

**Proposed route:** /portfolio/holdings/{listing}?as_of={date}; transaction history may also be reachable at /portfolio/transactions with GET filters.

### Job

Show exactly which recorded transactions produced a holding’s quantity, pooled cost, cash effect, and grade.

### Layout and hierarchy

Lead with the holding verdict at the selected date. Show quantity, pooled cost, price/value or refusal, grade, then a transaction ledger: date, kind, quantity/amount, price, dealing costs, currency, cash effect, note, grade/origin. Follow with price history only where recorded and server-rendered.

The drawer version is a concise subset and links “Open full transaction record”. The full page remains canonical.

### States and sample copy

- **Open:** “1,250 shares · pooled cost £18,432.10 · value £21,875.00.”
- **Closed:** “Position closed on 12 June; prior transactions remain.”
- **Unpriced:** show the refusal where value would be.
- **No transactions/error:** impossible derived-state mismatch is an error, not empty.
- **Refused/not found:** dual listing/ownership rules.
- **Partial:** missing price history does not hide transaction lineage.
- **Loading:** drawer may say “Loading holding record…”; full page server-rendered.

### Responsive/no-JavaScript and accessibility

Drawer trigger is link-first. Transaction table scrolls inside its own region; a narrow alternative may group rows by date while preserving semantic labels. Every figure is server formatted.

### Data contract

- **[EXISTING]** transaction rows and server-computed holding values can be derived without writing.
- **[NEW SERVER DATA]** pooled-cost walk rows/cash effects formatted for explanation.
- **[NEW ROUTE]** canonical holding detail and optional transaction index/filter route.

## 10.3 Record a transaction

**POST route:** /portfolio/transactions. Selection state may use /portfolio?record={kind}#transaction.

### Job

Make each transaction type feel like a short form while preserving one server-owned signing and validation path.

### Layout and hierarchy

First ask “What happened?” using six link-like choices that set a GET query: Buy, Sell, Dividend, Fee, Deposit, Withdrawal. The server then renders only the relevant form:

- **Buy/Sell:** security, trade date, quantity, price per share, currency, dealing costs, note.
- **Dividend/Fee:** security optional as domain allows, date, amount, currency, note.
- **Deposit/Withdrawal:** date, amount, currency, note.

State above every form: “Enter positive amounts. The transaction type determines the sign.” For London listings, explain that price is in the dealing currency unit shown on the contract note, including pence where applicable. Empty Security means cash only where that meaning is valid and plainly labelled.

### Interactions

The transaction kind is a GET choice so no script is needed to reshape the form. An optional htmx fragment may replace the form area, but the URL/full-page result remains valid. Security remains input+datalist and accepts Ticker, Vendor symbol, or Ticker Exchange. The server refuses ambiguous dual listings with both options.

### States and sample copy

- **Blank kind:** “Choose what happened to see only the fields it needs.”
- **Validation error:** preserve every entered value and associate errors.
- **Unknown ticker refusal:** “RDS resolves to more than one listing. Choose RDSA LSE or SHEL NYSE.”
- **No priced listing:** “No priced securities are available yet. Deposits, withdrawals, and other cash entries still work.”
- **Constraint refusal:** surface the database rule in operator language.
- **Success:** “Recorded buy of 125 MSFT shares on 21 August.” The grade statement: “Typed and self-certified.”
- **Partial/error:** do not update the displayed book optimistically; redirect to a freshly computed date view.
- **Loading:** ordinary POST; htmx may disable only the submitting control.

### Responsive/no-JavaScript and accessibility

Kind choices are ordinary links or GET submit buttons, not a scripted select dependency. Fieldsets/legends change with the server-rendered kind. Numeric input instructions identify shares versus money. The success/refusal summary receives focus. Never use placeholder text as the only label.

### Data contract

- **[EXISTING]** six transaction kinds, current eight controls, positive-input signing, supported security forms, attested grade, server/database validation.
- **[NEW SERVER BEHAVIOUR]** GET-selected kind renders the relevant subset while the POST handler remains authoritative.

---

# 11. Cross-page build checklist

Claude should not treat the mockup’s presence as evidence that a data field exists. Before implementing each template:

1. Add or extend a typed server view model for every **[NEW SERVER DATA]** field.
2. Supply that field on every handler path, including empty/error/refused paths, because StrictUndefined is enabled.
3. Render all money, dates, percentages, durations, signs, ranges, and chart assets on the server. Portfolio money is exact to the penny; report money uses report house style.
4. Keep status enums and ids in technical details only; create stable UK-English display labels server-side.
5. Reuse one shell nav DOM, one badge slot, one drawer, one evidence-spine macro, one gate journey macro, and one gate decision macro.
6. Make destructive actions and state changes POSTs with CSRF; make returnable views GETs with query state.
7. Verify every enhanced path with scripting disabled.
8. Test light/dark/system, 320px, 200% zoom, keyboard-only, reduced motion, missing delayed fragments, and the complete empty/partial/error/refused checklist.
9. Run accessibility checks against rendered states, not component examples alone.
10. Preserve every record boundary: exact skill bytes, exact approval payload hash, archived report bytes, ledger-read valuation, server-laid-out graphs, and non-writing portfolio GETs.

## Acceptance journey

The redesign is coherent when one operator can:

- arrive on Overview and see the most consequential work, its age, and its cost without scrolling;
- commission a defensible request from the essential decisions, with hindsight and spending consequences impossible to miss;
- watch a run and distinguish healthy thought from a missing worker;
- learn one gate shape and use all seven, with evidence before decision and exact-payload reassurance;
- move from any material figure to its origin and stored words in at most two meaningful clicks;
- find an old report by conclusion and see what changed;
- understand what a skill adds before it is saved or tested;
- see what platform knowledge needs human follow-up;
- reconcile a dated book down to its transactions without ever mistaking a subtotal for a total;
- complete the same critical actions with JavaScript disabled.
