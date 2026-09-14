/*
 * "V1.0 Alpha — what changes": the what-happens-next deck, for people who already
 * know and like the system.
 *
 * Drawn in the platform's own design system (Tracework, docs/design-system.md) rather
 * than the investor deck's navy, because the audience recognises it.
 *
 * Every present-tense figure is read from docs/plan/readiness-audit-2026-09.md or from
 * the repository. Every forward claim carries a PLANNED tag and no measured number.
 */

const pptxgen = require("pptxgenjs");

/* Tracework tokens. Light theme unless a slide is dark, in which case the dark-theme
   value of the same token is used -- never a second colour invented for slides. */
const DEEP = "07171D";        // canvas, dark
const PANEL = "102B35";       // surface-raised, dark
const SUNKDARK = "12343D";    // surface-selected, dark
const TEAL = "0F6673";        // verification
const TEALUP = "B5ECF0";      // verification, dark theme
const AMBER = "7A4B00";       // decision
const AMBERUP = "FFD27A";     // decision, dark theme
const AMBERWASH = "FFF3D6";
const GREEN = "14613F";
const GREENWASH = "E3F4EA";
const PLUM = "6B3F60";        // refusal
const PLUMWASH = "F6EAF2";
const CRIMSON = "9B293F";     // failure
const CRIMWASH = "FBEAED";
const INK = "15252E";
const MUTE = "52656E";
const SUBTLE = "5B6D75";
const CANVAS = "F4F7F8";
const WHITE = "FFFFFF";
const SUNKEN = "EAF0F1";
const SELECTED = "E2F3F4";
const LINE = "D3DFE2";

const SERIF = "Cambria";
const SANS = "Calibri";
const M = 0.75;
const FULL = 11.83;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "Ageantic";
pres.company = "Ageantic";
pres.title = "Ageantic V1.0 Alpha — what changes";
pres.subject = "The report becomes a loop";

const soft = () => ({ type: "outer", color: "07171D", opacity: 0.12, blur: 12, offset: 3, angle: 90 });
const lift = () => ({ type: "outer", color: "07171D", opacity: 0.30, blur: 14, offset: 4, angle: 90 });

const dark = () => { const s = pres.addSlide(); s.background = { color: DEEP }; return s; };
const light = () => { const s = pres.addSlide(); s.background = { color: CANVAS }; return s; };

function eyebrow(s, text, colour) {
  s.addText(text, { x: M, y: 0.44, w: 10.5, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 2.2, color: colour, isTextBox: true, margin: 0, valign: "top" });
}

function heading(s, text, colour, opts) {
  s.addText(text, Object.assign({ x: M, y: 0.85, w: FULL, h: 0.62, fontFace: SERIF, fontSize: 32,
    bold: true, color: colour, isTextBox: true, margin: 0, valign: "top" }, opts || {}));
}

function note(s, text, opts) {
  s.addText(text, Object.assign({ fontFace: SANS, fontSize: 12.5, color: MUTE, lineSpacing: 17,
    isTextBox: true, margin: 0, valign: "top" }, opts));
}

function cite(s, text, colour) {
  s.addText(text, { x: M, y: 6.88, w: FULL, h: 0.3, fontFace: SANS, fontSize: 9.5, italic: true,
    color: colour || SUBTLE, isTextBox: true, margin: 0, valign: "top" });
}

function card(s, opts) {
  s.addShape(pres.ShapeType.roundRect, Object.assign({ rectRadius: 0.05, fill: { color: WHITE },
    line: { color: LINE, width: 0.75 }, shadow: soft() }, opts));
}

function bullets(s, items, opts) {
  const runs = items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i !== items.length - 1 } }));
  s.addText(runs, Object.assign({ fontFace: SANS, fontSize: 12.5, color: MUTE, lineSpacing: 17,
    paraSpaceAfter: 9, isTextBox: true, margin: 0, valign: "top" }, opts));
}

/* Says whether a claim is measured today or intended. On every slide that looks forward. */
function tag(s, text, x, y, colour, w) {
  const width = w || 1.0;
  s.addShape(pres.ShapeType.roundRect, { x, y, w: width, h: 0.26, rectRadius: 0.1, fill: { color: colour } });
  s.addText(text, { x, y, w: width, h: 0.26, fontFace: SANS, fontSize: 8.5, bold: true, color: WHITE,
    align: "center", valign: "middle", isTextBox: true, margin: 0, charSpacing: 0.6 });
}

