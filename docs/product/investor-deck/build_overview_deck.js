/*
 * The investor overview deck: what the platform is, what it does, where it is weak,
 * how it compares to a chat console, and where it goes next.
 *
 * Every present-tense figure is read from the readiness audit of 2026-09-12 or from the
 * repository. Forward-looking slides are tagged VISION and carry no measured claims.
 */

const pptxgen = require("pptxgenjs");

const NAVY = "1E2761";
const PANEL = "26306A";
const ICE = "CADCFC";
const WHITE = "FFFFFF";
const INK = "1B2140";
const BODY = "3E466B";
const MUTE = "7B83A6";
const ICEMUTE = "8FA3D8";
const AMBER = "B8862F";
const CARD = "F2F5FC";
const LINE = "D8E0F2";
const GREEN = "2E6B4F";

const SERIF = "Cambria";
const SANS = "Calibri";
const M = 0.75;
const FULL = 11.83;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "Ageantic";
pres.company = "Ageantic";
pres.title = "Ageantic — investor overview";
pres.subject = "An equity research system where every number can be checked";

const soft = () => ({ type: "outer", color: "1E2761", opacity: 0.13, blur: 12, offset: 3, angle: 90 });

const dark = () => { const s = pres.addSlide(); s.background = { color: NAVY }; return s; };
const light = () => { const s = pres.addSlide(); s.background = { color: WHITE }; return s; };

function eyebrow(s, text, colour) {
  s.addText(text, { x: M, y: 0.44, w: 9.6, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 2.2, color: colour, isTextBox: true, margin: 0, valign: "top" });
}

function heading(s, text, colour, opts) {
  s.addText(text, Object.assign({ x: M, y: 0.85, w: FULL, h: 0.62, fontFace: SERIF, fontSize: 32,
    bold: true, color: colour, isTextBox: true, margin: 0, valign: "top" }, opts || {}));
}

function note(s, text, opts) {
  s.addText(text, Object.assign({ fontFace: SANS, fontSize: 12.5, color: BODY, lineSpacing: 17,
    isTextBox: true, margin: 0, valign: "top" }, opts));
}

function cite(s, text, colour) {
  s.addText(text, { x: M, y: 6.86, w: FULL, h: 0.3, fontFace: SANS, fontSize: 9.5, italic: true,
    color: colour, isTextBox: true, margin: 0, valign: "top" });
}

function card(s, opts) {
  s.addShape(pres.ShapeType.roundRect, Object.assign({ rectRadius: 0.06, fill: { color: CARD },
    shadow: soft() }, opts));
}

function bullets(s, items, opts) {
  const runs = items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i !== items.length - 1 } }));
  s.addText(runs, Object.assign({ fontFace: SANS, fontSize: 12.5, color: BODY, lineSpacing: 17,
    paraSpaceAfter: 9, isTextBox: true, margin: 0, valign: "top" }, opts));
}

// A small tag that says whether a claim is measured or intended. Used on every forward slide.
function tag(s, text, x, y, colour) {
  s.addShape(pres.ShapeType.roundRect, { x, y, w: 0.95, h: 0.26, rectRadius: 0.1, fill: { color: colour } });
  s.addText(text, { x, y, w: 0.95, h: 0.26, fontFace: SANS, fontSize: 8.5, bold: true, color: WHITE,
    align: "center", valign: "middle", isTextBox: true, margin: 0, charSpacing: 0.6 });
}

