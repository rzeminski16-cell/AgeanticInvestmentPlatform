/*
 * The investor deck: why an auditable research record beats a chat answer.
 *
 * Every figure on every slide is read from the readiness audit of 2026-09-12
 * (docs/plan/readiness-audit-2026-09.md and its results folder). sources.md maps
 * each one back to the section it came from. Nothing here is estimated.
 *
 *   node build_deck.js [output.pptx]
 */

const pptxgen = require("pptxgenjs");

const NAVY = "1E2761";
const PANEL = "26306A"; // a card on a navy ground
const ICE = "CADCFC";
const WHITE = "FFFFFF";
const INK = "1B2140";
const BODY = "3E466B";
const MUTE = "7B83A6";
const ICEMUTE = "8FA3D8";
const AMBER = "B8862F";
const CARD = "F2F5FC";
const LINE = "D8E0F2";

const SERIF = "Cambria";
const SANS = "Calibri";

const M = 0.75;
const FULL = 11.83;
const NB = " "; // keeps "5.83 %" off two lines

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.author = "Ageantic";
pres.company = "Ageantic";
pres.title = "Not a better chat";
pres.subject = "Why an auditable research record beats a chat answer";

// pptxgenjs converts option objects to EMU in place, so every call gets its own.
const soft = () => ({ type: "outer", color: "1E2761", opacity: 0.13, blur: 12, offset: 3, angle: 90 });

function dark() {
  const s = pres.addSlide();
  s.background = { color: NAVY };
  return s;
}

function light() {
  const s = pres.addSlide();
  s.background = { color: WHITE };
  return s;
}

function eyebrow(s, text, colour) {
  s.addText(text, {
    x: M, y: 0.44, w: 9.5, h: 0.3,
    fontFace: SANS, fontSize: 11, bold: true, charSpacing: 2.2,
    color: colour, isTextBox: true, margin: 0, valign: "top",
  });
}

function heading(s, text, colour, opts) {
  s.addText(text, Object.assign({
    x: M, y: 0.85, w: FULL, h: 0.62,
    fontFace: SERIF, fontSize: 33, bold: true,
    color: colour, isTextBox: true, margin: 0, valign: "top",
  }, opts || {}));
}

// Paragraphs and labels sit at the top of their box, so neighbouring blocks line up.
function note(s, text, opts) {
  s.addText(text, Object.assign({
    fontFace: SANS, fontSize: 12.5, color: BODY, lineSpacing: 17,
    isTextBox: true, margin: 0, valign: "top",
  }, opts));
}

// The deck's motif: every slide says where its figures come from.
function cite(s, text, colour) {
  s.addText(text, {
    x: M, y: 6.86, w: FULL, h: 0.3,
    fontFace: SANS, fontSize: 9.5, italic: true,
    color: colour, isTextBox: true, margin: 0, valign: "top",
  });
}

function card(s, opts) {
  s.addShape(pres.ShapeType.roundRect, Object.assign({
    rectRadius: 0.06, fill: { color: CARD }, shadow: soft(),
  }, opts));
}

function bullets(s, items, opts) {
  const runs = items.map((t, i) => ({
    text: t,
    options: { bullet: true, breakLine: i !== items.length - 1 },
  }));
  s.addText(runs, Object.assign({
    fontFace: SANS, fontSize: 12.5, color: BODY, lineSpacing: 17,
    paraSpaceAfter: 9, isTextBox: true, margin: 0, valign: "top",
  }, opts));
}