/* A numbered disc, the deck's one repeated motif. */
function disc(s, n, x, y, fill, ink) {
  s.addShape(pres.ShapeType.ellipse, { x, y, w: 0.46, h: 0.46, fill: { color: fill } });
  s.addText(String(n), { x, y, w: 0.46, h: 0.46, fontFace: SERIF, fontSize: 15, bold: true,
    color: ink || WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
}

/* ------------------------------------------------------------------ 1. Title */
{
  const s = dark();
  s.addShape(pres.ShapeType.ellipse, { x: 9.4, y: -1.5, w: 6.2, h: 6.2, fill: { color: PANEL } });
  s.addShape(pres.ShapeType.ellipse, { x: 10.9, y: 0.1, w: 3.2, h: 3.2, fill: { color: SUNKDARK } });

  s.addText("AGEANTIC  ·  V1.0 ALPHA", { x: M, y: 1.5, w: 8.6, h: 0.34, fontFace: SANS, fontSize: 12,
    bold: true, charSpacing: 3, color: TEALUP, isTextBox: true, margin: 0, valign: "top" });
  s.addText("The report becomes a loop", { x: M, y: 2.1, w: 8.6, h: 1.5, fontFace: SERIF, fontSize: 48,
    bold: true, color: WHITE, isTextBox: true, margin: 0, valign: "top", lineSpacing: 52 });
  s.addText("What changes when the next version lands — and what deliberately does not.",
    { x: M, y: 3.75, w: 8.2, h: 0.8, fontFace: SANS, fontSize: 15, color: TEALUP, isTextBox: true,
      margin: 0, valign: "top", lineSpacing: 22 });

  s.addText("Research  →  Decide  →  Hold  →  Review", { x: M, y: 5.1, w: 8.6, h: 0.4,
    fontFace: SERIF, fontSize: 19, italic: true, color: AMBERUP, isTextBox: true, margin: 0, valign: "top" });

  cite(s, "Present-tense figures are from the readiness audit of 12 September 2026. Everything forward-looking is tagged PLANNED and carries no measured claim.", "6E8A92");
  s.addNotes("The whole deck in one line: today the platform produces a document and then forgets you. V1.0 makes it remember.");
}

/* ------------------------------------------------------------ 2. Where we are */
{
  const s = light();
  eyebrow(s, "WHERE WE ARE TODAY", TEAL);
  heading(s, "The hard part already works", INK);
  note(s, "The research tool was audited across six live runs in September. What it does, it does properly.",
    { x: M, y: 1.58, w: 11.0, h: 0.5 });

  const stats = [
    ["0", "contradicted figures", "of 779 checkable, against the filings themselves", GREEN, GREENWASH],
    ["259 / 259", "citations verified", "every excerpt re-read from the hashed artefact", TEAL, SELECTED],
    ["3,335", "calculations replayed", "re-derived from the record, zero divergence", TEAL, SELECTED],
    ["£7.19", "a full report", "eighteen sections, priced and metered in code", AMBER, AMBERWASH],
  ];
  stats.forEach((st, i) => {
    const x = M + i * 2.95;
    card(s, { x, y: 2.3, w: 2.72, h: 1.98 });
    s.addText(st[0], { x: x + 0.2, y: 2.5, w: 2.32, h: 0.55, fontFace: SERIF, fontSize: 27, bold: true,
      color: st[3], isTextBox: true, margin: 0, valign: "top" });
    s.addText(st[1], { x: x + 0.2, y: 3.08, w: 2.32, h: 0.3, fontFace: SANS, fontSize: 11.5, bold: true,
      color: INK, isTextBox: true, margin: 0, valign: "top" });
    s.addText(st[2], { x: x + 0.2, y: 3.42, w: 2.32, h: 0.75, fontFace: SANS, fontSize: 10.5,
      color: MUTE, lineSpacing: 13.5, isTextBox: true, margin: 0, valign: "top" });
  });

  card(s, { x: M, y: 4.62, w: FULL, h: 1.9, fill: { color: PLUMWASH }, line: { color: "E3CFDD", width: 0.75 } });
  s.addShape(pres.ShapeType.rect, { x: M, y: 4.62, w: 0.045, h: 1.9, fill: { color: PLUM } });
  s.addText("And the honest half", { x: M + 0.35, y: 4.85, w: 11.0, h: 0.32, fontFace: SANS, fontSize: 11,
    bold: true, charSpacing: 1.6, color: PLUM, isTextBox: true, margin: 0, valign: "top" });
  s.addText("It produces a document, and then it forgets you. Nine of nine blind comparisons preferred a Claude console note, and six of six judges said they would not act on what came out. Three of six runs reached an approved report at all; £14.41 was lost to two states the interface could not leave.",
    { x: M + 0.35, y: 5.22, w: 10.9, h: 1.1, fontFace: SANS, fontSize: 12.5, color: INK, lineSpacing: 18,
      isTextBox: true, margin: 0, valign: "top" });

  cite(s, "Readiness audit, 2026-09-12 · six live runs, three console baselines, eighteen blind judge reads");
  s.addNotes("Lead with the good numbers because they are real, then name the gap without flinching. The audit is the reason anyone should believe the rest of the deck.");
}

/* ---------------------------------------------------------- 3. The one change */
{
  const s = dark();
  eyebrow(s, "THE ONE CHANGE EVERYTHING ELSE FOLLOWS FROM", TEALUP);
  heading(s, "A document you read, or a position you hold", WHITE);
  note(s, "Every other change in V1.0 exists to make this one true.",
    { x: M, y: 1.58, w: 11.0, h: 0.4, color: TEALUP });

  card(s, { x: M, y: 2.3, w: 5.3, h: 3.9, fill: { color: PANEL }, line: { color: SUNKDARK, width: 1 }, shadow: lift() });
  s.addText("TODAY", { x: M + 0.4, y: 2.62, w: 4.5, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 2, color: "8FA8AE", isTextBox: true, margin: 0, valign: "top" });
  s.addText("A report generator", { x: M + 0.4, y: 3.0, w: 4.5, h: 0.5, fontFace: SERIF, fontSize: 24,
    bold: true, color: WHITE, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "You commission a company. You get eighteen sections.",
    "Every number is checkable, and nothing reads it back.",
    "Tomorrow it knows nothing about what you decided.",
    "The next report starts from nothing and pays full price.",
  ], { x: M + 0.4, y: 3.65, w: 4.5, h: 2.3, color: "B9CBD0", fontSize: 12 });

  card(s, { x: 6.38, y: 2.3, w: 6.2, h: 3.9, fill: { color: SUNKDARK }, line: { color: TEAL, width: 1.5 }, shadow: lift() });
  s.addText("V1.0 ALPHA", { x: 6.78, y: 2.62, w: 5.4, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 2, color: TEALUP, isTextBox: true, margin: 0, valign: "top" });
  s.addText("A loop that remembers", { x: 6.78, y: 3.0, w: 5.4, h: 0.5, fontFace: SERIF, fontSize: 24,
    bold: true, color: WHITE, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "The report ends in a thesis: what you believe, and the test that would prove you wrong.",
    "The thesis becomes a decision, and the decision points back at the reasoning that authorised it.",
    "While you hold it, the platform watches the tests.",
    "When you close it, it asks whether you were right — and separately, whether you were right for the right reasons.",
  ], { x: 6.78, y: 3.65, w: 5.4, h: 2.4, color: "D6E8EB", fontSize: 12 });

  tag(s, "PLANNED", 11.28, 2.42, AMBER, 1.05);
  cite(s, "This is the thing a chat session structurally cannot do: it has no record of what you decided, and no reason to look again tomorrow.", "6E8A92");
  s.addNotes("The pivot slide. If someone only remembers one thing, it is this: the product stops being a document and becomes a loop.");
}

/* ------------------------------------------------------------- 4. Four stages */
{
  const s = light();
  eyebrow(s, "THE LOOP, STAGE BY STAGE", TEAL);
  heading(s, "Each stage writes a record the next one reads", INK);
  note(s, "Nothing here is a new app. It is four things the platform already half-knows, joined up.",
    { x: M, y: 1.58, w: 11.0, h: 0.4 });

  const stages = [
    ["RESEARCH", "The report, deeper", [
      "States a view instead of “no view reached”",
      "An adversary that argues the other side",
      "Prints the figures it already computes",
    ], TEAL, SELECTED],
    ["DECIDE", "Why, before the outcome", [
      "A thesis as premises, each with its test",
      "Buy, add, trim, exit — and pass",
      "What it does to the book you already hold",
    ], AMBER, AMBERWASH],
    ["HOLD", "Watched while you get on", [
      "Premises checked against new filings",
      "Price moves that cross a threshold",
      "A refresh when enough has changed",
    ], GREEN, GREENWASH],
    ["REVIEW", "Two questions, never conflated", [
      "Did it work out?",
      "Was the reasoning sound?",
      "Right for the right reasons · right anyway · wrong for good reasons · wrong for bad",
    ], PLUM, PLUMWASH],
  ];

  stages.forEach((st, i) => {
    const x = M + i * 2.95;
    card(s, { x, y: 2.28, w: 2.72, h: 3.95 });
    s.addShape(pres.ShapeType.rect, { x, y: 2.28, w: 2.72, h: 0.045, fill: { color: st[3] } });
    disc(s, i + 1, x + 0.2, 2.52, st[3]);
    s.addText(st[0], { x: x + 0.76, y: 2.6, w: 1.8, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
      charSpacing: 1.6, color: st[3], isTextBox: true, margin: 0, valign: "middle" });
    s.addText(st[1], { x: x + 0.2, y: 3.16, w: 2.32, h: 0.9, fontFace: SERIF, fontSize: 16, bold: true,
      color: INK, isTextBox: true, margin: 0, valign: "top", lineSpacing: 19 });
    bullets(s, st[2], { x: x + 0.2, y: 4.16, w: 2.32, h: 1.95, fontSize: 10.5, lineSpacing: 14, paraSpaceAfter: 7 });
  });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "Eighteen features, specified in docs/V1.0_Alpha/ — and the judgement layer they connect to is already built and has never been reached from a report");
  s.addNotes("The judgement layer already exists in the database: decisions, premises, findings, review verdicts. V1.0 is mostly about connecting it, not inventing it.");
}

/* --------------------------------------------------------- 5. The home screen */
{
  const s = light();
  eyebrow(s, "WHERE YOU WILL ACTUALLY LIVE", TEAL);
  heading(s, "Your home stops being a filing cabinet", INK);

  card(s, { x: M, y: 1.72, w: FULL, h: 1.12, fill: { color: SELECTED }, line: { color: "BBD9DD", width: 0.75 } });
  s.addText("“Are the reasons I bought still standing?”", { x: M + 0.4, y: 1.95, w: 11.0, h: 0.66,
    fontFace: SERIF, fontSize: 25, bold: true, italic: true, color: TEAL, isTextBox: true, margin: 0, valign: "top" });

  note(s, "The portfolio becomes a validity dashboard rather than a list of holdings. Every position is shown against the thesis that justified it, and the question the page answers is whether that thesis still holds — not what the position is worth, which your broker already tells you.",
    { x: M, y: 3.05, w: 7.1, h: 1.5, fontSize: 13, lineSpacing: 19 });

  const rows = [
    ["Holding up", "every premise still inside its threshold", GREEN, GREENWASH],
    ["Needs a look", "a premise has crossed, or a price has moved far", AMBER, AMBERWASH],
    ["Broken", "a premise has failed its own test", CRIMSON, CRIMWASH],
    ["Unwatched", "held with no thesis written — named, not hidden", PLUM, PLUMWASH],
  ];
  rows.forEach((r, i) => {
    const y = 3.05 + i * 0.87;
    card(s, { x: 8.1, y, w: 4.48, h: 0.72, fill: { color: r[3] }, line: { color: "FFFFFF", width: 0.75 } });
    s.addShape(pres.ShapeType.rect, { x: 8.1, y, w: 0.04, h: 0.72, fill: { color: r[2] } });
    s.addText(r[0], { x: 8.32, y: y + 0.09, w: 4.1, h: 0.26, fontFace: SANS, fontSize: 11.5, bold: true,
      color: r[2], isTextBox: true, margin: 0, valign: "top" });
    s.addText(r[1], { x: 8.32, y: y + 0.36, w: 4.1, h: 0.3, fontFace: SANS, fontSize: 10.5,
      color: INK, isTextBox: true, margin: 0, valign: "top" });
  });

  note(s, "And a landing page that opens with what state your book is in — not a list of notifications you have to triage before you know whether anything matters.",
    { x: M, y: 4.75, w: 7.1, h: 1.1, fontSize: 13, lineSpacing: 19 });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "Nineteen surfaces drawn and documented in docs/V1.0_Alpha/design/ — every screen, with the decision it embodies");
  s.addNotes("The operator's own correction during design: the company page is not where you live. The portfolio is.");
}