/* ---------------------------------------------------------------- 1. Title */
{
  const s = dark();
  eyebrow(s, "AGEANTIC  ·  INVESTOR OVERVIEW  ·  SEPTEMBER 2026", ICE);
  s.addText(
    [{ text: "Equity research where", options: { color: WHITE, breakLine: true } },
     { text: "every number can be checked.", options: { color: ICE } }],
    { x: M, y: 1.45, w: 11.3, h: 1.9, fontFace: SERIF, fontSize: 42, bold: true, lineSpacing: 50,
      isTextBox: true, margin: 0, valign: "top" });
  note(s, "A research platform that writes an institutional-style note on a listed company — and can prove, " +
    "figure by figure, where every number came from and how it was calculated. Independently audited over six " +
    "live runs in September 2026.",
    { x: M, y: 3.5, w: 8.8, h: 1.3, fontSize: 14, color: ICE, lineSpacing: 21 });

  [["0 of 779", "figures contradicting the filing they came from"],
   ["259 of 259", "citations verified against archived bytes"],
   ["£7.19", "average cost of a complete research report"]].forEach(([big, label], i) => {
    const x = 0.75 + i * 4.0;
    s.addShape(pres.ShapeType.roundRect, { x, y: 5.1, w: 3.6, h: 1.45, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(big, { x: x + 0.28, y: 5.22, w: 3.05, h: 0.5, fontFace: SERIF, fontSize: 26, bold: true,
      color: ICE, isTextBox: true, margin: 0, valign: "top" });
    note(s, label, { x: x + 0.28, y: 5.78, w: 3.05, h: 0.65, fontSize: 10.5, color: WHITE, lineSpacing: 13 });
  });
  cite(s, "Figures from an independent readiness audit, 12 September 2026, committed in full to the repository.", ICEMUTE);
  s.addNotes("Open on the artefact, not the ambition: this is a working system with measured results, and the " +
    "audit that produced those results is published including the parts that went against us.");
}

/* ------------------------------------------------------------ 2. What it is */
{
  const s = light();
  eyebrow(s, "WHAT IT IS", MUTE);
  heading(s, "A research analyst you can audit", NAVY);
  note(s, "It writes a full equity research note on a company that files with the SEC — every US listing, and a " +
    "UK plc filing a 20-F. You approve a costed plan before anything is spent. It fetches and archives the primary " +
    "sources itself, does all the arithmetic in ordinary tested Python, has a language model write the prose, then " +
    "attacks its own draft before you approve it into an immutable document.",
    { x: M, y: 1.62, w: 11.3, h: 1.3, fontSize: 13.5 });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 3.05, w: FULL, h: 1.25, rectRadius: 0.05, fill: { color: NAVY } });
  s.addText("Deterministic Python owns every number and every fact. The model owns planning, interpretation, " +
    "challenge and writing.",
    { x: 1.15, y: 3.3, w: 11.0, h: 0.8, fontFace: SERIF, fontSize: 16.5, italic: true, color: WHITE,
      lineSpacing: 24, isTextBox: true, margin: 0, valign: "top" });

  [["The problem", "Ask a chatbot to research a company and you get fluent prose with numbers that are right in " +
    "shape and sometimes wrong in fact. Generating a sentence and computing a cash flow are the same operation to " +
    "it — and only one of those has a right answer."],
   ["The answer", "The model is never asked to produce a number, and structurally cannot. A figure that is not a " +
    "stored fact or a recorded calculation is refused before it reaches a page. A discounted cash flow here is " +
    "forty lines of Python with property-based tests."]].forEach(([t, b], i) => {
    const x = 0.75 + i * 6.05;
    card(s, { x, y: 4.55, w: 5.78, h: 1.95 });
    s.addText(t, { x: x + 0.35, y: 4.75, w: 5.1, h: 0.35, fontFace: SERIF, fontSize: 16, bold: true,
      color: NAVY, isTextBox: true, margin: 0, valign: "top" });
    note(s, b, { x: x + 0.35, y: 5.18, w: 5.1, h: 1.2, fontSize: 11.5, lineSpacing: 15 });
  });
  cite(s, "The split is the repository's first architectural rule, enforced in code rather than requested in a prompt.", MUTE);
  s.addNotes("If they remember one thing, it is this slide. Everything measurable downstream follows from the split.");
}

/* --------------------------------------------------------- 3. How a run works */
{
  const s = light();
  eyebrow(s, "HOW A RUN WORKS", MUTE);
  heading(s, "Five stages, and you hold the gates", NAVY);

  const stages = [
    ["Plan", "It proposes sections, sources, a cost in pounds and the risks. Nothing is spent until you approve."],
    ["Acquire", "Filings, prices and macro series fetched, hashed and archived. Nothing published after your as-of date."],
    ["Compute", "Statements normalised; ratios, cost of capital, DCF, bank models, scenarios, an 81-cell grid."],
    ["Write", "A model drafts each section from structured facts, then a separate adversary attacks the draft."],
    ["Approve", "Citation, consistency and plausibility checks run in code. You approve. The document is frozen and hashed."],
  ];
  stages.forEach(([t, b], i) => {
    const x = 0.75 + i * 2.4;
    const gate = i === 0 || i === 4;
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.9, w: 2.2, h: 0.62, rectRadius: 0.06,
      fill: { color: gate ? NAVY : PANEL } });
    s.addText(`${i + 1}. ${t}`, { x, y: 1.9, w: 2.2, h: 0.62, fontFace: SERIF, fontSize: 15, bold: true,
      color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    if (gate) {
      s.addText("YOU APPROVE", { x, y: 2.58, w: 2.2, h: 0.25, fontFace: SANS, fontSize: 8.5, bold: true,
        color: AMBER, align: "center", charSpacing: 1, isTextBox: true, margin: 0, valign: "top" });
    }
    note(s, b, { x, y: 2.92, w: 2.2, h: 1.8, fontSize: 10.5, lineSpacing: 14 });
  });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 5.0, w: FULL, h: 1.35, rectRadius: 0.05, fill: { color: CARD } });
  note(s, "A complete run takes about half an hour of machine time and costs about £7. It stops for your decision " +
    "six or seven times — the plan, the peer set, the themes, any unmapped accounting concepts, the valuation " +
    "assumptions, and the finished draft. Nothing reaches a document that you did not approve, and nothing is " +
    "spent past a ceiling enforced in code.",
    { x: 1.1, y: 5.22, w: 11.1, h: 1.0, fontSize: 12.5, color: INK });
  cite(s, "Machine time 26.5–36.8 minutes across the five audited runs; spend £6.80–£7.61, against a £10 ceiling.", MUTE);
  s.addNotes("The gates are the product, not friction. A research tool nobody approves is a content generator.");
}