/* ------------------------------------------------------------------ 1. Title */
{
  const s = dark();
  eyebrow(s, "AGEANTIC  ·  EQUITY RESEARCH PLATFORM", ICE);
  s.addText(
    [
      { text: "Not a better chat.", options: { color: WHITE, breakLine: true } },
      { text: "A different artefact.", options: { color: ICE } },
    ],
    { x: M, y: 1.35, w: 10.5, h: 1.9, fontFace: SERIF, fontSize: 46, bold: true, lineSpacing: 54, isTextBox: true, margin: 0, valign: "top" },
  );
  note(s,
    "Every figure in a research note here re-executes from stored inputs, resolves to hashed bytes, and " +
      "costs a number you can read off a ledger. A chat answer does none of those three things. An " +
      "independent readiness audit measured the difference across six live runs and three Claude-console " +
      "baselines on the same briefs.",
    { x: M, y: 3.45, w: 8.6, h: 1.45, fontSize: 14, color: ICE, lineSpacing: 21 },
  );

  const stats = [
    ["0 of 779", "checkable figures contradicting the filing they came from, across five reports"],
    ["259 of 259", "citations verified by re-reading the archived bytes, not by asking the model"],
    ["3,335", "calculation rows re-executed from stored inputs — zero divergence"],
  ];
  stats.forEach(([big, label], i) => {
    const x = 0.75 + i * 4.0;
    s.addShape(pres.ShapeType.roundRect, { x, y: 5.05, w: 3.6, h: 1.5, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(big, { x: x + 0.28, y: 5.18, w: 3.05, h: 0.5, fontFace: SERIF, fontSize: 26, bold: true, color: ICE, isTextBox: true, margin: 0, valign: "top" });
    note(s, label, { x: x + 0.28, y: 5.76, w: 3.05, h: 0.72, fontSize: 10.5, color: WHITE, lineSpacing: 13 });
  });
  cite(s, "Readiness audit, 11–12 September 2026 — six live runs, three console baselines, £63.32 of measured spend.", ICEMUTE);
  s.addNotes(
    "The claim is not that our prose is better than Claude's. It is that we produce a different kind of thing: " +
      "a research record. The three numbers at the bottom are the whole argument, and each was measured by an " +
      "audit whose raw results ship with the repository.",
  );
}

/* ------------------------------------------------------- 2. The problem */
{
  const s = light();
  eyebrow(s, "THE PROBLEM", MUTE);
  heading(s, "A brilliant answer you cannot check", NAVY);
  note(s,
    "Three console notes, written to the same brief as our own runs and measured by the same audit. " +
      "They were accurate. They were also unauditable.",
    { x: M, y: 1.62, w: 10.6, h: 0.5, fontSize: 13.5 },
  );

  const cols = [
    ["159", "URLs cited across the three notes — and no response carried a machine-readable citation at all. The references are the model's own prose."],
    ["54", "of those links no longer resolved when the audit fetched them the next day. A third of the evidence was already gone."],
    ["65 %", "spread in what the same three briefs cost: £6.71 to £11.03, with no ledger to inspect and no cap that binds."],
  ];
  cols.forEach(([big, label], i) => {
    const x = 0.75 + i * 4.0;
    card(s, { x, y: 2.35, w: 3.6, h: 2.25 });
    s.addText(big, { x: x + 0.3, y: 2.52, w: 3.0, h: 0.7, fontFace: SERIF, fontSize: 38, bold: true, color: NAVY, isTextBox: true, margin: 0, valign: "top" });
    note(s, label, { x: x + 0.3, y: 3.32, w: 3.0, h: 1.15, fontSize: 11.5, lineSpacing: 15 });
  });

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 5.05, w: FULL, h: 1.25, rectRadius: 0.05, fill: { color: NAVY } });
  s.addText(
    "Nothing in a chat transcript re-executes. When the links rot, what is left is a memory of an answer — " +
      "and no way to tell which of yesterday's numbers survived the night.",
    { x: 1.15, y: 5.3, w: 11.0, h: 0.85, fontFace: SERIF, fontSize: 16.5, italic: true, color: WHITE, lineSpacing: 24, isTextBox: true, margin: 0, valign: "top" },
  );
  cite(s, "Readiness audit §3.2 — every cited URL fetched once through the platform's own guarded fetcher; §3.3 for the prices.", MUTE);
  s.addNotes(
    "This is not a swipe at the console. The console's figures were accurate: the audit found nothing " +
      "contradicting the filings. The problem is that neither you nor I can demonstrate that without doing " +
      "the work again by hand.",
  );
}