/* -------------------------------------------------------------- 6. The thesis */
{
  const s = light();
  eyebrow(s, "THE LOAD-BEARING IDEA", AMBER);
  heading(s, "Write down why, and what would break it", INK);
  note(s, "A thesis is a set of sentences, and each one carries the test that defeats it. That is the whole trick, and everything downstream depends on it.",
    { x: M, y: 1.58, w: 11.0, h: 0.5 });

  card(s, { x: M, y: 2.3, w: 7.35, h: 3.0 });
  s.addText("A premise, as the platform stores it", { x: M + 0.35, y: 2.55, w: 6.6, h: 0.3, fontFace: SANS,
    fontSize: 11, bold: true, charSpacing: 1.4, color: MUTE, isTextBox: true, margin: 0, valign: "top" });
  s.addText("“Cloud gross margin stays above 68%.”", { x: M + 0.35, y: 2.95, w: 6.6, h: 0.45,
    fontFace: SERIF, fontSize: 20, bold: true, color: INK, isTextBox: true, margin: 0, valign: "top" });

  const parts = [["METRIC", "cloud gross margin"], ["COMPARATOR", "greater than"], ["THRESHOLD", "68%"], ["REVIEW BY", "next annual filing"]];
  parts.forEach((p, i) => {
    const x = M + 0.35 + (i % 2) * 3.35;
    const y = 3.6 + Math.floor(i / 2) * 0.72;
    s.addShape(pres.ShapeType.roundRect, { x, y, w: 3.15, h: 0.6, rectRadius: 0.06, fill: { color: SUNKEN } });
    s.addText(p[0], { x: x + 0.16, y: y + 0.06, w: 2.9, h: 0.22, fontFace: SANS, fontSize: 8.5, bold: true,
      charSpacing: 1.2, color: SUBTLE, isTextBox: true, margin: 0, valign: "top" });
    s.addText(p[1], { x: x + 0.16, y: y + 0.28, w: 2.9, h: 0.28, fontFace: SANS, fontSize: 12, bold: true,
      color: TEAL, isTextBox: true, margin: 0, valign: "top" });
  });
  s.addText("Four fields, so a machine can check it. Not a paragraph of hope.", { x: M + 0.35, y: 5.0,
    w: 6.6, h: 0.26, fontFace: SANS, fontSize: 11, italic: true, color: SUBTLE, isTextBox: true, margin: 0, valign: "top" });

  card(s, { x: 8.5, y: 2.3, w: 4.08, h: 3.0, fill: { color: AMBERWASH }, line: { color: "E8D5A8", width: 0.75 } });
  s.addText("Why it matters", { x: 8.85, y: 2.55, w: 3.4, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 1.4, color: AMBER, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "The monitor has something to watch.",
    "The portfolio has something to validate.",
    "The adversary has something to attack.",
    "The review has something to judge.",
    "And you have a record of what you actually thought, written before you knew the answer.",
  ], { x: 8.85, y: 2.95, w: 3.4, h: 2.2, color: INK, fontSize: 11.5, lineSpacing: 15, paraSpaceAfter: 8 });

  card(s, { x: M, y: 5.52, w: FULL, h: 1.02, fill: { color: SUNKEN }, line: { color: LINE, width: 0.75 } });
  const rules = [["It is yours", "never the model\u2019s"], ["It is revised", "never quietly replaced"], ["It is the instrument", "everything downstream reads"]];
  rules.forEach((r, i) => {
    const x = M + 0.35 + i * 3.85;
    s.addText(r[0], { x, y: 5.72, w: 3.6, h: 0.3, fontFace: SANS, fontSize: 12.5, bold: true,
      color: TEAL, isTextBox: true, margin: 0, valign: "top" });
    s.addText(r[1], { x, y: 6.02, w: 3.6, h: 0.3, fontFace: SANS, fontSize: 11.5, color: MUTE,
      isTextBox: true, margin: 0, valign: "top" });
  });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "The tables for this already exist and have never been written to — premises already carry metric, comparator, threshold, unit and review date");
  s.addNotes("Without the thesis, nothing else in the judgement layer can exist. It is the first thing built after the deletions.");
}