/* ------------------------------------------------------------- 4. The chain */
{
  const s = dark();
  eyebrow(s, "THE PROPERTY THAT MAKES IT DIFFERENT", ICE);
  heading(s, "Every figure sits on an unbroken chain", WHITE);

  const links = ["Figure\nin a section", "Claim\nnames one fact", "Citation\nverified by code",
    "Extraction\ntext with locators", "Artefact\naddressed by hash", "The archived\nbytes"];
  links.forEach((t, i) => {
    const x = 0.75 + i * 1.99;
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.95, w: 1.8, h: 1.0, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(t, { x: x + 0.06, y: 1.95, w: 1.68, h: 1.0, fontFace: SANS, fontSize: 10.5, color: WHITE,
      align: "center", valign: "middle", isTextBox: true, margin: 0, lineSpacing: 13 });
    if (i < 5) {
      s.addText("›", { x: x + 1.79, y: 1.95, w: 0.2, h: 1.0, fontFace: SERIF, fontSize: 20, bold: true,
        color: ICE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    }
  });

  bullets(s, [
    "The model may propose a citation; only code confirms one — by re-opening the archived artefact by its hash and checking the quoted excerpt is genuinely there.",
    "A calculation stores its own formula, its inputs (each with a unit and a source) and the version of the code that produced it. Any figure can be re-derived from its own record.",
    "In the interface this is not a claim, it is a link. Click a footnote and you get the excerpt, the verifier's verdict and the document digest.",
  ], { x: M, y: 3.35, w: 11.3, h: 2.3, color: ICE, fontSize: 12.5 });

  s.addText("No chat transcript has this chain, and none can be given one after the fact.",
    { x: M, y: 5.55, w: 11.3, h: 0.55, fontFace: SERIF, fontSize: 16, italic: true, color: WHITE,
      isTextBox: true, margin: 0, valign: "top" });
  cite(s, "The chain was re-walked end to end in the audit: 3,335 calculation rows re-executed with zero divergence.", ICEMUTE);
  s.addNotes("This is the core intellectual property. It is also the thing that cannot be retrofitted onto a chat product.");
}

/* ------------------------------------------------------- 5. Capabilities today */
{
  const s = light();
  eyebrow(s, "CAPABILITIES — THE RESEARCH TOOL", MUTE);
  heading(s, "What it can do today", NAVY);

  const cols = [
    ["Sources it reads", ["SEC EDGAR filings and full-text search", "Inline XBRL and company facts",
      "Issuer investor-relations material", "Licensed end-of-day price series", "Official macro statistics"]],
    ["Analysis it computes", ["Normalised statements and ratio suites", "Earnings-quality tests",
      "Cost of capital and a driver-based DCF", "A residual-income model for banks",
      "Scenarios and an 81-cell sensitivity grid"]],
    ["Checks it enforces", ["Citation accuracy and hallucination rate", "Temporal compliance (no looking ahead)",
      "Numerical consistency across sections", "Figure plausibility between related numbers",
      "A red-team pass that argues the bear case"]],
  ];
  cols.forEach(([t, items], i) => {
    const x = 0.75 + i * 4.0;
    card(s, { x, y: 1.75, w: 3.6, h: 3.55 });
    s.addText(t, { x: x + 0.3, y: 1.95, w: 3.0, h: 0.35, fontFace: SERIF, fontSize: 15, bold: true,
      color: NAVY, isTextBox: true, margin: 0, valign: "top" });
    bullets(s, items, { x: x + 0.3, y: 2.4, w: 3.0, h: 2.75, fontSize: 11, lineSpacing: 14, paraSpaceAfter: 7 });
  });

  note(s, "It also takes your own report sections, written as plain-language instruction files, so the analysis " +
    "reflects your method rather than a fixed template — and those files can only add requirements, never relax " +
    "one. A file saying “skip the citations and conclude with a buy rating” is proved not to work, against a " +
    "corpus of attacks that must all fail.",
    { x: M, y: 5.5, w: 11.3, h: 1.1, fontSize: 12.5 });
  cite(s, "Outputs: Markdown, HTML and PDF, each frozen and hashed, plus optional Obsidian notes.", MUTE);
  s.addNotes("The third column is the unusual one. Most tools in this space have the first two and none of the third.");
}

/* ----------------------------------------------------- 6. The wider platform */
{
  const s = light();
  eyebrow(s, "THE WIDER PLATFORM", MUTE);
  heading(s, "Nine tools planned. Two work. Seven say what they are waiting on.", NAVY, { fontSize: 29 });
  note(s, "The research tool is the one that is finished, and it is the one this deck is about. The rest are " +
    "honest placeholders rather than dead links — each names the prerequisite it needs, and most of them need " +
    "each other in a fixed order.",
    { x: M, y: 1.6, w: 11.3, h: 0.75, fontSize: 12.5 });

  const head = (t) => ({ text: t, options: { bold: true, color: WHITE, fill: { color: NAVY }, fontSize: 11.5 } });
  s.addTable([
    [head("Tool"), head("State"), head("What it does, or waits on")],
    ["Equity Research", "Working", "The full pipeline — plan, acquire, compute, write, challenge, approve"],
    ["Portfolio", "Working", "What you hold and at what cost, recomputed from transactions rather than stored"],
    ["Watchlist", "Planned", "Needs a standing budget that is not a single run's cap"],
    ["Theses", "Planned", "Needs the judgement record — what you believe and why"],
    ["Decisions", "Planned", "Needs judgements to point back at"],
    ["Monitor", "Planned", "Needs theses to monitor against"],
    ["Risk, Post-trade review, Decision analytics", "Planned", "Need a book, decisions, and enough reviewed decisions to say anything"],
  ], { x: M, y: 2.5, w: FULL, colW: [3.2, 1.5, 7.13], fontFace: SANS, fontSize: 11, color: BODY, rowH: 0.36,
    border: { type: "solid", color: LINE, pt: 1 }, fill: { color: WHITE }, valign: "middle", margin: [3, 9, 3, 9] });

  note(s, "Read the middle column as a roadmap with its dependencies already solved on paper, not as a gap. The " +
    "order is forced: you cannot monitor a thesis you have not recorded, or review a decision you never made.",
    { x: M, y: 5.7, w: 11.3, h: 0.8, fontSize: 12, italic: true, color: MUTE });
  cite(s, "Explicitly out of scope, permanently: trade execution, broker connections, portfolio optimisation.", MUTE);
  s.addNotes("Volunteer the two-of-nine number. It reads as discipline when you say it and as concealment when they find it.");
}

/* ------------------------------------------------------------ 7. The evidence */
{
  const s = dark();
  eyebrow(s, "THE EVIDENCE", ICE);
  heading(s, "Audited, with the results published either way", WHITE);
  note(s, "Six live runs on three companies, three matched Claude-console baselines on identical briefs, and " +
    "twelve blind reads by independent judges. The audit and its raw results are committed to the repository.",
    { x: M, y: 1.6, w: 11.3, h: 0.75, fontSize: 13, color: ICE });

  [["0 of 779", "checkable figures contradicted the filing they came from, across five reports"],
   ["259 / 259", "citations verified by re-reading the archived bytes"],
   ["3,335", "calculation rows re-executed from stored inputs with zero divergence"],
   ["£7.19", "average per report — five runs inside an 11% band, against a console spread of 65%"],
   ["6,968", "automated tests, run with no network access and no model spend"],
   ["28 / 21", "defects found by the audit, and fixed with a regression test that failed first"]
  ].forEach(([big, label], i) => {
    const x = 0.75 + (i % 3) * 4.05;
    const y = 2.55 + Math.floor(i / 3) * 1.75;
    s.addShape(pres.ShapeType.roundRect, { x, y, w: 3.7, h: 1.5, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(big, { x: x + 0.3, y: y + 0.16, w: 3.1, h: 0.5, fontFace: SERIF, fontSize: 23, bold: true,
      color: ICE, isTextBox: true, margin: 0, valign: "top" });
    note(s, label, { x: x + 0.3, y: y + 0.7, w: 3.1, h: 0.68, fontSize: 10.5, color: WHITE, lineSpacing: 13 });
  });
  cite(s, "Readiness audit, 11–12 September 2026. £63.32 of measured spend against a £100 ceiling.", ICEMUTE);
  s.addNotes("The 28/21 tile matters as much as the accuracy tiles: it shows the system is measured and repaired, " +
    "not just asserted.");
}

/* ----------------------------------------------------------- 8. Weaknesses */
{
  const s = light();
  eyebrow(s, "WEAKNESSES", MUTE);
  heading(s, "Where it falls short today", NAVY);
  note(s, "All of this came out of our own audit, and all of it is scoped and costed.",
    { x: M, y: 1.58, w: 11.3, h: 0.4, fontSize: 12.5 });

  const rows = [
    ["It reaches no conclusion", "The report states no view. Nine of nine blind comparisons preferred a Claude console note, and six of six judges said they would not act on ours."],
    ["It withholds figures a note is read for", "No peer multiple, no segment revenue, no management guidance on any audited run — though it computed the subject's own P/E and simply never printed it."],
    ["A bank cannot yet be researched", "A bank's revenue resolves to fee income, so every margin is impossible and the run correctly refuses to publish."],
    ["Coverage is narrow", "SEC filers and 20-F foreign issuers only. A domestic London listing cannot be researched at all yet."],
    ["It is a single-user tool", "One report at a time, one operator, one machine. No authentication, no multi-user deployment."],
  ];
  rows.forEach(([t, b], i) => {
    const y = 2.15 + i * 0.92;
    s.addShape(pres.ShapeType.roundRect, { x: M, y, w: FULL, h: 0.78, rectRadius: 0.05,
      fill: { color: CARD }, line: { color: AMBER, width: 1 } });
    s.addText(t, { x: 1.05, y: y + 0.06, w: 3.5, h: 0.66, fontFace: SERIF, fontSize: 13, bold: true,
      color: AMBER, isTextBox: true, margin: 0, valign: "middle" });
    note(s, b, { x: 4.7, y: y + 0.06, w: 7.6, h: 0.66, fontSize: 11.5, lineSpacing: 14, valign: "middle" });
  });
  cite(s, "Every row is an item in the remediation plan, with its fix, its cost and the test that proves it.", MUTE);
  s.addNotes("Say this slide plainly and early. It buys the credibility that the proof slides then spend.");
}

/* ------------------------------------------------------ 9. Versus the console */
{
  const s = light();
  eyebrow(s, "THE COMPARISON", MUTE);
  heading(s, "Against a Claude console session, measured", NAVY);
  note(s, "The same briefs, the same day, judged blind by three independent readers. We lost more than we won — " +
    "and the pattern of where is the whole strategy.",
    { x: M, y: 1.58, w: 11.3, h: 0.45, fontSize: 12.5 });

  const head = (t, c) => ({ text: t, options: { bold: true, color: WHITE, fill: { color: c }, fontSize: 11.5 } });
  s.addTable([
    [head("The console wins", AMBER), head("A draw", MUTE), head("We win, structurally", GREEN)],
    ["Argument — it states a rating, a target price and an expected return",
     "Accuracy of stated figures — 0 contradicted on both sides",
     "Reproducibility — 3,335 rows re-execute; a chat has nothing to re-run"],
    ["Breadth — segment tables, guidance, named competitors",
     "Cost per note — £6.71–£11.03 against our £6.80–£7.61",
     "Provenance — hashed bytes against links, a third of which died within a day"],
    ["Speed — 14–19 minutes to a first answer against our 26–37",
     "Reading time — 30–45 minutes against 50–60",
     "Cost ceiling — ours is enforced in code; a console session has no natural stopping point"],
    ["Flexibility — you can ask it anything, mid-sentence",
     "",
     "Refusals — it declines to publish an impossible figure rather than explaining it"],
  ], { x: M, y: 2.25, w: FULL, colW: [3.94, 3.94, 3.95], fontFace: SANS, fontSize: 10.5, color: BODY,
    rowH: 0.62, border: { type: "solid", color: LINE, pt: 1 }, fill: { color: WHITE }, valign: "top",
    margin: [5, 9, 5, 9] });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 5.6, w: FULL, h: 0.95, rectRadius: 0.05, fill: { color: NAVY } });
  s.addText("Column one is execution work we have already scoped. Column three cannot be added to a chat product " +
    "at all. That asymmetry is the investment case.",
    { x: 1.15, y: 5.78, w: 11.0, h: 0.65, fontFace: SERIF, fontSize: 14.5, italic: true, color: WHITE,
      isTextBox: true, margin: 0, valign: "top" });
  cite(s, "Console baseline: Claude Opus 5 at high effort with server-side web search, priced on the same table.", MUTE);
  s.addNotes("Do not soften column one. An investor who discovers it later discounts everything else you said.");
}

/* ------------------------------------------------------------- 10. The moat */
{
  const s = dark();
  eyebrow(s, "WHY THE ADVANTAGE HOLDS", ICE);
  heading(s, "This is a category difference, not a feature gap", WHITE);

  [["A chat answers", ["Generates prose and numbers by the same mechanism",
    "Cites links that rot — a third were dead the next day",
    "Keeps no structured record between sessions",
    "Cannot be re-run to check what it told you",
    "Spends until you stop it"]],
   ["This computes", ["Arithmetic in tested Python; the model cannot state a figure",
    "Evidence hashed at the moment it is fetched",
    "A queryable record of facts, calculations and decisions",
    "Any report re-executes from its own inputs, years later",
    "A ceiling enforced before the money moves"]]
  ].forEach(([t, items], i) => {
    const x = 0.75 + i * 6.05;
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.85, w: 5.78, h: 3.55, rectRadius: 0.06,
      fill: { color: i === 0 ? PANEL : "2F5C47" } });
    s.addText(t, { x: x + 0.35, y: 2.08, w: 5.1, h: 0.35, fontFace: SERIF, fontSize: 16.5, bold: true,
      color: ICE, isTextBox: true, margin: 0, valign: "top" });
    bullets(s, items, { x: x + 0.35, y: 2.55, w: 5.1, h: 2.7, color: WHITE, fontSize: 11.5, lineSpacing: 15,
      paraSpaceAfter: 7 });
  });

  note(s, "If Anthropic ships persistent memory tomorrow, it is memory of a conversation — not a versioned " +
    "calculation engine with an immutable audit chain. The gap is architectural: one system generates, the other " +
    "executes. That is why closing it from their side means building this.",
    { x: M, y: 5.6, w: 11.3, h: 0.95, fontSize: 12.5, color: ICE });
  cite(s, "Stated as a limit on us too: we will not out-argue or out-run a frontier model, and do not intend to try.", ICEMUTE);
  s.addNotes("Expect the question 'what if Anthropic just builds this'. This slide is the answer.");
}