/* ------------------------------------------------------------ 3. The rule */
{
  const s = dark();
  eyebrow(s, "HOW IT WORKS", ICE);
  heading(s, "One rule decides what the machine may do", WHITE);
  s.addText(
    "Deterministic Python owns every number and every fact. The model owns planning, interpretation, " +
      "challenge and writing.",
    { x: M, y: 1.58, w: 11.2, h: 0.8, fontFace: SERIF, fontSize: 17, italic: true, color: ICE, lineSpacing: 24, isTextBox: true, margin: 0, valign: "top" },
  );

  const panes = [
    ["Code owns", [
      "All arithmetic — ratios, growth, cost of capital, discounted cash flow, scenarios",
      "Fetching, hashing, caching and point-in-time filtering",
      "Units and currency, carried through every operation; a mismatch raises",
      "Citation verification: re-reading the artefact by hash and finding the excerpt",
      "Cost metering, and the caps that stop a run rather than warn about it",
    ]],
    ["The model owns", [
      "Planning the research and forming the queries",
      "Judging which sources are worth reading",
      "Proposing assumptions, each with a justification you approve or refuse",
      "Red-teaming the thesis it has just been shown",
      "Writing prose from facts that are already structured",
    ]],
  ];
  panes.forEach(([title, items], i) => {
    const x = 0.75 + i * 6.05;
    s.addShape(pres.ShapeType.roundRect, { x, y: 2.5, w: 5.78, h: 3.25, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(title, { x: x + 0.35, y: 2.72, w: 5.1, h: 0.4, fontFace: SERIF, fontSize: 17, bold: true, color: ICE, isTextBox: true, margin: 0, valign: "top" });
    bullets(s, items, { x: x + 0.35, y: 3.22, w: 5.1, h: 2.4, color: WHITE, fontSize: 11.5, lineSpacing: 15, paraSpaceAfter: 7 });
  });

  note(s,
    "A discounted cash flow is forty lines of Python with unit tests. It is not a reasoning task — and prose " +
      "is the most reliable way yet found to be confidently wrong about a number.",
    { x: M, y: 5.95, w: 11.3, h: 0.7, color: ICE },
  );
  cite(s, "The rule is the repository's first convention, and the invariants beneath it are enforced in code, not in prompts.", ICEMUTE);
  s.addNotes(
    "This is the architectural bet, and everything measurable downstream follows from it. Move one " +
      "calculation into a prompt and the whole chain of evidence stops being checkable.",
  );
}

/* ------------------------------------------------------- 4. Proof: accuracy */
{
  const s = light();
  eyebrow(s, "PROOF ONE  —  ACCURACY", MUTE);
  heading(s, "Every figure, held against its filing", NAVY);

  const facts = [
    ["0 / 779", "checkable figures across five reports contradicted the filing they were drawn from."],
    ["259 / 259", "citations verified by re-reading the stored bytes and finding the quoted excerpt in them."],
  ];
  facts.forEach(([big, label], i) => {
    const y = 1.85 + i * 1.6;
    s.addText(big, { x: M, y, w: 3.3, h: 0.8, fontFace: SERIF, fontSize: 42, bold: true, color: NAVY, isTextBox: true, margin: 0, valign: "top" });
    note(s, label, { x: 4.2, y: y + 0.12, w: 3.15, h: 1.15, fontSize: 12, lineSpacing: 16 });
  });
  note(s,
    "Checked independently. The audit rebuilt the basket of figures from the SEC's own company-facts data " +
      "with its own arithmetic, so the result does not rest on the platform's word for itself.",
    { x: M, y: 4.95, w: 6.6, h: 0.95 },
  );

  card(s, { x: 7.75, y: 1.85, w: 4.83, h: 4.25 });
  s.addText("And the honest half", { x: 8.05, y: 2.08, w: 4.2, h: 0.4, fontFace: SERIF, fontSize: 17, bold: true, color: NAVY, isTextBox: true, margin: 0, valign: "top" });
  note(s, "On the figures it chose to state, the console was just as accurate:", { x: 8.05, y: 2.58, w: 4.25, h: 0.55, fontSize: 12, lineSpacing: 16 });
  bullets(s, [
    "0 contradicted of 154 — Microsoft",
    "0 contradicted of 107 — M&T Bank",
    "1 flagged of 179 — AstraZeneca, and it is a definition the note itself discloses, not a wrong number",
  ], { x: 8.05, y: 3.2, w: 4.25, h: 1.6, fontSize: 11.5 });
  s.addText(
    "The difference between us is not accuracy. It is whether anybody can tell.",
    { x: 8.05, y: 4.95, w: 4.25, h: 0.95, fontFace: SERIF, fontSize: 15, bold: true, italic: true, color: AMBER, lineSpacing: 20, isTextBox: true, margin: 0, valign: "top" },
  );
  cite(s, "Readiness audit §3.2 — the platform's own metrics and the audit's independent matcher, calibrated until every disputed reading was resolved on both sides.", MUTE);
  s.addNotes(
    "Accuracy is a draw, and we say so. Anyone selling you accuracy against a frontier model on public " +
      "filings is selling you something the model already does well.",
  );
}

/* -------------------------------------------------- 5. Proof: reproducibility */
{
  const s = light();
  eyebrow(s, "PROOF TWO  —  REPRODUCIBILITY", MUTE);
  heading(s, "A report that re-executes", NAVY);
  note(s,
    "Every figure stores the formula that produced it, each input with its unit and its source, and the " +
      "version of the code that ran. One command re-executes the lot and compares.",
    { x: M, y: 1.72, w: 5.5, h: 1.0 },
  );
  bullets(s, [
    "3,335 calculation rows re-run across five runs — zero divergence",
    "259 citations re-verified from the stored bytes",
    "559 artefacts re-hashed, every one intact",
    "36 audit events walked, the chain unbroken",
  ], { x: M, y: 2.85, w: 5.5, h: 1.8, fontSize: 12.5 });
  s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.8, w: 5.5, h: 1.45, rectRadius: 0.05, fill: { color: NAVY } });
  note(s,
    "A chat answer has nothing to re-run. Ask it again tomorrow and you get a different answer, with no way " +
      "to tell which one was right.",
    { x: 1.05, y: 5.0, w: 4.9, h: 1.05, color: WHITE },
  );

  s.addChart(
    pres.ChartType.bar,
    [{ name: "Calculation rows re-derived", labels: ["Microsoft #1", "Microsoft #2", "AstraZeneca #1", "AstraZeneca #2", "M&T Bank"], values: [852, 857, 152, 831, 643] }],
    {
      x: 6.6, y: 1.95, w: 6.0, h: 3.6,
      barDir: "col", chartColors: [NAVY, NAVY, MUTE, NAVY, NAVY],
      showValue: true, dataLabelPosition: "outEnd", dataLabelColor: INK, dataLabelFontFace: SANS, dataLabelFontSize: 11,
      showLegend: false, showTitle: false,
      catAxisLabelColor: BODY, catAxisLabelFontFace: SANS, catAxisLabelFontSize: 10.5,
      valAxisLabelColor: MUTE, valAxisLabelFontFace: SANS, valAxisLabelFontSize: 10,
      valGridLine: { color: LINE, size: 1 }, catGridLine: { style: "none" },
      valAxisMinVal: 0, valAxisMaxVal: 1000, valAxisMajorUnit: 250, barGapWidthPct: 55,
    },
  );
  note(s,
    "AstraZeneca's first run withheld its valuation for want of a share count the accounting tags did not " +
      "carry; one mapping fix found by this audit restored 679 rows and a full discounted cash flow.",
    { x: 6.6, y: 5.7, w: 6.0, h: 0.85, fontSize: 10.5, italic: true, color: MUTE, lineSpacing: 14 },
  );
  cite(s, "Readiness audit §3.1 and §4.3 — replay output for each run is committed beside the reports.", MUTE);
  s.addNotes(
    "Reproducibility is the thing a chat structurally cannot offer, and it is what turns a research note " +
      "into a record you can defend a year later.",
  );
}