/* ------------------------------------------------------------- 7. The monitor */
{
  const s = dark();
  eyebrow(s, "WHILE YOU GET ON WITH SOMETHING ELSE", TEALUP);
  heading(s, "It watches the tests, not the headlines", WHITE);
  note(s, "No alert feed. No sentiment score. It checks the specific things you said would change your mind.",
    { x: M, y: 1.58, w: 11.0, h: 0.4, color: TEALUP });

  const items = [
    ["Filings", "When a new one lands, every premise is re-measured against it — by code, from the filed figure, not by a model reading a summary."],
    ["Prices", "End of day, every day. A move large enough to cross one of your own thresholds raises a finding; ordinary noise does not."],
    ["Cadence", "Monthly by default, quarterly where that is truer. You set it per company, and change it whenever it is wrong."],
    ["Refresh", "When enough has moved, it offers to update the report rather than making you commission a new one."],
  ];
  items.forEach((it, i) => {
    const y = 2.3 + i * 1.08;
    s.addShape(pres.ShapeType.roundRect, { x: M, y, w: FULL, h: 0.95, rectRadius: 0.05, fill: { color: PANEL } });
    s.addShape(pres.ShapeType.rect, { x: M, y, w: 0.045, h: 0.95, fill: { color: TEALUP } });
    s.addText(it[0], { x: M + 0.35, y: y + 0.19, w: 2.1, h: 0.32, fontFace: SERIF, fontSize: 17, bold: true,
      color: TEALUP, isTextBox: true, margin: 0, valign: "top" });
    s.addText(it[1], { x: M + 2.55, y: y + 0.17, w: 9.0, h: 0.64, fontFace: SANS, fontSize: 12.5,
      color: "C7DADE", lineSpacing: 17, isTextBox: true, margin: 0, valign: "top" });
  });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "Deterministic Python owns every number here. The model reads what the movement means; it never decides whether the threshold was crossed.", "6E8A92");
  s.addNotes("The distinction that keeps this trustworthy: code measures the crossing, the model interprets it.");
}