/* -------------------------------------------------- 11. The future, move one */
{
  const s = light();
  eyebrow(s, "THE FUTURE  —  THE CENTRAL MOVE", MUTE);
  tag(s, "VISION", 11.63, 0.42, AMBER);
  heading(s, "Stop selling reports. Start holding positions.", NAVY);
  note(s, "A single report is a commodity a chat writes well and cheaply, and competing on it means fighting on " +
    "their ground. A standing record of a company you follow is something a chat has no product for at all.",
    { x: M, y: 1.58, w: 11.3, h: 0.75, fontSize: 13 });

  [["Today", "You commission a report. You read it. It is finished, and so is the relationship.", PANEL],
   ["Next", "You follow a company. It is researched once, then watched — and when something moves, you are told what changed, against what you believed, with the arithmetic redone.", NAVY]
  ].forEach(([t, b, colour], i) => {
    const x = 0.75 + i * 6.05;
    s.addShape(pres.ShapeType.roundRect, { x, y: 2.5, w: 5.78, h: 1.85, rectRadius: 0.06, fill: { color: colour } });
    s.addText(t, { x: x + 0.35, y: 2.7, w: 5.1, h: 0.35, fontFace: SERIF, fontSize: 16, bold: true,
      color: ICE, isTextBox: true, margin: 0, valign: "top" });
    note(s, b, { x: x + 0.35, y: 3.12, w: 5.1, h: 1.1, fontSize: 12, color: WHITE, lineSpacing: 15 });
  });

  card(s, { x: M, y: 4.5, w: FULL, h: 2.05 });
  s.addText("The effect", { x: 1.1, y: 4.68, w: 3.0, h: 0.35, fontFace: SERIF, fontSize: 15, bold: true,
    color: NAVY, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "It changes the unit sold from a one-off document to a subscription with a reason to renew every month.",
    "It moves the competition off the axis we lose on — nobody compares a monitor to a chat answer, because a chat cannot tell you what changed since it last spoke to you.",
    "It is the cheapest large move available: the judgement record and the monitor already exist in the codebase, built and unused.",
  ], { x: 1.1, y: 5.12, w: 11.1, h: 1.3, fontSize: 11.5, lineSpacing: 16 });
  cite(s, "VISION — planned, not built. The prerequisites are recorded and the components exist unwired.", MUTE);
  s.addNotes("This is the slide that turns a good tool into a business. Spend time here.");
}