/* ------------------------------------------------------------ 6. Proof: cost */
{
  const s = light();
  eyebrow(s, "PROOF THREE  —  COST", MUTE);
  heading(s, "Priced to the penny, and predictable", NAVY);

  s.addChart(
    pres.ChartType.bar,
    [{
      name: "Cost per report (£)",
      labels: ["MSFT #1", "MSFT #2", "AZN #1", "AZN #2", "M&T", "Console\nMSFT", "Console\nAZN", "Console\nM&T"],
      values: [7.48, 6.80, 6.83, 7.21, 7.61, 7.63, 11.03, 6.71],
    }],
    {
      x: 0.6, y: 1.9, w: 7.6, h: 4.35,
      barDir: "col", chartColors: [NAVY, NAVY, NAVY, NAVY, NAVY, AMBER, AMBER, AMBER],
      showValue: true, dataLabelPosition: "outEnd", dataLabelColor: INK, dataLabelFontFace: SANS,
      dataLabelFontSize: 10.5, dataLabelFormatCode: '"£"0.00',
      showLegend: false, showTitle: false,
      catAxisLabelColor: BODY, catAxisLabelFontFace: SANS, catAxisLabelFontSize: 10,
      valAxisLabelColor: MUTE, valAxisLabelFontFace: SANS, valAxisLabelFontSize: 10,
      valGridLine: { color: LINE, size: 1 }, catGridLine: { style: "none" },
      valAxisMinVal: 0, valAxisMaxVal: 12, valAxisMajorUnit: 3, barGapWidthPct: 45,
    },
  );

  card(s, { x: 8.55, y: 1.9, w: 4.05, h: 4.35 });
  s.addText("£7.19", { x: 8.85, y: 2.08, w: 3.45, h: 0.7, fontFace: SERIF, fontSize: 38, bold: true, color: NAVY, isTextBox: true, margin: 0, valign: "top" });
  note(s, "average per report, against the £10 the operator set as the ceiling.", { x: 8.85, y: 2.85, w: 3.45, h: 0.65, fontSize: 12, lineSpacing: 16 });
  bullets(s, [
    "11 % spread across five runs; 65 % across three console notes",
    "The cap was never raised and no run breached it — the ceiling is code, not a warning",
    "Every model call is priced through one router and written to a ledger that adds up row by row",
  ], { x: 8.85, y: 3.6, w: 3.45, h: 2.5, fontSize: 11.5 });
  cite(s, "Readiness audit §3.3 — platform spend read from the costs table; console spend priced from the same table on the API's reported usage.", MUTE);
  s.addNotes(
    "Predictability matters more than the average. A research budget you can plan is a different product " +
      "from one that varies by two thirds on the same question.",
  );
}