/* ------------------------------------------------------------- 8. The refresh */
{
  const s = light();
  eyebrow(s, "THE ECONOMICS CHANGE", GREEN);
  heading(s, "A report stops being a purchase", INK);
  note(s, "Today a second look at a company you already own costs the same as the first. That is the wrong shape for something you hold for three years.",
    { x: M, y: 1.58, w: 11.0, h: 0.5 });

  card(s, { x: M, y: 2.32, w: 3.6, h: 2.5 });
  s.addText("A full run", { x: M + 0.3, y: 2.58, w: 3.0, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 1.4, color: MUTE, isTextBox: true, margin: 0, valign: "top" });
  s.addText("£7.19", { x: M + 0.3, y: 2.95, w: 3.0, h: 0.7, fontFace: SERIF, fontSize: 40, bold: true,
    color: INK, isTextBox: true, margin: 0, valign: "top" });
  s.addText("Eighteen sections, from nothing. Measured, today.", { x: M + 0.3, y: 3.72, w: 3.0, h: 0.8,
    fontFace: SANS, fontSize: 11.5, color: MUTE, lineSpacing: 15, isTextBox: true, margin: 0, valign: "top" });

  s.addShape(pres.ShapeType.rightArrow, { x: 4.55, y: 3.32, w: 0.85, h: 0.42, fill: { color: LINE } });

  card(s, { x: 5.58, y: 2.32, w: 3.6, h: 2.5, fill: { color: GREENWASH }, line: { color: "BCDFCB", width: 0.75 } });
  s.addText("A refresh", { x: 5.88, y: 2.58, w: 3.0, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 1.4, color: GREEN, isTextBox: true, margin: 0, valign: "top" });
  s.addText("under £2", { x: 5.88, y: 2.95, w: 3.0, h: 0.7, fontFace: SERIF, fontSize: 34, bold: true,
    color: GREEN, isTextBox: true, margin: 0, valign: "top" });
  s.addText("The target, not a measurement. Under ten minutes.", { x: 5.88, y: 3.72, w: 3.0, h: 0.8,
    fontFace: SANS, fontSize: 11.5, color: INK, lineSpacing: 15, isTextBox: true, margin: 0, valign: "top" });

  card(s, { x: 9.4, y: 2.32, w: 3.18, h: 2.5, fill: { color: WHITE } });
  s.addText("How", { x: 9.7, y: 2.58, w: 2.6, h: 0.3, fontFace: SANS, fontSize: 11, bold: true,
    charSpacing: 1.4, color: MUTE, isTextBox: true, margin: 0, valign: "top" });
  bullets(s, [
    "Fetch only what is new.",
    "Recompute every number — all of them, because that is nearly free and it is what stops the document contradicting itself.",
    "Re-write only the sections that moved.",
  ], { x: 9.7, y: 2.95, w: 2.6, h: 1.8, fontSize: 10.5, lineSpacing: 13.5, paraSpaceAfter: 6 });

  card(s, { x: M, y: 5.02, w: FULL, h: 1.5, fill: { color: SELECTED }, line: { color: "BBD9DD", width: 0.75 } });
  s.addText("And the headline is what changed", { x: M + 0.35, y: 5.24, w: 11.0, h: 0.34, fontFace: SANS,
    fontSize: 11, bold: true, charSpacing: 1.6, color: TEAL, isTextBox: true, margin: 0, valign: "top" });
  s.addText("Not the report — the delta. Which figures moved and by how much, which of your premises are affected, and what the valuation did. The previous report is archived, immutable and readable at its own address for good: superseded, never overwritten.",
    { x: M + 0.35, y: 5.62, w: 11.0, h: 0.8, fontFace: SANS, fontSize: 12.5, color: INK, lineSpacing: 18,
      isTextBox: true, margin: 0, valign: "top" });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "£7.19 is measured across the audit's runs. The refresh figure is a target the delivery plan is held to, not a result.");
  s.addNotes("This is the slide that turns a tool into something you keep using. A purchase becomes a relationship.");
}