/* ------------------------------------------------ 12. The future, five moves */
{
  const s = light();
  eyebrow(s, "THE FUTURE  —  AND WHAT EACH CHANGES", MUTE);
  tag(s, "VISION", 11.63, 0.42, AMBER);
  heading(s, "Five more moves, and their effect", NAVY);

  const head = (t) => ({ text: t, options: { bold: true, color: WHITE, fill: { color: NAVY }, fontSize: 11.5 } });
  s.addTable([
    [head("The move"), head("What changes for the user"), head("Effect on the competitive picture")],
    ["Ask it anything, grounded", "Follow-up questions answered instantly from stored calculations — “what if the discount rate is half a point higher” re-runs the real model",
      "Closes the flexibility gap without breaking the rule. The engine already exists, unused"],
    ["Make trust visible", "A verification badge on load and one click from any number to the page it came from",
      "Turns an invisible guarantee into something a user feels in the first ten seconds"],
    ["Print what it already knows", "Peer multiples, segment revenue, guidance and a stated view reach the page",
      "Converts today's clear losses into ties. Mostly plumbing, not new capability"],
    ["Reproduce it live", "Replay a six-month-old report on stage and show the numbers land identically",
      "Makes the strongest claim a demonstration rather than an assertion"],
    ["Build for the accountable buyer", "Authentication, sharing, and an evidence pack built for someone else's scrutiny",
      "Serves the user who actually has a reason to pay: one who must defend the work"],
  ], { x: M, y: 1.8, w: FULL, colW: [3.0, 4.6, 4.23], fontFace: SANS, fontSize: 10.5, color: BODY,
    rowH: 0.58, border: { type: "solid", color: LINE, pt: 1 }, fill: { color: WHITE }, valign: "top",
    margin: [5, 9, 5, 9] });

  note(s, "Together with the monitor, these take the product from “an evidence base you can trust” to “the " +
    "research you keep”. The first four are scoped and costed at roughly sixty working sessions and £64 of live " +
    "model spend; the fifth is a deliberate change of who the product is for.",
    { x: M, y: 5.9, w: 11.3, h: 0.8, fontSize: 12 });
  cite(s, "VISION — from the remediation plan committed 13 September 2026, with targets set before the work starts.", MUTE);
  s.addNotes("Note the pattern: four of six moves are connecting things already built. That is why the estimate is " +
    "large and the risk is not.");
}