/* --------------------------------------------------------------- 7. The bank */
{
  const s = dark();
  eyebrow(s, "WHAT THE DISCIPLINE ACTUALLY BUYS", ICE);
  heading(s, "The run we are proudest of is the one that refused to publish", WHITE, { fontSize: 30, h: 1.15 });
  note(s,
    "M&T Bank. Eighteen sections written, 643 calculations, every figure traceable to its filing and not one " +
      "of them contradicting it. Then the check that reads the relations between figures found eleven that " +
      "are impossible — a net margin above one, income larger than revenue — because a bank's revenue is not " +
      "a caption M&T has reported since 2023. The report was refused at the final gate. It was never published.",
    { x: M, y: 2.15, w: 6.85, h: 2.1, fontSize: 13, color: ICE, lineSpacing: 19 },
  );
  s.addText(
    "A chat would have written that sentence and moved on. We would rather publish nothing than publish a " +
      "number that cannot be true.",
    { x: M, y: 4.5, w: 6.85, h: 1.1, fontFace: SERIF, fontSize: 16, italic: true, color: WHITE, lineSpacing: 23, isTextBox: true, margin: 0, valign: "top" },
  );

  const rows = [
    ["11", "impossible relations caught by a check no prompt performs"],
    ["0", "reports published while one of them stood"],
    ["1", "open decision: what a bank's revenue is — ours to take, not the model's"],
  ];
  rows.forEach(([big, label], i) => {
    const y = 2.0 + i * 1.45;
    s.addShape(pres.ShapeType.roundRect, { x: 8.0, y, w: 4.6, h: 1.25, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(big, { x: 8.3, y: y + 0.2, w: 0.9, h: 0.85, fontFace: SERIF, fontSize: 30, bold: true, color: ICE, isTextBox: true, margin: 0, valign: "middle" });
    note(s, label, { x: 9.25, y: y + 0.2, w: 3.1, h: 0.85, fontSize: 11, color: WHITE, lineSpacing: 14, valign: "middle" });
  });
  cite(s, "Readiness audit §3.2 and finding F-24 — the one blocking finding the audit left open, with the three ways to close it costed.", ICEMUTE);
  s.addNotes(
    "This is the slide that separates us from a demo. The guard caught a defect that every figure-level " +
      "check passed, and the platform's answer was to withhold the report rather than ship it.",
  );
}

/* ------------------------------------------------------ 8. Where chat wins */
{
  const s = light();
  eyebrow(s, "SAID PLAINLY", MUTE);
  heading(s, "Today, a chat writes the better note", NAVY);
  note(s,
    "Three independent judges read each document blind — an investor deciding with their own money, a reader " +
      "handed only the file, a sceptic checking the work — and then compared the pairs blind.",
    { x: M, y: 1.62, w: 11.3, h: 0.55 },
  );

  const head = (t) => ({ text: t, options: { bold: true, color: WHITE, fill: { color: NAVY }, fontSize: 12 } });
  s.addTable(
    [
      [head("The judges were asked"), head("This platform"), head("Claude console")],
      ["Would you act on it?", "no — 6 of 6", "with reservations — 6 of 6"],
      ["Is an investment view stated?", "no — 5 of 6", "yes — 6 of 6"],
      ["Is it argued, rather than recited?", "no — 5 of 6", "yes — 6 of 6"],
      ["Blind pairwise comparisons won", "0 of 6", "6 of 6"],
      ["Reading time", "30–45 minutes", "50–60 minutes"],
    ],
    {
      x: M, y: 2.42, w: FULL, colW: [5.03, 3.4, 3.4],
      fontFace: SANS, fontSize: 12, color: BODY, rowH: 0.42,
      border: { type: "solid", color: LINE, pt: 1 },
      fill: { color: WHITE }, valign: "middle", margin: [4, 10, 4, 10],
    },
  );

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 5.25, w: FULL, h: 1.25, rectRadius: 0.05, fill: { color: CARD }, line: { color: AMBER, width: 1.25 } });
  note(s,
    "The reasons were the same every time: our header says “no view reached”, the brief's questions are " +
      "answered only partly, and the multiples a research note is read for are withheld on every run.",
    { x: 1.1, y: 5.5, w: 11.1, h: 0.85, color: INK, lineSpacing: 18 },
  );
  cite(s, "Readiness audit §4.1 — twelve blind reads and six blind comparisons; the raw rubrics are committed with the audit.", MUTE);
  s.addNotes(
    "Put this slide in front of them early. An investor who finds this out later will not trust the rest; " +
      "an investor who is told it up front will believe the proof slides.",
  );
}