/* --------------------------------------------------- 9. The report gets better */
{
  const s = light();
  eyebrow(s, "AND THE REPORT ITSELF", TEAL);
  heading(s, "Four things it stops refusing to do", INK);

  const four = [
    ["It states a view", "Today every report prints “no view reached” — because the field was never wired, not because no view was reached. V1.0 composes the half that is arithmetic (the range, the method, the distance from the price) and lets you author the half that is judgement. The model writes neither.", TEAL],
    ["The adversary argues the other side", "It stops re-checking arithmetic that code already checks better, and builds the strongest case against your conclusion. Long thesis, argue the short. And nothing ships unresolved.", PLUM],
    ["It prints what it already computed", "Multiples, segment revenue, the implied range, the verified excerpts. All of it computed, stored and hashed today — and thrown away before anyone reads it.", GREEN],
    ["It reads your own book", "What this position does to the concentration you already have, and to the sector exposure you already carry. Consequences, computed from your holdings. Never instructions.", AMBER],
  ];
  four.forEach((f, i) => {
    const x = M + (i % 2) * 6.04;
    const y = 1.72 + Math.floor(i / 2) * 2.42;
    card(s, { x, y, w: 5.79, h: 2.22 });
    s.addShape(pres.ShapeType.rect, { x, y, w: 5.79, h: 0.045, fill: { color: f[2] } });
    disc(s, i + 1, x + 0.3, y + 0.34, f[2]);
    s.addText(f[0], { x: x + 0.92, y: y + 0.38, w: 4.6, h: 0.4, fontFace: SERIF, fontSize: 18, bold: true,
      color: INK, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(f[1], { x: x + 0.3, y: y + 0.95, w: 5.2, h: 1.15, fontFace: SANS, fontSize: 11.5, color: MUTE,
      lineSpacing: 15.5, isTextBox: true, margin: 0, valign: "top" });
  });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "Three of these are unlocking work the platform already does. Only the first is genuinely new.");
  s.addNotes("The recurring theme of the audit: the system holds more than it shows. Three of these four are just opening the tap.");
}

/* ------------------------------------------------------------- 10. In the box */
{
  const s = light();
  eyebrow(s, "NEW IN THE BOX", TEAL);
  heading(s, "Three things you have not had before", INK);
  note(s, "None of these needs the loop. They ship because the record is already there to build them on.",
    { x: M, y: 1.58, w: 11.0, h: 0.4 });

  const boxes = [
    ["The model, in Excel", "Every report ships with a workbook carrying the valuation as live formulas — not pasted numbers. Change an input cell and the model recomputes in the sheet. Inputs blue, computed black, and a provenance tab listing every input with its source and the hash of the report it came from.", TEAL, SELECTED],
    ["Ask, in three tiers", "A question box that answers from the record for nothing, from your stored evidence for pennies, and from fresh research only after telling you the price and waiting for a yes. A question it cannot answer is refused with what it would cost — never guessed.", AMBER, AMBERWASH],
    ["Portability", "Because the arithmetic lives in Python and not in a prompt, the language model is a configured choice. A second provider is a settings change, not a rewrite — which is a hedge against anyone else's pricing, policy or availability.", GREEN, GREENWASH],
  ];
  boxes.forEach((b, i) => {
    const x = M + i * 4.03;
    card(s, { x, y: 2.25, w: 3.77, h: 3.95, fill: { color: b[3] }, line: { color: "FFFFFF", width: 0.75 } });
    s.addShape(pres.ShapeType.rect, { x, y: 2.25, w: 3.77, h: 0.05, fill: { color: b[2] } });
    s.addText(b[0], { x: x + 0.32, y: 2.58, w: 3.15, h: 0.85, fontFace: SERIF, fontSize: 21, bold: true,
      color: b[2], isTextBox: true, margin: 0, valign: "top", lineSpacing: 25 });
    s.addText(b[1], { x: x + 0.32, y: 3.54, w: 3.15, h: 2.5, fontFace: SANS, fontSize: 12, color: INK,
      lineSpacing: 17, isTextBox: true, margin: 0, valign: "top" });
  });

  tag(s, "PLANNED", 11.58, 0.46, AMBER, 1.0);
  cite(s, "The workbook's one honest limit, printed on its own provenance tab: change a figure in Excel and the chain is broken — it is your model then, not the platform's record.");
  s.addNotes("The workbook is also a quiet distribution channel: somebody emails the model to a colleague and the provenance tab travels with it.");
}

/* ------------------------------------------------------------ 11. Not doing */
{
  const s = dark();
  eyebrow(s, "AND WHAT WE ARE DELIBERATELY NOT DOING", AMBERUP);
  heading(s, "The things we are happy to lose", WHITE);
  note(s, "Every one of these is a decision written down with its reason, not an omission we have not got to yet.",
    { x: M, y: 1.58, w: 11.0, h: 0.4, color: TEALUP });

  const nots = [
    ["Speed", "A chat answers in ninety seconds. We take an afternoon. Conceded on purpose — if you need a view before the open, use the chat."],
    ["Open-ended conversation", "You cannot ask this anything. You can ask it about the record it holds, and it will tell you when the answer is not in there."],
    ["A screener", "It will not find you ideas. It goes deep on one company at a time, which is the opposite discipline."],
    ["Importing your broker", "Manual entry only. A holdings feed is a plumbing project, and it is not where the value is."],
    ["A second user", "Deferred, deliberately. Accounts and sharing are designed and argued — and waiting on a legal read before anything is built."],
  ];
  nots.forEach((n, i) => {
    const y = 2.3 + i * 0.88;
    s.addShape(pres.ShapeType.roundRect, { x: M, y, w: FULL, h: 0.76, rectRadius: 0.05, fill: { color: PANEL } });
    s.addText(n[0], { x: M + 0.35, y: y + 0.14, w: 2.6, h: 0.4, fontFace: SERIF, fontSize: 16, bold: true,
      color: AMBERUP, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(n[1], { x: M + 3.05, y: y + 0.1, w: 8.5, h: 0.6, fontFace: SANS, fontSize: 12,
      color: "C7DADE", lineSpacing: 16, isTextBox: true, margin: 0, valign: "middle" });
  });

  cite(s, "A tool that is honest about what it is bad at is easier to trust about what it is good at.", "6E8A92");
  s.addNotes("Fans respond well to this slide. It is the credibility slide.");
}

/* --------------------------------------------------- 12. How we will know */
{
  const s = light();
  eyebrow(s, "HOW WE WILL KNOW IT WORKED", PLUM);
  heading(s, "We wrote down what would make us stop", INK);
  note(s, "The audit judged the platform against a Claude console note, blind, with a fixed rubric. It lost nine of nine. The same instrument will be run again — and the result that would end this work is written down before the work starts.",
    { x: M, y: 1.58, w: 11.4, h: 0.75 });

  const gates = [
    ["The bar", "At least three of six comparisons no longer choose the console, and at least two choose the platform — with a fresh console baseline in the set, because the comparator improves for free while we build.", TEAL, SELECTED],
    ["The other bar", "Zero stopped states without a labelled way forward. Zero identifiers and zero shell commands in anything you read. Three of three runs reaching an approved report with no terminal and no rescue.", GREEN, GREENWASH],
    ["The stop", "If nothing moves and no judge's stated reason changes category, the work stops and the claim narrows to what is already true: an evidence base and a checking instrument. A gate that cannot fail is not a gate.", PLUM, PLUMWASH],
  ];
  gates.forEach((g, i) => {
    const y = 2.55 + i * 1.38;
    card(s, { x: M, y, w: FULL, h: 1.22, fill: { color: g[3] }, line: { color: "FFFFFF", width: 0.75 } });
    s.addShape(pres.ShapeType.rect, { x: M, y, w: 0.045, h: 1.22, fill: { color: g[2] } });
    s.addText(g[0], { x: M + 0.35, y: y + 0.2, w: 2.4, h: 0.34, fontFace: SANS, fontSize: 11, bold: true,
      charSpacing: 1.6, color: g[2], isTextBox: true, margin: 0, valign: "top" });
    s.addText(g[1], { x: M + 0.35, y: y + 0.56, w: 11.0, h: 0.6, fontFace: SANS, fontSize: 12.5, color: INK,
      lineSpacing: 17, isTextBox: true, margin: 0, valign: "top" });
  });

  cite(s, "The judging panel becomes code before it is used again — the same rubric, blinded, seeded, with the judges' identity-guess rate recorded so we know the blinding held");
  s.addNotes("This is the slide that separates this from a pitch. We pre-register the result that would make us abandon the plan.");
}

/* ---------------------------------------------------------- 13. Shape of work */
{
  const s = light();
  eyebrow(s, "THE SHAPE OF THE WORK", TEAL);
  heading(s, "Priced, sequenced and gated", INK);

  const nums = [
    ["18", "features", "each with what it touches and how we know it is done"],
    ["7", "phases", "in dependency order, with a kill gate at phase five"],
    ["8", "decision records", "written before the code, not after it"],
    ["£64", "of live spend", "the whole plan, end to end, measured against a £100 ceiling"],
  ];
  nums.forEach((n, i) => {
    const x = M + i * 2.95;
    card(s, { x, y: 1.8, w: 2.72, h: 2.0 });
    s.addText(n[0], { x: x + 0.22, y: 2.0, w: 2.28, h: 0.7, fontFace: SERIF, fontSize: 38, bold: true,
      color: TEAL, isTextBox: true, margin: 0, valign: "top" });
    s.addText(n[1], { x: x + 0.22, y: 2.72, w: 2.28, h: 0.3, fontFace: SANS, fontSize: 11.5, bold: true,
      color: INK, isTextBox: true, margin: 0, valign: "top" });
    s.addText(n[2], { x: x + 0.22, y: 3.04, w: 2.28, h: 0.72, fontFace: SANS, fontSize: 10.5, color: MUTE,
      lineSpacing: 13.5, isTextBox: true, margin: 0, valign: "top" });
  });

  card(s, { x: M, y: 4.1, w: FULL, h: 2.4 });
  s.addText("The order, and why it is not negotiable", { x: M + 0.35, y: 4.32, w: 11.0, h: 0.34,
    fontFace: SANS, fontSize: 11, bold: true, charSpacing: 1.6, color: MUTE, isTextBox: true, margin: 0, valign: "top" });

  const steps = ["Delete what never fired", "Make it usable", "A bank can be researched", "Print what exists", "Measure — and be willing to stop", "The view, the argument, the export", "The verdict round"];
  steps.forEach((st, i) => {
    const x = M + 0.35 + i * 1.585;
    const colour = i === 4 ? PLUM : (i === 6 ? GREEN : TEAL);
    s.addShape(pres.ShapeType.ellipse, { x, y: 4.82, w: 0.34, h: 0.34, fill: { color: colour } });
    s.addText(String(i + 1), { x, y: 4.82, w: 0.34, h: 0.34, fontFace: SANS, fontSize: 11, bold: true,
      color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    if (i < steps.length - 1) {
      s.addShape(pres.ShapeType.rect, { x: x + 0.38, y: 4.98, w: 1.16, h: 0.02, fill: { color: LINE } });
    }
    s.addText(st, { x: x - 0.16, y: 5.3, w: 1.55, h: 0.8, fontFace: SANS, fontSize: 10, color: MUTE,
      lineSpacing: 13, isTextBox: true, margin: 0, valign: "top" });
  });
  s.addText("Phase five is the kill gate. Nothing after it is built until the measurement says the thesis holds.",
    { x: M + 0.35, y: 6.12, w: 11.0, h: 0.28, fontFace: SANS, fontSize: 11, italic: true, color: PLUM,
      isTextBox: true, margin: 0, valign: "top" });

  cite(s, "Full plan: docs/V1.0_Alpha/05-delivery-plan.md · decisions: docs/adr/0113–0120");
  s.addNotes("£64 of live spend for the whole plan. The point is that the work is priced and gated, not open-ended.");
}

/* ----------------------------------------------------------------- 14. Close */
{
  const s = dark();
  s.addShape(pres.ShapeType.ellipse, { x: -2.2, y: 3.2, w: 7.4, h: 7.4, fill: { color: PANEL } });

  s.addText("WHAT V1.0 IS FOR", { x: 4.6, y: 1.75, w: 8.0, h: 0.34, fontFace: SANS, fontSize: 12, bold: true,
    charSpacing: 3, color: TEALUP, isTextBox: true, margin: 0, valign: "top" });
  s.addText("A research note is read once.\nA research record is read again\nevery time you doubt yourself.",
    { x: 4.6, y: 2.3, w: 8.0, h: 2.2, fontFace: SERIF, fontSize: 30, bold: true, color: WHITE,
      isTextBox: true, margin: 0, valign: "top", lineSpacing: 42 });
  s.addText("V1.0 is the version where the record starts reading you back.",
    { x: 4.6, y: 5.0, w: 8.0, h: 0.8, fontFace: SANS, fontSize: 15, color: TEALUP, isTextBox: true,
      margin: 0, valign: "top", lineSpacing: 22 });

  cite(s, "Not investment advice. A personal research tool, and every surface says so.", "6E8A92");
  s.addNotes("Close on the distinction the whole product rests on: a note versus a record.");
}

const out = process.argv[2] || "whats-next-v1-alpha.pptx";
pres.writeFile({ fileName: out }).then(() => console.log("wrote " + out));