/* ------------------------------------------------------- 13. What we concede */
{
  const s = dark();
  eyebrow(s, "WHAT WE WILL NOT WIN, ON PURPOSE", ICE);
  heading(s, "Three things we concede to the chat", WHITE);

  [["Breadth of commentary", "Analyst opinion, market narrative, anything outside the filings. Closing it means licensing, and our buyer is not paying for it."],
   ["Speed on a cold subject", "A chat answers a company nobody has researched in minutes. Our guarantees cost time, and we would rather keep the guarantees."],
   ["Open-ended conversation", "A general model will always range wider. We answer narrowly and prove it, which is a different promise."]
  ].forEach(([t, b], i) => {
    const x = 0.75 + i * 4.05;
    s.addShape(pres.ShapeType.roundRect, { x, y: 2.0, w: 3.7, h: 2.35, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(t, { x: x + 0.3, y: 2.22, w: 3.1, h: 0.7, fontFace: SERIF, fontSize: 15, bold: true, color: ICE,
      isTextBox: true, margin: 0, valign: "top" });
    note(s, b, { x: x + 0.3, y: 3.0, w: 3.1, h: 1.2, fontSize: 11.5, color: WHITE, lineSpacing: 15 });
  });

  s.addText("A product that claims to beat a frontier model at everything is telling you it has not measured " +
    "itself. We have, and we publish the losses.",
    { x: M, y: 4.85, w: 11.3, h: 0.9, fontFace: SERIF, fontSize: 16.5, italic: true, color: ICE,
      lineSpacing: 24, isTextBox: true, margin: 0, valign: "top" });
  cite(s, "The nine blind comparisons we lost are committed to the repository with the rubrics that scored them.", ICEMUTE);
  s.addNotes("Counter-intuitively this is a trust-building slide, not a weakness slide. Deliver it with confidence.");
}

/* ----------------------------------------------------- 14. Extra suggestions */
{
  const s = light();
  eyebrow(s, "FURTHER OPPORTUNITIES", MUTE);
  tag(s, "IDEAS", 11.63, 0.42, MUTE);
  heading(s, "Six more worth considering", NAVY);
  note(s, "Not in the plan, not costed — but each follows naturally from what the architecture already guarantees.",
    { x: M, y: 1.58, w: 11.3, h: 0.4, fontSize: 12.5 });

  const ideas = [
    ["A provable track record", "Every report is immutable and hashed, so the views it recorded can be scored against what actually happened — a published, tamper-evident record of being right or wrong. No competitor in this space can offer one."],
    ["Model portability", "Because arithmetic lives in Python, the language model is swappable. That is a hedge against a supplier's price or policy, and margin that improves as models commoditise."],
    ["A second-opinion mode", "Run a brief through both the platform and a raw chat, then diff them and show exactly where they disagree. It turns the competitor into a feature."],
    ["Fixed-price research", "Because cost is bounded in code rather than estimated, a flat price per report can be offered with confidence a metered competitor cannot match."],
    ["Breadth by filing type", "The cheapest coverage win is not new countries but new documents — S-1s, 8-K exhibits, 13F holdings — all reachable through a source already wired."],
    ["A methodology library", "User-written sections are additive-only and attack-tested. A shared library of methods — a screen, a checklist, a house style — is a network effect the safety model already permits."],
  ];
  ideas.forEach(([t, b], i) => {
    const x = 0.75 + (i % 3) * 4.0;
    const y = 2.2 + Math.floor(i / 3) * 2.3;
    card(s, { x, y, w: 3.6, h: 2.05 });
    s.addText(t, { x: x + 0.3, y: y + 0.18, w: 3.0, h: 0.35, fontFace: SERIF, fontSize: 14, bold: true,
      color: NAVY, isTextBox: true, margin: 0, valign: "top" });
    note(s, b, { x: x + 0.3, y: y + 0.62, w: 3.0, h: 1.3, fontSize: 10.5, lineSpacing: 14 });
  });
  cite(s, "IDEAS — unscoped. Listed because each is cheap relative to what it would return, not because it is planned.", MUTE);
  s.addNotes("The first is the strongest: an auditable track record is the one marketing asset that compounds and " +
    "that nobody else in the category can manufacture.");
}

/* --------------------------------------------------------------- 15. Close */
{
  const s = dark();
  eyebrow(s, "IN ONE LINE", ICE);
  s.addText([
    { text: "A chat gives you an answer you have to trust.", options: { color: ICE, breakLine: true } },
    { text: "This gives you research you can keep.", options: { color: WHITE } }],
    { x: M, y: 1.55, w: FULL, h: 1.7, fontFace: SERIF, fontSize: 30, bold: true, lineSpacing: 44,
      isTextBox: true, margin: 0, valign: "top" });
  note(s, "Built and audited: the chain, the arithmetic, the cost ceiling, the refusals. Scoped and costed: the " +
    "figures it withholds, the view it does not state, the bank it cannot research. Designed and not yet built: " +
    "the monitor that turns a report into a position you hold.",
    { x: M, y: 3.5, w: 11.3, h: 1.0, fontSize: 13.5, color: ICE });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.75, w: FULL, h: 1.5, rectRadius: 0.06, fill: { color: PANEL } });
  s.addText("Where the money goes", { x: 1.1, y: 4.95, w: 6.0, h: 0.32, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 1.4, color: ICE, isTextBox: true, margin: 0, valign: "top" });
  note(s, "The monitor and the standing record   ·   the figures the report already computes but never prints   ·   " +
    "a stated view   ·   and the authentication and sharing that let somebody other than its author use it.",
    { x: 1.1, y: 5.38, w: 11.1, h: 0.8, fontSize: 12.5, color: WHITE, lineSpacing: 18 });
  cite(s, "Ageantic — a local-first, auditable equity research platform. Not regulated investment advice.", ICEMUTE);
  s.addNotes("No number on the slide. State the ask in the room, against whichever of the four they care about most.");
}

pres.writeFile({ fileName: process.argv[2] || "ageantic-investor-overview.pptx" })
  .then((f) => console.log("wrote " + f));