/* --------------------------------------------------------- 9. The asymmetry */
{
  const s = light();
  eyebrow(s, "THE ASYMMETRY", MUTE);
  heading(s, "One gap is wiring. The other is architecture.", NAVY);

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.8, w: 5.78, h: 4.2, rectRadius: 0.06, fill: { color: NAVY } });
  s.addText("What we lack is named and scoped", { x: 1.1, y: 2.02, w: 5.1, h: 0.4, fontFace: SERIF, fontSize: 16.5, bold: true, color: ICE, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "Peer multiples — acquire eight peers' filings, which EDGAR gives away",
    "Segment revenue and guidance — extraction work, not intelligence",
    "News in the body — raise one evidence ceiling by a single tier",
    "A bank's revenue — one function and one recorded definition",
    "A stated view — the model already writes the prose; today the policy withholds the verdict",
  ], { x: 1.1, y: 2.55, w: 5.1, h: 3.3, color: WHITE, fontSize: 12 });

  s.addShape(pres.ShapeType.roundRect, { x: 6.8, y: 1.8, w: 5.78, h: 4.2, rectRadius: 0.06, fill: { color: CARD }, line: { color: AMBER, width: 1.25 } });
  s.addText("What a chat lacks cannot be bolted on", { x: 7.15, y: 2.02, w: 5.1, h: 0.4, fontFace: SERIF, fontSize: 16.5, bold: true, color: AMBER, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "Evidence hashed at the moment it is fetched — or it is hearsay for ever",
    "Arithmetic outside the prose, with units that raise instead of coercing",
    "A cost ledger, and a cap that stops the work rather than warning about it",
    "Point-in-time enforced when a page is acquired, not when it is quoted",
    "A record that still re-executes a year after anybody remembers the question",
  ], { x: 7.15, y: 2.55, w: 5.1, h: 3.3, fontSize: 12 });

  s.addText(
    "Closing our list is a quarter's work on a system that exists. Closing theirs means building this one.",
    { x: M, y: 6.25, w: FULL, h: 0.45, fontFace: SERIF, fontSize: 14.5, bold: true, italic: true, color: NAVY, isTextBox: true, margin: 0, valign: "top" },
  );
  cite(s, "Readiness audit §8 — each item on the left is a recorded decision with its options and its cost set out.", MUTE);
  s.addNotes(
    "The left-hand column is deliberately unflattering and deliberately specific: every item has a named fix " +
      "in the audit. That specificity is the argument — we know exactly what is missing.",
  );
}

/* --------------------------------------------------------- 10. Self-audit */
{
  const s = dark();
  eyebrow(s, "HOW WE KNOW ANY OF THIS", ICE);
  heading(s, "We audited ourselves the way we audit a filer", WHITE);

  const tiles = [
    ["28", "findings, of which 21 are fixed — each with a regression test that failed first"],
    ["6,968", "automated tests, run with no network access and no model spend"],
    ["559", "stored artefacts re-hashed, every one intact"],
    ["36", "audit events walked with the chain unbroken"],
    ["£63.32", "of a £100 audit budget, itemised run by run"],
    ["6 of 6", "blind comparisons lost to the console — measured, written down, published"],
  ];
  tiles.forEach(([big, label], i) => {
    const x = 0.75 + (i % 3) * 4.05;
    const y = 2.0 + Math.floor(i / 3) * 1.85;
    s.addShape(pres.ShapeType.roundRect, { x, y, w: 3.7, h: 1.6, rectRadius: 0.06, fill: { color: PANEL } });
    s.addText(big, { x: x + 0.3, y: y + 0.18, w: 3.1, h: 0.52, fontFace: SERIF, fontSize: 25, bold: true, color: ICE, isTextBox: true, margin: 0, valign: "top" });
    note(s, label, { x: x + 0.3, y: y + 0.76, w: 3.1, h: 0.72, fontSize: 10.5, color: WHITE, lineSpacing: 13 });
  });

  s.addText(
    "The audit is committed to the repository with its raw rubrics, its scores, its own defects and the six " +
      "comparisons that went against us. Anything else would be marketing.",
    { x: M, y: 5.9, w: 11.3, h: 0.8, fontFace: SERIF, fontSize: 15, italic: true, color: ICE, lineSpacing: 21, isTextBox: true, margin: 0, valign: "top" },
  );
  cite(s, "Readiness audit §5 to §7 — findings, the harness's own defects, and the spend ledger.", ICEMUTE);
  s.addNotes(
    "An investor's real question is whether to believe the other slides. The answer is that the evidence " +
      "behind them is in the repository, including the parts that do not flatter us.",
  );
}

/* ------------------------------------------------- 11. Where it earns a place */
{
  const s = light();
  eyebrow(s, "TODAY, NOT EVENTUALLY", MUTE);
  heading(s, "Where it already earns its place", NAVY);

  s.addShape(pres.ShapeType.roundRect, { x: M, y: 1.8, w: 5.78, h: 3.95, rectRadius: 0.06, fill: { color: NAVY } });
  s.addText("Use it for", { x: 1.1, y: 2.02, w: 5.1, h: 0.4, fontFace: SERIF, fontSize: 17, bold: true, color: ICE, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "Building and auditing an evidence base on an SEC filer — £7.48, 42 minutes of machine time, 18 sections, 142 footnotes",
    "Walking any figure to the bytes behind it in minutes, and any objection to what it rests on",
    "Refreshing a company you have already researched, with the prior report in the plan",
    "A UK plc through its 20-F — 831 calculations and a 5.83" + NB + "% cost of capital",
  ], { x: 1.1, y: 2.55, w: 5.1, h: 3.05, color: WHITE, fontSize: 12 });

  s.addShape(pres.ShapeType.roundRect, { x: 6.8, y: 1.8, w: 5.78, h: 3.95, rectRadius: 0.06, fill: { color: CARD }, line: { color: AMBER, width: 1.25 } });
  s.addText("Not yet", { x: 7.15, y: 2.02, w: 5.1, h: 0.4, fontFace: SERIF, fontSize: 17, bold: true, color: AMBER, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "Reaching a view — the report states none, and six judges of six would not act on it",
    "A bank — until what a bank's revenue is has been decided and recorded",
    "A domestic UK filer — acquisition resolves every subject through EDGAR alone",
    "An answer before the open — 31 to 42 minutes of machine time and six human decisions; nothing useful exists at ten",
  ], { x: 7.15, y: 2.55, w: 5.1, h: 3.05, fontSize: 12 });
  s.addText(
    "The left column is a product today. The right is the roadmap, in the order the audit put it.",
    { x: M, y: 6.05, w: FULL, h: 0.45, fontFace: SERIF, fontSize: 14.5, bold: true, italic: true, color: NAVY, isTextBox: true, margin: 0, valign: "top" },
  );
  cite(s, "Readiness audit §4.3 — the suitability matrix, twelve use cases walked or proved offline.", MUTE);
  s.addNotes(
    "The left column is a real product for a real job today: an evidence base nobody has to take on trust. " +
      "The right column is the roadmap, stated as the audit stated it.",
  );
}

/* ------------------------------------------------------------- 12. The close */
{
  const s = dark();
  eyebrow(s, "THE WHOLE ARGUMENT", ICE);
  s.addText(
    [
      { text: "A chat gives you an answer you have to trust.", options: { color: ICE, breakLine: true } },
      { text: "This gives you a record you can check.", options: { color: WHITE } },
    ],
    { x: M, y: 1.55, w: FULL, h: 1.7, fontFace: SERIF, fontSize: 30, bold: true, lineSpacing: 44, isTextBox: true, margin: 0, valign: "top" },
  );
  note(s,
    "0 of 779 figures contradicting their filings   ·   259 of 259 citations verified   ·   3,335 calculation " +
      "rows re-executed with no divergence   ·   £7.19 a report, in an 11" + NB + "% band",
    { x: M, y: 3.5, w: FULL, h: 0.85, fontSize: 14, color: ICE, lineSpacing: 22 },
  );
  s.addShape(pres.ShapeType.roundRect, { x: M, y: 4.65, w: FULL, h: 1.55, rectRadius: 0.06, fill: { color: PANEL } });
  s.addText("Next, in the order the audit put them", { x: 1.1, y: 4.88, w: 6.0, h: 0.35, fontFace: SANS, fontSize: 11, bold: true, charSpacing: 1.4, color: ICE, isTextBox: true, margin: 0, valign: "top" });
  note(s,
    "Peer multiples, so a report carries EV/EBITDA and P/E   ·   a bank's revenue, so a bank can be " +
      "researched at all   ·   a stated view, so the record becomes a note somebody can act on.",
    { x: 1.1, y: 5.32, w: 11.1, h: 0.8, color: WHITE, lineSpacing: 18 },
  );
  cite(s, "Ageantic — a local-first, auditable equity research platform. A personal research tool, not regulated investment advice.", ICEMUTE);
  s.addNotes(
    "Close on the asymmetry: the three things they can check are already true, and the things that are not " +
      "yet true are named with their fixes. No ask is on the slide — put yours in the room.",
  );
}

pres.writeFile({ fileName: process.argv[2] || "why-a-research-record.pptx" }).then((f) => console.log("wrote " + f));
