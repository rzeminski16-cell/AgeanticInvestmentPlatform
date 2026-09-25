/*
 * The angel pitch deck: the whole case for taking the platform into a business, written to be
 * cut down rather than padded out. The founder deletes what a given investor does not need.
 *
 * Every slide carries a provenance tag, as every figure in the product does:
 *   MEASURED             read from the readiness audit, a measurement round or the repository
 *   READY · PER PLAN     in ROADMAP.md or the V1.0 plan, and shown as delivered at the founder's
 *                        instruction; each must be confirmed as shipped before the deck is sent
 *   EXTERNAL · VERIFY    a third party's price, round or feature, read on 25 September 2026
 *   OUR JUDGEMENT        an argument, not a measurement
 *   PLACEHOLDER          the founder's to fill: market, pricing, go-to-market, future, team, ask
 *   DESIGN ILLUSTRATION  a V1.0 artboard; every company, name and figure in it is invented
 *
 * pitch-sources.md maps every figure to where it was read. Nothing about the future is
 * asserted: where the record is silent the slide carries a placeholder, not a guess.
 *
 * Rebuild:  node build_pitch_deck.js tracework-angel-pitch.pptx   (needs pptxgenjs)
 * The screens in screens/ are committed; render_screens.js regenerates them.
 */

const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const JSZip = require("jszip");

const BRAND = "Tracework Invest";
const SCREENS = path.join(__dirname, "screens");
const OUT = process.argv[2] || path.join(__dirname, "tracework-angel-pitch.pptx");

/* Tracework tokens (docs/design-system.md §2). Content slides use the light theme; a dark
   slide uses the dark-theme value of the same token, never a colour invented for slides. */
const C = {
  deep: "07171D", panel: "0C222B", raised: "102B35", selDark: "12343D",
  canvas: "F4F7F8", white: "FFFFFF", sunken: "EAF0F1", selected: "E2F3F4",
  ink: "15252E", mute: "52656E", subtle: "5B6D75", line: "CDD8DC", lineStrong: "9AAEB5",
  teal: "0F6673", tealWash: "E2F3F4", tealUp: "B5ECF0",
  amber: "7A4B00", amberStrong: "5D3900", amberWash: "FFF3D6", amberUp: "FFD27A",
  green: "14613F", greenWash: "E3F4EA",
  plum: "6B3F60", plumWash: "F6EAF2",
  crimson: "9B293F", crimWash: "FBEAED",
  inkDark: "EDF6F7", muteDark: "BBCACE", subtleDark: "9AADB3", lineDark: "2B414A",
};

/* Chart marks only. The interface's teal and amber read grey as a filled bar, so these are the
   same two hues stepped up until the pair passes the chart checks (chroma floor, colour-vision
   separation, contrast against white). Text never takes them. */
const MARK = { ours: "00879B", theirs: "B86E00" };

const HEAD = "Calibri";
const BODY = "Calibri";
const MONO = "Consolas";
const W = 13.333;
const M = 0.6;
const CW = W - 2 * M;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "[Founder]";
pres.company = BRAND;
pres.title = `${BRAND} — angel pitch`;
pres.subject = "Research you can check. Reasons you can keep.";

let page = 0;

/* ------------------------------------------------------------------------------ helpers */

// Typographic quotes, so a straight one typed in this file never reaches a slide: a quote that
// opens a word turns left, and every other one is an apostrophe.
const q = (t) => (typeof t === "string" ? t.replace(/(^|[\s(\[“])'/g, "$1‘").replace(/'/g, "’") : t);
const runs = (r) =>
  Array.isArray(r) ? r.map((x) => (typeof x === "string" ? { text: q(x) } : { text: q(x.text), options: x.options })) : q(r);

function T(s, text, o) {
  s.addText(runs(text), Object.assign({ fontFace: BODY, fontSize: 12, color: C.ink, isTextBox: true,
    margin: 0, valign: "top" }, o));
}

function mono(s, text, o) {
  T(s, text, Object.assign({ fontFace: MONO, fontSize: 9, bold: true, charSpacing: 1, color: C.teal }, o));
}

function box(s, x, y, w, h, o) {
  o = o || {};
  const opts = { x, y, w, h, fill: { color: o.fill || C.white } };
  if (o.line !== null) opts.line = { color: o.line || C.line, width: o.lineW || 0.75 };
  if (o.radius === 0) {
    s.addShape(pres.ShapeType.rect, opts);
  } else {
    s.addShape(pres.ShapeType.roundRect, Object.assign(opts, { rectRadius: o.radius || 0.05 }));
  }
}

function dot(s, x, y, d, o) {
  o = o || {};
  s.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: o.fill || C.white },
    line: { color: o.ring || C.teal, width: o.ringW || 1.5 } });
}

function rule(s, x, y, w, h, o) {
  o = o || {};
  const line = { color: o.color || C.lineStrong, width: o.width || 1 };
  if (o.dash) line.dashType = o.dash;
  if (o.end) line.endArrowType = o.end;
  if (o.begin) line.beginArrowType = o.begin;
  s.addShape(pres.ShapeType.line, { x, y, w, h, line, flipH: !!o.flipH, flipV: !!o.flipV });
}

/* A bulleted list whose items may be [lead, rest]: the lead in bold ink, the rest muted. */
function items(s, list, o) {
  const size = o.fontSize || 11.5;
  const out = [];
  list.forEach((it, i) => {
    const last = i === list.length - 1;
    const para = { paraSpaceAfter: o.gap === undefined ? 7 : o.gap };
    if (o.bullet !== false) para.bullet = { indent: Math.round(size * 1.15) };
    if (Array.isArray(it)) {
      out.push({ text: q(it[0]), options: Object.assign({}, para, { bold: true, color: o.lead || C.ink }) });
      out.push({ text: q(" " + it[1]), options: { color: o.color || C.mute, breakLine: !last } });
    } else {
      out.push({ text: q(it), options: Object.assign({}, para, { color: o.color || C.mute, breakLine: !last }) });
    }
  });
  s.addText(out, { x: o.x, y: o.y, w: o.w, h: o.h, fontFace: BODY, fontSize: size, isTextBox: true,
    margin: 0, valign: "top" });
}

/* Numbered list with the design system's disc as the marker, one text box per item. */
function numbered(s, list, o) {
  const size = o.fontSize || 11;
  list.forEach((t, i) => {
    const y = o.y + i * o.step;
    s.addShape(pres.ShapeType.ellipse, { x: o.x, y: y + 0.01, w: 0.26, h: 0.26, fill: { color: o.disc || C.teal } });
    T(s, String(i + 1), { x: o.x, y: y + 0.01, w: 0.26, h: 0.26, fontFace: MONO, fontSize: 9, bold: true,
      color: C.white, align: "center", valign: "middle" });
    T(s, t, { x: o.x + 0.4, y, w: o.w - 0.4, h: o.step - 0.04, fontSize: size, color: o.color || C.ink });
  });
}

const TAG = {
  measured: { label: "MEASURED", fill: C.teal },
  ready: { label: "READY · PER PLAN", fill: C.green },
  placeholder: { label: "PLACEHOLDER", fill: C.amber },
  external: { label: "EXTERNAL · VERIFY", line: C.mute },
  judgement: { label: "OUR JUDGEMENT", line: C.ink },
  design: { label: "DESIGN ILLUSTRATION", line: C.teal },
};
const tagWidth = (key) => TAG[key].label.length * 0.074 + 0.3;

function drawTag(s, key, x, y, dark) {
  const t = TAG[key];
  const w = tagWidth(key);
  if (t.fill) {
    s.addShape(pres.ShapeType.roundRect, { x, y, w, h: 0.24, rectRadius: 0.12, fill: { color: t.fill } });
  } else {
    s.addShape(pres.ShapeType.roundRect, { x, y, w, h: 0.24, rectRadius: 0.12,
      fill: { color: dark ? C.deep : C.canvas }, line: { color: dark ? C.muteDark : t.line, width: 1 } });
  }
  T(s, t.label, { x, y, w, h: 0.24, fontFace: MONO, fontSize: 7.5, bold: true, charSpacing: 0.8,
    color: t.fill ? C.white : (dark ? C.muteDark : t.line), align: "center", valign: "middle" });
  return w;
}

function tagRow(s, keys, dark) {
  let x = W - M;
  for (let i = keys.length - 1; i >= 0; i--) {
    x -= tagWidth(keys[i]);
    drawTag(s, keys[i], x, 0.42, dark);
    x -= 0.1;
  }
}

/* A placeholder is a thing the founder must fill, so it looks like the product's decision
   panel: amber, and never mistakable for a measured figure. */
function ph(s, x, y, w, h, text, o) {
  o = o || {};
  box(s, x, y, w, h, { fill: C.amberWash, line: C.amber, radius: 0.05 });
  const pad = o.pad || 0.14;
  const label = o.label === undefined ? "PLACEHOLDER" : o.label;
  let top = y + pad;
  if (label) {
    mono(s, label, { x: x + pad, y: top, w: w - 2 * pad, h: 0.2, fontSize: 8, color: C.amber });
    top += 0.25;
  }
  T(s, text, { x: x + pad, y: top, w: w - 2 * pad, h: y + h - top - pad * 0.6, fontSize: o.fontSize || 11,
    color: C.amberStrong, align: o.align || "left" });
}

function stat(s, x, y, w, h, big, label, o) {
  o = o || {};
  box(s, x, y, w, h, { fill: o.fill || C.white, line: o.line || C.line });
  T(s, big, { x: x + 0.2, y: y + 0.16, w: w - 0.4, h: 0.5, fontFace: MONO, fontSize: o.bigSize || 24,
    bold: true, color: o.bigColor || C.teal });
  T(s, label, { x: x + 0.2, y: y + 0.72, w: w - 0.4, h: h - 0.82, fontSize: o.fontSize || 10.5,
    color: o.labelColor || C.mute });
}

function shot(s, file, x, y, w, h, caption) {
  box(s, x - 0.03, y - 0.03, w + 0.06, h + 0.06, { fill: C.white, line: C.lineStrong, radius: 0 });
  s.addImage({ path: path.join(SCREENS, file), x, y, w, h });
  if (caption) {
    T(s, caption, { x, y: y + h + 0.1, w, h: 0.26, fontSize: 9, italic: true, color: C.subtle });
  }
}

/* A table whose first column is the row's name. Cells are strings or {text, options}. */
function table(s, header, rows, o) {
  const size = o.fontSize || 10;
  const hdr = header.map((h) => ({ text: q(h), options: { bold: true, fontFace: MONO, fontSize: 8,
    color: C.mute, fill: { color: C.sunken }, valign: "middle" } }));
  const body = rows.map((r) => r.map((c, ci) => {
    const cell = typeof c === "string" ? { text: c } : c;
    return { text: runs(cell.text), options: Object.assign({ fontFace: BODY, fontSize: size,
      color: ci === 0 ? C.ink : C.mute, bold: ci === 0 && o.boldFirst !== false, fill: { color: C.white },
      valign: "top" }, cell.options || {}) };
  }));
  s.addTable([hdr, ...body], { x: o.x, y: o.y, w: o.w, colW: o.colW, rowH: o.rowH,
    border: { type: "solid", pt: 0.75, color: C.line }, margin: [0.05, 0.08, 0.05, 0.08], autoPage: false });
}

function cite(s, text, dark) {
  T(s, text, { x: M, y: 6.8, w: CW, h: 0.26, fontSize: 9, italic: true,
    color: dark ? C.subtleDark : C.subtle });
}

function footer(s, dark) {
  T(s, `${BRAND}  ·  Confidential  ·  Not investment advice`, { x: M, y: 7.12, w: 7, h: 0.2, fontSize: 8,
    color: dark ? C.subtleDark : C.subtle, valign: "middle" });
  T(s, String(page).padStart(2, "0"), { x: W - M - 0.6, y: 7.12, w: 0.6, h: 0.2, fontFace: MONO, fontSize: 8,
    color: dark ? C.subtleDark : C.subtle, align: "right", valign: "middle" });
}

function slide(o) {
  const s = pres.addSlide();
  page += 1;
  const dark = !!o.dark;
  s.background = { color: dark ? C.deep : C.canvas };
  if (o.section) {
    dot(s, M, 0.475, 0.13, { fill: dark ? C.deep : C.canvas, ring: dark ? C.tealUp : C.teal });
    mono(s, o.section, { x: M + 0.24, y: 0.42, w: 8.9, h: 0.24, fontSize: 10, charSpacing: 1.5,
      color: dark ? C.tealUp : C.teal, valign: "middle" });
  }
  if (o.title) {
    T(s, o.title, { x: M, y: 0.76, w: CW, h: 0.55, fontFace: HEAD, fontSize: 28, bold: true,
      color: dark ? C.inkDark : C.ink });
  }
  if (o.sub) {
    T(s, o.sub, { x: M, y: 1.34, w: o.subW || 11.4, h: 0.5, fontSize: 13.5, color: dark ? C.muteDark : C.mute });
  }
  if (o.tags) tagRow(s, o.tags, dark);
  if (o.cite) cite(s, o.cite, dark);
  footer(s, dark);
  if (o.notes) s.addNotes(q(o.notes));
  return s;
}

/* A product slide: what the surface does on the left, the drawn screen on the right. */
function productSlide(o) {
  const s = slide({ section: o.section, title: o.title, sub: o.sub, tags: ["ready", "design"], notes: o.notes });
  items(s, o.items, { x: M, y: 1.75, w: 4.75, h: 4.6, fontSize: 12, gap: 10 });
  shot(s, o.file, 5.83, 1.7, 6.9, 4.6, "Design illustration from the V1.0 specification. Every company, name and figure in it is invented.");
  return s;
}

/* pptxgenjs writes a paragraph-properties element for every run, so a paragraph that mixes a
   bold lead with plain text carries a second <a:pPr> after its first run. The schema allows one,
   first. This keeps the first and drops the rest before the file is written. */
async function save(file) {
  const zip = await JSZip.loadAsync(await pres.write({ outputType: "nodebuffer" }));
  for (const name of Object.keys(zip.files)) {
    if (!/^ppt\/slides\/slide\d+\.xml$/.test(name)) continue;
    const xml = await zip.file(name).async("string");
    const fixed = xml.replace(/<a:p>([\s\S]*?)<\/a:p>/g, (whole, inner) => {
      const kept = inner.replace(/<a:pPr\b[^>]*?(?:\/>|>[\s\S]*?<\/a:pPr>)/g, (m, offset) => (offset === 0 ? m : ""));
      return `<a:p>${kept}</a:p>`;
    });
    zip.file(name, fixed);
  }
  fs.writeFileSync(file, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
}

/* ======================================================================== 00 · READ ME FIRST */
{
  const s = slide({
    section: "00 · READ ME FIRST · DELETE THIS SLIDE BEFORE SENDING",
    title: "How this deck is built, and what is left for you",
    tags: ["placeholder"],
    notes: "For you, not the investor. Every slide says what kind of claim it makes, which is the same " +
      "discipline the product applies to every figure. Work through the checklist, then delete this slide. " +
      "pitch-sources.md, beside this file, maps every figure to the document it was read from.",
  });
  box(s, M, 1.6, 6.1, 5.05);
  mono(s, "THE PROVENANCE TAG ON EVERY SLIDE", { x: M + 0.25, y: 1.8, w: 5.6, h: 0.22 });
  T(s, "Every slide says what kind of claim it makes, as every figure in the product does.",
    { x: M + 0.25, y: 2.1, w: 5.6, h: 0.3, fontSize: 11, color: C.mute });
  const legend = [
    ["measured", "Read from the readiness audit, a measurement round or the repository. Mapped in pitch-sources.md."],
    ["ready", "In the roadmap or the V1.0 plan, shown as delivered at your instruction. Confirm each has shipped."],
    ["external", "A third party's price, round or feature, from public, mostly secondary, sources on 25 Sep 2026."],
    ["judgement", "An argument, not a measurement. Yours to keep, sharpen or cut."],
    ["placeholder", "Yours to fill: market, pricing, go-to-market, the future, team and the ask."],
    ["design", "A V1.0 design artboard. Every company, name and figure in it is invented."],
  ];
  legend.forEach(([key, text], i) => {
    const y = 2.55 + i * 0.66;
    drawTag(s, key, M + 0.25, y + 0.03, false);
    T(s, text, { x: M + 2.15, y, w: 3.75, h: 0.6, fontSize: 10, color: C.ink });
  });

  box(s, 6.95, 1.6, 5.78, 5.05, { fill: C.amberWash, line: C.amber });
  mono(s, "BEFORE IT LEAVES YOUR HANDS", { x: 7.2, y: 1.8, w: 5.3, h: 0.22, color: C.amber });
  numbered(s, [
    "Pick one name. The README says Tracework Invest; the older decks and the licence say Ageantic. This deck and its screens use the README's.",
    "Fill every amber placeholder: search the deck for “PLACEHOLDER” and for “[”.",
    "Confirm every READY item has shipped. The plan's own V1.0 finish line is on the “We sell the evidence” slide.",
    "Re-check each EXTERNAL figure in the week you send the deck. Prices and rounds move.",
    "Have a solicitor read the regulatory slides and every disclaimer before the deck goes anywhere.",
    "Settle the licence and IP position (MIT, marked provisional) before sharing code or a build.",
    "Keep the evidence slides. Diligence will find the losses in the repository; shown first, they read as discipline.",
    "Never quote a figure from a design screen. They are invented.",
  ], { x: 7.2, y: 2.15, w: 5.35, step: 0.54, fontSize: 10, disc: C.amber, color: C.amberStrong });
}

/* ================================================================================ 01 · TITLE */
{
  const s = slide({
    dark: true,
    notes: "Open on the artefact, not the ambition. The two lines are the whole pitch: research whose " +
      "every figure can be checked, and a record of why you bought that the product keeps testing. The three " +
      "figures are measured, from the readiness audit of 11–12 September 2026 — five live reports on Microsoft, " +
      "AstraZeneca and M&T Bank. The panel on the right is the chain every figure sits on.",
  });
  mono(s, "TRACEWORK INVEST  ·  ANGEL ROUND", { x: M, y: 0.7, w: 8, h: 0.3, fontSize: 11, charSpacing: 3,
    color: C.tealUp });
  T(s, [{ text: "Research you can check.", options: { color: C.inkDark, breakLine: true } },
    { text: "Reasons you can keep.", options: { color: C.tealUp } }],
  { x: M, y: 1.15, w: 8.6, h: 1.75, fontFace: HEAD, fontSize: 46, bold: true });
  T(s, "An equity research platform for self-directed investors. Code does every calculation and traces " +
    "every figure to a hashed source document; a language model reads, argues and writes; you approve each " +
    "step. And the record of why you bought is kept, tested against every new filing, and reviewed when you sell.",
  { x: M, y: 3.05, w: 8.1, h: 1.3, fontSize: 14.5, color: C.muteDark });

  [["0 of 779", "checkable figures contradicted by their source"],
    ["259 / 259", "citations confirmed by code re-reading the archived document"],
    ["£7.19", "average model spend for a full 18-section report"]].forEach(([big, label], i) => {
    const x = M + i * 2.72;
    box(s, x, 4.55, 2.55, 1.3, { fill: C.raised, line: C.lineDark });
    T(s, big, { x: x + 0.2, y: 4.68, w: 2.2, h: 0.45, fontFace: MONO, fontSize: 22, bold: true, color: C.tealUp });
    T(s, label, { x: x + 0.2, y: 5.18, w: 2.2, h: 0.6, fontSize: 10, color: C.inkDark });
  });
  T(s, "[PLACEHOLDER: founder name  ·  email  ·  phone  ·  month and year]", { x: M, y: 6.15, w: 8.1, h: 0.3,
    fontFace: MONO, fontSize: 10, color: C.amberUp });
  T(s, "Measured in the readiness audit of 11–12 September 2026: five live reports on three companies.",
    { x: M, y: 6.55, w: 8.1, h: 0.26, fontSize: 9, italic: true, color: C.subtleDark });

  box(s, 9.5, 0.7, 3.23, 6.05, { fill: C.panel, line: C.lineDark });
  mono(s, "ONE FIGURE, SIX LINKS", { x: 9.8, y: 0.95, w: 2.8, h: 0.22, color: C.tealUp });
  const chain = [
    ["The figure", "in a section of the report"],
    ["The claim", "names exactly one fact or calculation"],
    ["The citation", "confirmed by code, never by the model"],
    ["The extraction", "text with locators into the source"],
    ["The artefact", "addressed by its SHA-256 hash"],
    ["The bytes", "archived as fetched, with the date"],
  ];
  rule(s, 9.9, 1.6, 0, 4.55, { color: C.lineDark, width: 1.25 });
  chain.forEach(([name, desc], i) => {
    const y = 1.45 + i * 0.9;
    dot(s, 9.8, y + 0.04, 0.2, { fill: C.panel, ring: C.tealUp, ringW: 2 });
    T(s, name, { x: 10.2, y: y - 0.02, w: 2.4, h: 0.3, fontSize: 12.5, bold: true, color: C.inkDark });
    T(s, desc, { x: 10.2, y: y + 0.28, w: 2.4, h: 0.45, fontSize: 9.5, color: C.muteDark });
  });
}

/* ======================================================================== 02 · IN ONE MINUTE */
{
  const s = slide({
    section: "01 · IN ONE MINUTE",
    title: "The short version",
    tags: ["measured", "ready", "placeholder"],
    notes: "If the investor reads one slide, it is this one. Each card is expanded later in the deck. The " +
      "evidence card is deliberately two-sided: the accuracy figures are strong, and the comparisons judged by language models " +
      "went against us. Saying both here buys the credibility the rest of the deck spends.",
  });
  const cards = [
    ["WHAT IT IS", "An equity research platform for self-directed investors. It writes institutional-style reports on listed companies, and keeps a living record of your theses, decisions and reviews, tested against every new filing."],
    ["THE PROBLEM", "AI made company research fast but not checkable. The tools that make it checkable are priced for institutions. And nothing keeps an investor's own reasoning honest after they buy."],
    ["THE DIFFERENCE", "Code owns every number and every fact; the model plans, reads, argues and writes. Every figure resolves to a formula or to archived, hashed source bytes, and a figure that cannot is refused."],
    ["WHAT IS READY", "Ten tools across research, decide, hold and review: costed plans with approval gates, refresh, Ask, a live workbook, theses, decisions, a monitor, risk, post-trade review and decision analytics."],
    ["THE EVIDENCE", "Audited on live runs: 0 of 779 checkable figures contradicted, 259 of 259 citations confirmed, £7.19 a report. Model judges still preferred a general AI note, so we sell the evidence, not the essay."],
  ];
  const cw = (CW - 2 * 0.25) / 3;
  cards.forEach(([label, text], i) => {
    const x = M + (i % 3) * (cw + 0.25);
    const y = i < 3 ? 1.6 : 4.1;
    box(s, x, y, cw, 2.3);
    mono(s, label, { x: x + 0.22, y: y + 0.2, w: cw - 0.44, h: 0.22 });
    T(s, text, { x: x + 0.22, y: y + 0.52, w: cw - 0.44, h: 1.7, fontSize: 13 });
  });
  ph(s, M + 2 * (cw + 0.25), 4.1, cw, 2.3,
    "THE ASK\n[Amount] · [instrument] · [what it buys] · [by when]", { fontSize: 12 });
}

/* ========================================================================= 03 · PROBLEM, AI */
{
  const s = slide({
    section: "02 · THE PROBLEM",
    title: "AI made company research fast. It did not make it checkable.",
    tags: ["measured"],
    cite: "AI baseline: Claude Opus 5 at high effort with server-side web search, through the API, on the same three briefs as the platform. Readiness audit, 11–12 Sep 2026.",
    notes: "The problem is not that general AI is inaccurate — our own audit found its stated figures " +
      "accurate. The problem is that nobody can tell which figures to trust without redoing the work. The three " +
      "numbers on the right come from the three baseline notes the audit commissioned: a third of their links " +
      "were dead the next day, none carried a citation block, and the cost of the same kind of brief varied by 65%.",
  });
  T(s, "Ask a general-purpose assistant to research a company and you get fluent, plausible prose. Writing a " +
    "paragraph and computing a discounted cash flow are the same operation to it, and only one of them has a " +
    "right answer. Asking it to be careful and cite its sources is a request, not a constraint, and it fails " +
    "quietly on exactly the figures a reader is least able to check.",
  { x: M, y: 1.65, w: 6.0, h: 1.9, fontSize: 14.5, color: C.ink });
  box(s, M, 3.7, 6.0, 1.45, { fill: C.deep, line: null });
  T(s, "“The difference is not accuracy. It is whether anybody can tell.”",
    { x: M + 0.3, y: 3.87, w: 5.4, h: 1.1, fontSize: 21, italic: true, color: C.tealUp, valign: "middle" });
  T(s, "To be fair to them: the baseline notes' stated figures were accurate — 0 contradicted of 154 and of 107, " +
    "and 1 flagged of 179, a definition the note itself disclosed. They are not usually wrong. You cannot tell when they are.",
  { x: M, y: 5.4, w: 6.0, h: 1.2, fontSize: 12, color: C.mute });

  const rows = [
    ["54 of 159", "web links cited by the three AI notes had stopped resolving the day after they were written."],
    ["0 of 3", "notes carried a citation block. The references were the model's own prose."],
    ["65%", "spread in cost across the three briefs, £6.71 to £11.03, with no ceiling built in."],
    ["Nothing", "to re-run. A chat keeps no structured record of how any figure was produced."],
  ];
  rows.forEach(([big, text], i) => {
    const y = 1.65 + i * 1.23;
    box(s, 7.05, y, 5.68, 1.08);
    T(s, big, { x: 7.25, y: y + 0.22, w: 1.95, h: 0.6, fontFace: MONO, fontSize: 20, bold: true, color: C.crimson,
      valign: "middle" });
    T(s, text, { x: 9.25, y: y + 0.17, w: 3.3, h: 0.78, fontSize: 11, color: C.ink, valign: "middle" });
  });
}

/* ================================================================ 04 · PROBLEM, PRICED OUT */
{
  const s = slide({
    section: "02 · THE PROBLEM",
    title: "The tools that make research checkable are priced for institutions",
    tags: ["external", "judgement"],
    cite: "Prices from public, mostly secondary, sources read 25 Sep 2026; AlphaSense is quote-based, shown as a range. Log scale. Verify before use — sources in pitch-sources.md.",
    notes: "The gap is between a twenty-dollar chatbot and a five-figure professional seat. Consumer tools " +
      "give data, ratings and screens; professional tools give depth and source links, at institutional prices. " +
      "The claim in the left card is our judgement from the categories we examined, not a survey of every product — " +
      "check it against anything the investor names. Our own price is a placeholder: the only number we have is " +
      "the measured cost of producing one report.",
  });
  const rows = [
    ["ChatGPT Plus", "General AI assistant", 240, null, "$20 a month", "ours"],
    ["Perplexity Pro", "General AI, with finance pages", 240, null, "$20 a month", "ours"],
    ["Morningstar Investor", "Retail research and ratings", 249, null, "$249 a year", "ours"],
    ["Fiscal.ai Pro", "AI stock research", 468, null, "$39 a month, billed yearly", "ours"],
    ["Koyfin Premium", "Market data and dashboards", 948, null, "$79 a month", "ours"],
    ["AlphaSense", "AI market intelligence", 10000, 40000, "~$10,000–$40,000+ a seat", "theirs"],
    ["Bloomberg Terminal", "The professional terminal", 31980, null, "$31,980 a year", "theirs"],
  ];
  const x0 = 3.35;
  const bw = 6.35;
  const lo = 2;
  const hi = Math.log10(50000);
  const px = (v) => x0 + ((Math.log10(v) - lo) / (hi - lo)) * bw;
  const top = 2.0;
  const rh = 0.46;
  [100, 1000, 10000].forEach((v) => {
    rule(s, px(v), top - 0.1, 0, rows.length * rh + 0.1, { color: C.line, width: 0.75, dash: "dash" });
    T(s, `$${v.toLocaleString("en-GB")} a year`, { x: px(v) - 0.6, y: top + rows.length * rh + 0.04, w: 1.2, h: 0.22,
      fontSize: 8.5, color: C.subtle, align: "center" });
  });
  rows.forEach(([name, kind, v, v2, label, who], i) => {
    const y = top + i * rh;
    T(s, name, { x: M, y: y + 0.02, w: 2.65, h: 0.22, fontSize: 11, bold: true });
    T(s, kind, { x: M, y: y + 0.22, w: 2.65, h: 0.2, fontSize: 8.5, color: C.subtle });
    const colour = MARK[who];
    s.addShape(pres.ShapeType.rect, { x: x0, y: y + 0.1, w: px(v) - x0, h: 0.24, fill: { color: colour } });
    if (v2) {
      s.addShape(pres.ShapeType.rect, { x: px(v), y: y + 0.1, w: px(v2) - px(v), h: 0.24,
        fill: { color: colour, transparency: 60 } });
    }
    T(s, label, { x: 9.85, y: y + 0.1, w: 2.88, h: 0.24, fontFace: MONO, fontSize: 9.5, color: C.ink, valign: "middle" });
  });
  [["Built for individuals", "ours"], ["Built for institutions", "theirs"]].forEach(([label, who], i) => {
    const x = 7.9 + i * 2.45;
    s.addShape(pres.ShapeType.rect, { x, y: 1.62, w: 0.22, h: 0.16, fill: { color: MARK[who] } });
    T(s, label, { x: x + 0.3, y: 1.57, w: 2.1, h: 0.26, fontSize: 9.5, color: C.mute });
  });

  box(s, M, 5.55, 7.3, 1.1);
  T(s, [{ text: "Our reading: ", options: { bold: true, color: C.ink } },
    { text: "between a $20-a-month chatbot and a $32,000-a-year terminal we have not found a product that gives an " +
      "individual research whose every figure, the valuation included, resolves to a formula or a filing.", options: { color: C.mute } }],
  { x: M + 0.2, y: 5.68, w: 6.9, h: 0.9, fontSize: 11 });
  ph(s, 8.1, 5.55, 4.63, 1.1, "Tracework's price: [£ ___ ]. Measured cost of one full report: £7.19 of model spend.",
    { fontSize: 10.5 });
}

/* ============================================================== 05 · PROBLEM, THE REASONING */
{
  const s = slide({
    section: "02 · THE PROBLEM",
    title: "Nobody keeps the record of why you bought",
    tags: ["judgement", "placeholder"],
    notes: "This is the problem the V1.0 product is organised around, in the design documents' own words: are " +
      "the reasons I bought still standing? It is our framing, not a measured market fact — the placeholder at the " +
      "bottom is where evidence from real investors goes. Interviews that quote this pain back to you are worth " +
      "more than any number on this slide.",
  });
  T(s, "“Are the reasons I bought still standing?”", { x: M, y: 1.55, w: CW, h: 0.75, fontFace: HEAD, fontSize: 30,
    italic: true, bold: true, color: C.teal });
  T(s, "A broker shows what you hold and what it is worth, in real time, for nothing. Nothing tells you whether the " +
    "reasoning behind each position still holds, because the reasoning was never written down in a form anything could test.",
  { x: M, y: 2.35, w: 11.2, h: 0.8, fontSize: 13.5, color: C.mute });
  const cw = (CW - 2 * 0.25) / 3;
  [["BEFORE YOU BUY", "The thesis lives in your head, or in a note nothing can read. What would prove it wrong is rarely written down at all."],
    ["WHILE YOU HOLD", "New filings arrive every quarter. Nothing reads them against what you believed, so a broken premise is found late, usually by the price."],
    ["AFTER YOU SELL", "A profitable trade on a broken thesis looks like skill. Without the record, luck and judgement are indistinguishable, and the lesson is lost."],
  ].forEach(([label, text], i) => {
    const x = M + i * (cw + 0.25);
    box(s, x, 3.3, cw, 1.95);
    mono(s, label, { x: x + 0.22, y: 3.5, w: cw - 0.44, h: 0.22 });
    T(s, text, { x: x + 0.22, y: 3.83, w: cw - 0.44, h: 1.35, fontSize: 13.5 });
  });
  ph(s, M, 5.45, CW, 1.2, "Evidence that investors feel this: [interview quotes]  ·  [survey result]  ·  [waitlist or pilot numbers]",
    { fontSize: 12 });
}

/* ============================================================================ 06 · THE LOOP */
{
  const s = slide({
    section: "03 · THE SOLUTION",
    title: "One loop, and every stage leaves a record the next one reads",
    tags: ["ready"],
    notes: "This is the product, not the report. A chat session structurally cannot do this: it does not keep " +
      "what you believed, so it cannot test it. The pass matters most — a record of the companies you declined, and " +
      "why, is the one thing nobody else keeps. Source: docs/V1.0_Alpha/00-the-product.md §1.",
  });
  const stages = [
    ["Research", "Commission a report, read it, ask follow-up questions.", "A cited, immutable document and every fact and calculation beneath it."],
    ["Decide", "Write the thesis as premises with tests; record the decision, or the pass.", "A thesis of testable premises, and a decision with its reasons and size."],
    ["Hold", "Own the position and watch it.", "Holdings valued daily, and a monitor testing each premise against every new filing."],
    ["Review", "Close the position and learn from it.", "Whether the thesis was right, whether the decision was good, and what many decisions say about you."],
  ];
  const cw = (CW - 3 * 0.42) / 4;
  stages.forEach(([name, you, keeps], i) => {
    const x = M + i * (cw + 0.42);
    box(s, x, 1.65, cw, 3.3);
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.22, y: 1.85, w: 0.4, h: 0.4, fill: { color: C.teal } });
    T(s, String(i + 1), { x: x + 0.22, y: 1.85, w: 0.4, h: 0.4, fontFace: MONO, fontSize: 13, bold: true,
      color: C.white, align: "center", valign: "middle" });
    T(s, name, { x: x + 0.75, y: 1.86, w: cw - 0.9, h: 0.4, fontSize: 18, bold: true, color: C.teal, valign: "middle" });
    mono(s, "YOU", { x: x + 0.22, y: 2.48, w: 1, h: 0.2, fontSize: 8, color: C.mute });
    T(s, you, { x: x + 0.22, y: 2.72, w: cw - 0.44, h: 0.8, fontSize: 11 });
    mono(s, "IT KEEPS", { x: x + 0.22, y: 3.55, w: 1.5, h: 0.2, fontSize: 8, color: C.mute });
    T(s, keeps, { x: x + 0.22, y: 3.79, w: cw - 0.44, h: 1.1, fontSize: 11 });
    if (i < 3) rule(s, x + cw + 0.06, 3.3, 0.3, 0, { color: C.teal, width: 1.75, end: "triangle" });
  });
  const xl = M + cw / 2;
  const xr = M + 3 * (cw + 0.42) + cw / 2;
  rule(s, xr, 4.95, 0, 0.35, { color: C.teal, width: 1.5 });
  rule(s, xl, 5.3, xr - xl, 0, { color: C.teal, width: 1.5 });
  rule(s, xl, 4.98, 0, 0.32, { color: C.teal, width: 1.5, flipV: true, end: "triangle" });
  T(s, "each stage reads the record the last one wrote", { x: xl + 2.5, y: 5.05, w: 4.2, h: 0.22, fontSize: 9.5,
    italic: true, color: C.teal, align: "center" });
  box(s, M, 5.6, CW, 1.05, { fill: C.tealWash, line: C.teal });
  T(s, [{ text: "A decision not to act is a first-class decision. ", options: { bold: true, color: C.ink } },
    { text: "“You passed at $80 because Y, and Y is now false” is the most valuable thing the system can tell you, " +
      "and no chat can say it, because no chat remembers what you believed.", options: { color: C.ink } }],
  { x: M + 0.25, y: 5.72, w: CW - 0.5, h: 0.85, fontSize: 12.5, valign: "middle" });
}

/* ============================================================================ 07 · THE RULE */
{
  const s = slide({
    section: "03 · THE SOLUTION",
    title: "The rule everything else follows from",
    tags: ["ready"],
    notes: "If they remember one thing technical, it is this split. It is the repository's first architectural " +
      "decision (ADR 0003), and it is enforced in code rather than requested in a prompt: a figure that is not a stored " +
      "fact, a recorded calculation or the operator's own attested holding is refused before it reaches a page.",
  });
  box(s, M, 1.6, CW, 1.3, { fill: C.deep, line: null });
  T(s, [{ text: "Deterministic code owns ", options: { color: C.inkDark } },
    { text: "every number and every fact", options: { color: C.tealUp, bold: true } },
    { text: ". The language model owns planning, interpretation, argument and writing.", options: { color: C.inkDark } }],
  { x: M + 0.35, y: 1.72, w: CW - 0.7, h: 1.06, fontSize: 21, valign: "middle" });
  const cw = (CW - 0.3) / 2;
  [["CODE DOES THIS", [
    "Fetching, hashing, archiving and parsing every source",
    "All arithmetic: ratios, growth, cost of capital, discounted cash flow, comparables, scenarios",
    "Units and currencies. A mismatch raises; it never coerces",
    "Deciding which filing's word stands for each period",
    "Resolving and verifying every citation",
    "Storage, rendering and metering every penny spent",
  ]], ["THE MODEL DOES THIS", [
    "Deciding what is worth researching",
    "Proposing an assumption, with its justification",
    "Judging whether a source is relevant",
    "Writing a section from facts it was handed",
    "Arguing the other side of your view",
    "Plain-English prose",
  ]]].forEach(([label, list], i) => {
    const x = M + i * (cw + 0.3);
    box(s, x, 3.15, cw, 2.75);
    mono(s, label, { x: x + 0.25, y: 3.33, w: cw - 0.5, h: 0.22, color: i === 0 ? C.teal : C.plum });
    items(s, list, { x: x + 0.25, y: 3.68, w: cw - 0.5, h: 2.15, fontSize: 12, gap: 5, color: C.ink });
  });
  T(s, "A discounted cash flow here is forty lines of tested Python, not a reasoning task. The model is never asked " +
    "for a number, and a figure with no chain behind it cannot reach a page.",
  { x: M, y: 6.1, w: CW, h: 0.55, fontSize: 12, color: C.mute });
}

/* ====================================================================== 08 · HOW A RUN WORKS */
{
  const s = slide({
    section: "03 · THE SOLUTION",
    title: "A report in five stages, and you hold the gates",
    tags: ["ready", "measured"],
    cite: "Readiness audit, 11–12 Sep 2026, five full runs. Machine time as corrected in the V1.0 delivery plan (the audit's step times summed a parallel wave).",
    notes: "The gates are the product, not friction: nothing is spent until the costed plan is approved, and " +
      "nothing is published until the draft is. A run stops for six decisions on an ordinary company and seven on a " +
      "bank. The largest single cost is drafting. All figures in the bottom row are measured.",
  });
  const stages = [
    ["Plan", "Proposes the sections, sources, cost in pounds, time and known risks. A critic attacks the plan first. Nothing is spent until you approve."],
    ["Acquire", "Fetches filings, prices and official statistics through one guarded door. Every byte is hashed and archived."],
    ["Compute", "Statements, ratios, earnings quality, cost of capital, DCF, bank models, comparables and scenarios, all traced."],
    ["Write", "The model drafts each section from structured facts; an adversary argues the other side; code checks every citation and figure."],
    ["Approve", "You approve the draft. It is frozen and hashed, as Markdown, HTML and PDF, with a live model workbook beside it."],
  ];
  const cw = (CW - 4 * 0.2) / 5;
  rule(s, M + cw / 2, 2.2, 4 * (cw + 0.2), 0, { color: C.teal, width: 1.5 });
  stages.forEach(([name, text], i) => {
    const x = M + i * (cw + 0.2);
    const cx = x + cw / 2 - 0.24;
    s.addShape(pres.ShapeType.ellipse, { x: cx, y: 1.96, w: 0.48, h: 0.48, fill: { color: C.teal } });
    T(s, String(i + 1), { x: cx, y: 1.96, w: 0.48, h: 0.48, fontFace: MONO, fontSize: 14, bold: true,
      color: C.white, align: "center", valign: "middle" });
    if (i === 0 || i === 4) {
      s.addShape(pres.ShapeType.roundRect, { x: x + cw / 2 - 0.62, y: 1.55, w: 1.24, h: 0.26, rectRadius: 0.12,
        fill: { color: C.amber } });
      mono(s, "YOU APPROVE", { x: x + cw / 2 - 0.62, y: 1.55, w: 1.24, h: 0.26, fontSize: 7.5, color: C.white,
        align: "center", valign: "middle", charSpacing: 0.8 });
    }
    T(s, name, { x, y: 2.6, w: cw, h: 0.35, fontSize: 15, bold: true, align: "center" });
    T(s, text, { x: x + 0.05, y: 3.0, w: cw - 0.1, h: 1.5, fontSize: 11, color: C.mute, align: "center" });
  });
  T(s, "Between them, further gates: the sector, the peer set, themes, unmapped accounting concepts and the valuation assumptions.",
    { x: M, y: 4.45, w: CW, h: 0.3, fontSize: 11, italic: true, color: C.amber, align: "center" });
  const tiles = [
    ["£7.19", "average model spend per full report; five runs from £6.80 to £7.61"],
    ["26.5–36.8 min", "machine time per run, plus your decisions at the gates"],
    ["6 gates", "for an ordinary company, 7 for a bank; each one a labelled decision"],
    ["18 sections", "and 114–148 footnotes per report, 12,500 to 15,900 words"],
  ];
  const tw = (CW - 3 * 0.2) / 4;
  tiles.forEach(([big, label], i) => stat(s, M + i * (tw + 0.2), 4.95, tw, 1.6, big, label, { bigSize: 20, fontSize: 11 }));
}

/* ==================================================================== 09 · THE EVIDENCE CHAIN */
{
  const s = slide({
    section: "03 · THE SOLUTION",
    title: "Click any number. Walk it back to the filing.",
    tags: ["ready", "design"],
    notes: "This is the property that makes it different, and the one a chat product cannot retrofit: every " +
      "figure sits at the top of an unbroken chain down to archived bytes. In the interface the chain is a link; in " +
      "an exported file it is a quotation — the verified passage printed with its retrieval date and digest, so a " +
      "file that has left the machine is still checkable. The screen is an illustration; its figures are invented.",
  });
  const chain = [
    ["The figure", "in a section of the report"],
    ["The claim", "names exactly one fact or calculation"],
    ["The citation", "confirmed by code re-reading the document, never by the model"],
    ["The extraction", "text with locators into the original"],
    ["The artefact", "content-addressed by its SHA-256 hash"],
    ["The archived bytes", "exactly as fetched, with the retrieval date"],
  ];
  rule(s, M + 0.12, 1.85, 0, 3.3, { color: C.teal, width: 1.25 });
  chain.forEach(([name, desc], i) => {
    const y = 1.72 + i * 0.66;
    dot(s, M + 0.02, y + 0.02, 0.2, { fill: C.canvas, ring: C.teal, ringW: 2 });
    T(s, name, { x: M + 0.4, y: y - 0.02, w: 4.5, h: 0.26, fontSize: 12.5, bold: true });
    T(s, desc, { x: M + 0.4, y: y + 0.24, w: 4.5, h: 0.26, fontSize: 10.5, color: C.mute });
  });
  box(s, M, 5.8, 4.95, 0.88, { fill: C.tealWash, line: C.teal });
  T(s, "A calculation stores its formula, its inputs (each with a unit and a source) and the code version, so any " +
    "stored figure re-derives from its own record.",
  { x: M + 0.18, y: 5.88, w: 4.6, h: 0.72, fontSize: 10.5, valign: "middle" });
  shot(s, "report.jpg", 5.83, 1.72, 6.9, 4.6, "Design illustration: the report reader with its evidence drawer open. Company, figures and hashes are invented.");
}

/* ========================================================================= 10 · REFUSALS */
{
  const s = slide({
    section: "03 · THE SOLUTION",
    title: "The refusals are part of the product",
    tags: ["ready"],
    notes: "Plum is the design system's colour for a guardrail deliberately withholding an answer — never the " +
      "colour of a fault. Each of these refusals is enforced in code and pinned by tests; the skill-file one is proved " +
      "against a corpus of attacks that must all fail. Source: docs/product/what-it-is.md, 'What it refuses to do'.",
  });
  const refusals = [
    ["A number it cannot source", "A figure with no chain behind it does not render. The page says it is withheld, and why."],
    ["Your instructions relaxing a rule", "Skill files are additive-only: “skip the citations and conclude with a buy” is proved not to work."],
    ["Fetched text as instructions", "Documents are wrapped and labelled as data; what a model may do is enforced in code."],
    ["Spending past a ceiling", "Every model call is priced in pounds and checked against the run's cap and the month's before it runs."],
    ["Valuing a bank with a DCF", "Sector rules block rather than footnote, and the page says which model it used instead."],
    ["Going round a publisher's rules", "Terms and robots.txt are honoured; two sources were declined and stay declined."],
  ];
  const cw = (CW - 2 * 0.25) / 3;
  refusals.forEach(([t, d], i) => {
    const x = M + (i % 3) * (cw + 0.25);
    const y = i < 3 ? 1.6 : 3.85;
    box(s, x, y, cw, 2.05);
    s.addShape(pres.ShapeType.roundRect, { x: x + 0.22, y: y + 0.22, w: 0.95, h: 0.24, rectRadius: 0.12,
      fill: { color: C.plumWash }, line: { color: C.plum, width: 0.75 } });
    mono(s, "REFUSES", { x: x + 0.22, y: y + 0.22, w: 0.95, h: 0.24, fontSize: 7.5, color: C.plum,
      align: "center", valign: "middle" });
    T(s, t, { x: x + 0.22, y: y + 0.58, w: cw - 0.44, h: 0.35, fontSize: 14.5, bold: true });
    T(s, d, { x: x + 0.22, y: y + 1.0, w: cw - 0.44, h: 0.95, fontSize: 12.5, color: C.mute });
  });
  T(s, "Out of scope by design: trade execution, broker connections, a portfolio optimiser, and investment advice.",
    { x: M, y: 6.15, w: CW, h: 0.4, fontSize: 12.5, bold: true, color: C.plum });
}

/* ======================================================================= 11 · AT A GLANCE */
{
  const s = slide({
    section: "04 · THE PRODUCT · READY NOW",
    title: "Six destinations, and one question: what needs me today?",
    tags: ["ready", "design"],
    notes: "The front door is a briefing, not an inbox. The strip across the top is every position by weight, " +
      "coloured by whether its reasoning still holds — the two-second read is how much of the book still has a reason " +
      "behind it. Stages organise the menu; the portfolio is where the user lives. Everything on the screen is invented.",
  });
  shot(s, "today.jpg", M, 1.65, 7.4, 4.625, "Design illustration from the V1.0 specification. Every company, name and figure is invented.");
  const dests = [
    ["Today", "what needs you, and what is worth doing next"],
    ["Portfolio", "the book as a validity dashboard: conviction before profit"],
    ["Companies", "everything the account knows, and why each is there"],
    ["Research", "requests, live runs, reports, your methods, knowledge"],
    ["Review", "closed positions, and what many decisions say about you"],
    ["Platform", "costs, health, backups and settings"],
  ];
  dests.forEach(([name, desc], i) => {
    const y = 1.65 + i * 0.72;
    s.addShape(pres.ShapeType.ellipse, { x: 8.4, y: y + 0.04, w: 0.3, h: 0.3, fill: { color: C.teal } });
    T(s, String(i + 1), { x: 8.4, y: y + 0.04, w: 0.3, h: 0.3, fontFace: MONO, fontSize: 9.5, bold: true,
      color: C.white, align: "center", valign: "middle" });
    T(s, name, { x: 8.85, y, w: 3.88, h: 0.28, fontSize: 13, bold: true });
    T(s, desc, { x: 8.85, y: y + 0.29, w: 3.88, h: 0.38, fontSize: 10.5, color: C.mute });
  });
  box(s, 8.4, 6.02, 4.33, 0.62, { fill: C.tealWash, line: C.teal });
  T(s, "A command bar jumps to any company or starts a request from anywhere.",
    { x: 8.55, y: 6.07, w: 4.05, h: 0.52, fontSize: 10.5, valign: "middle" });
}

/* ====================================================================== 12 · RESEARCH */
productSlide({
  section: "04 · THE PRODUCT · RESEARCH",
  title: "Research: a costed plan before anything is spent",
  file: "request.jpg",
  items: [
    ["A costed plan first.", "Sections, sources, cost, machine time and your attention, stated before a penny is spent."],
    ["Standard or Quick.", "18 sections with full valuation, comparables and scenarios, or 9 sections, measured at £4.95."],
    ["One queue of gates.", "Plan, sector, peers, themes, unmapped concepts, assumptions, the final draft."],
    ["Your context in the brief.", "Planned weight, horizon and purpose feed a closing section on what the position does to your book."],
    ["Your own methods.", "Report sections written in plain language, additive-only and attack-tested."],
    ["Depth in primary sources.", "Exhibits, prepared remarks furnished on 8-Ks and rivals' filings, not licensed commentary."],
  ],
  notes: "Commissioning without documentation is one of the V1.0 bars. The Quick figure is a single measured run " +
    "(£4.95, 31 minutes). The closing section computes consequences over the operator's own book and never issues " +
    "instructions — which matters for the regulatory slides later. The screen's prices are invented.",
});

/* ========================================================================= 13 · ASK */
productSlide({
  section: "04 · THE PRODUCT · RESEARCH",
  title: "Ask, refresh, workbook: the record keeps working",
  file: "ask.jpg",
  items: [
    ["Ask, in three tiers.", "Recompute the stored model (free, instant), re-read what is archived (pennies), or research something new, priced and approved first."],
    ["Refresh.", "Reads only what is new, recomputes every figure, re-drafts only what moved and leads with the change. Measured once at £1.66; nothing is spent when nothing is new."],
    ["A live workbook.", "The valuation as real spreadsheet formulas, with a provenance tab that travels with the file."],
    ["A calculator page.", "Re-run the discounted cash flow by hand on the stored inputs."],
    ["One current report per company.", "A refresh supersedes the old report, which stays readable."],
  ],
  notes: "Ask never guesses: a question outside the record is refused with a price. The third tier leaves the record " +
    "larger than it found it. The refresh figure is one measured run from the verdict round (£1.66); that run also lost " +
    "its valuation, which the plan fixes and re-measures. The workbook is also a quiet distribution channel: the " +
    "provenance tab travels when an analyst emails the model to a colleague.",
});

/* ======================================================================== 14 · THESIS */
productSlide({
  section: "04 · THE PRODUCT · DECIDE",
  title: "Decide: what you believe, and what would defeat it",
  file: "thesis.jpg",
  items: [
    ["A thesis is a set of premises.", "Each is a sentence you would defend."],
    ["Each premise names its test.", "A metric and a threshold that would defeat it, or, where nothing can test it, a date to look again."],
    ["The monitor runs the tests.", "Against every filing since the premise was last read. A metric check costs nothing in model spend."],
    ["Revised, never erased.", "A withdrawn premise stays on the record; the history reads as a story."],
    ["Your view, argued against.", "The platform states no view of its own. Its adversary argues the opposite of yours."],
  ],
  notes: "The thesis is the load-bearing idea of the product: without it there is nothing to monitor, validate or " +
    "review. After the verdict round the platform stopped stating a view of its own; the view is the operator's and the " +
    "adversary argues against it (F2 and F13's authored half, both in the V1.0 plan).",
});

/* ====================================================================== 15 · DECISION */
productSlide({
  section: "04 · THE PRODUCT · DECIDE",
  title: "Record the decision, including the decision not to act",
  file: "decision.jpg",
  items: [
    ["Add, open, trim, exit, or pass.", "A pass is recorded the same way as a trade. It is the record nobody else keeps."],
    ["Linked, or not saved.", "Every decision points at the thesis it expresses and the report it rests on."],
    ["The pre-trade check.", "What the size does to concentration, sector exposure and your stated horizon, before you record it."],
    ["It never blocks.", "Your book is your own. The check makes sure you knew."],
    ["What would make it wrong.", "Anything written here becomes a testable premise on the thesis."],
  ],
  notes: "The pre-trade check and the risk page share one implementation, so they can never disagree. The check " +
    "computes consequences rather than issuing instructions — a tool that refuses a trade has confused advice with " +
    "control. The screen's figures are invented.",
});

/* ========================================================================== 16 · HOLD */
productSlide({
  section: "04 · THE PRODUCT · HOLD",
  title: "Hold: a book sorted by whether its reasoning holds",
  file: "monitor.jpg",
  items: [
    ["A validity dashboard.", "Thesis state, last check and risk sit beside value; the book sorts by conviction risk, not profit."],
    ["Two kinds of alert.", "A premise broke, on a filing; or the price moved past your own threshold, on the close."],
    ["Never a bare price move.", "Every price alert arrives with what the record says about it."],
    ["Risk you can trace.", "Concentration, exposure and stated shocks, from the same arithmetic as the pre-trade check."],
    ["Companies and a watchlist.", "Held, researched-not-owned and closed-but-watched, each with its reason and a standing budget."],
  ],
  notes: "'Down 12% this week. Nothing has been filed since your last check and all four premises still hold.' That " +
    "sentence is the product in miniature: it is the difference between an alert that causes panic and one that prevents " +
    "it, and no chat can produce it because no chat remembers what you believed last quarter. Figures on screen are invented.",
});

/* ======================================================================== 17 · REVIEW */
productSlide({
  section: "04 · THE PRODUCT · REVIEW",
  title: "Review: was it a good decision, or a lucky one?",
  file: "review.jpg",
  items: [
    ["Two questions, never conflated.", "Was the thesis right? Was the decision good?"],
    ["Four honest outcomes.", "Right for the right reasons, right anyway, wrong for good reasons, wrong for bad ones."],
    ["What you knew at the time.", "Every artefact keeps its retrieval timestamp, so a review reads what was in front of you."],
    ["Analytics with a count on everything.", "Calibration and the premises you most often get wrong, silent until the sample supports a claim."],
    ["A knowledge map.", "Companies, peers, themes, theses and decisions as a graph; Ask says where a broken premise matters elsewhere."],
  ],
  notes: "A profitable trade on a broken thesis is a bad decision that paid, and it teaches the wrong lesson if nobody " +
    "names it. Decision analytics deliberately says nothing until the sample supports it — the design shows it greyed " +
    "out at eleven reviews of twenty. The screen is an illustration.",
});

/* ===================================================================== 18 · THE INVENTORY */
{
  const s = slide({
    section: "04 · THE PRODUCT · EVERYTHING IN THE PLAN",
    title: "Everything in the plan, in one view",
    tags: ["ready"],
    notes: "Shown as delivered at your instruction: this is the V1.0 plan's scope plus the roadmap's tools. Before " +
      "sending, confirm each has shipped. F-numbers refer to docs/V1.0_Alpha/04-feature-specifications.md. Two honest " +
      "boundaries sit underneath: the step to a second user is designed but deferred, and some things are decided against.",
  });
  const cols = [
    ["Research", ["Equity research pipeline", "Standard and Quick depth", "Refresh (F4)", "Ask, in three tiers (F6)", "Model workbook (F5)", "Calculator page", "Methodology library", "Unmapped-concept curation", "Depth in primary sources (F7)", "Print what the run computed (F8)", "Adversary argues your opposite (F2)", "UK path for tagged filers (F19)"]],
    ["Decide", ["Theses: premises with tests (F9)", "Decisions, and the pass (F10)", "Pre-trade check (F12)", "The stated view, composed and authored (F13)", "Closing section reads your book (F3)"]],
    ["Hold", ["Portfolio as a validity dashboard", "Companies and watchlist", "Monitor: premises and price (F11)", "Risk and stated shocks (F12)", "Scheduling: daily and monthly passes (F15)"]],
    ["Review", ["Post-trade review (F14)", "Decision analytics (F14)", "Knowledge map learns your decisions (F20)"]],
    ["Platform", ["Local-first: your machine, your database", "Costs, health and backups", "Model portability (F18)", "Printed excerpts stay data (F16)", "Obsidian notes export"]],
  ];
  const cw = (CW - 4 * 0.18) / 5;
  cols.forEach(([name, list], i) => {
    const x = M + i * (cw + 0.18);
    box(s, x, 1.55, cw, 4.05);
    T(s, name, { x: x + 0.18, y: 1.7, w: cw - 0.36, h: 0.32, fontSize: 14, bold: true, color: C.teal });
    items(s, list, { x: x + 0.18, y: 2.1, w: cw - 0.3, h: 3.45, fontSize: 10.5, gap: 3, color: C.ink });
  });
  const hw = (CW - 0.25) / 2;
  ph(s, M, 5.78, hw, 0.88, "Designed, deferred: accounts, sharing and a sealed read-only evidence pack (F17, ADR 0120), the step to a second user.",
    { label: "NEXT, NOT YET IN THE PLAN", fontSize: 10 });
  box(s, M + hw + 0.25, 5.78, hw, 0.88, { fill: C.plumWash, line: C.plum });
  mono(s, "DECIDED AGAINST", { x: M + hw + 0.4, y: 5.9, w: hw - 0.3, h: 0.2, fontSize: 8, color: C.plum });
  T(s, "Trade execution, broker connections, a portfolio optimiser, multi-user deployment, investment advice.",
    { x: M + hw + 0.4, y: 6.15, w: hw - 0.3, h: 0.45, fontSize: 11 });
}

/* =================================================================== 19 · THE FOUNDATIONS */
{
  const s = slide({
    section: "04 · THE PRODUCT · FOUNDATIONS",
    title: "Built like a system that expects to be audited",
    tags: ["measured"],
    cite: "Counted from the repository on 25 Sep 2026; test and artefact counts from the verdict round's gate, 24 Sep 2026.",
    notes: "The engineering is part of the moat. Every architectural decision is a written record with its reason; " +
      "the invariants are enforced by tests, not by convention; and the whole suite runs with no network and no model " +
      "spend. The knowledge map is pinned to the code by a test, so the documentation fails the suite rather than going stale.",
  });
  const tiles = [
    ["8,288", "automated tests at the last gate: 8,054 default and 234 in a browser, run with no network and no model spend"],
    ["141k", "lines of application code in 415 Python modules"],
    ["144k", "lines of test code, in 281 test modules and their fixtures"],
    ["131", "architecture decision records, each a claim, immutable once accepted"],
    ["87", "database migrations"],
    ["7 + 1", "invariants: seven live and one retired by decision record, each enforced in code and pinned by tests"],
    ["772", "archived artefacts re-hashed intact at the last gate, 24 Sep 2026"],
    ["1 + 1", "one door to the network and one to the model: nothing else may make a request or import the vendor SDK"],
  ];
  const tw = (CW - 3 * 0.2) / 4;
  tiles.forEach(([big, label], i) => stat(s, M + (i % 4) * (tw + 0.2), i < 4 ? 1.6 : 3.55, tw, 1.75, big, label, { bigSize: 24 }));
  box(s, M, 5.5, CW, 1.1, { fill: C.tealWash, line: C.teal });
  T(s, "Local-first: Python 3.12, PostgreSQL and a background worker on the operator's own machine. The correctness " +
    "core, the arithmetic and domain types, is strictly typed and free of side effects; every model call is priced and " +
    "capped in code; every figure can be replayed from its own record.",
  { x: M + 0.25, y: 5.6, w: CW - 0.5, h: 0.9, fontSize: 11.5, valign: "middle" });
}

/* ==================================================================== 20 · THE AUDIT */
{
  const s = slide({
    section: "05 · EVIDENCE",
    title: "Audited on live runs, and published either way",
    tags: ["measured"],
    cite: "Readiness audit, 11–12 Sep 2026: six live runs on Microsoft, AstraZeneca and M&T Bank, three matched AI baselines, £63.32 of measured spend against a £100 ceiling.",
    notes: "0 of 779 comes from the audit's own matcher, whose precision was calibrated and whose recall was not " +
      "measured — say so if asked. 3,335 is the sum of five runs' replayed calculation rows. The 28/21 tile shows the " +
      "system is measured and repaired, not just asserted. The verdict strip is the audit's own words, including the two " +
      "criteria it failed at the time.",
  });
  const tiles = [
    ["0 of 779", "checkable figures contradicted by their source, across five reports"],
    ["259 / 259", "citations confirmed by code re-reading the archived bytes"],
    ["3,335", "calculation rows re-executed from their stored inputs, with zero divergence"],
    ["559", "archived artefacts re-hashed intact; a 36-event audit chain unbroken"],
    ["£7.19", "average per full report; five runs inside an 11% band, against 65% for the AI baseline"],
    ["28 → 21", "defects the audit found → fixed, each with a regression test"],
  ];
  const tw = (CW - 2 * 0.25) / 3;
  tiles.forEach(([big, label], i) => stat(s, M + (i % 3) * (tw + 0.25), i < 3 ? 1.6 : 3.45, tw, 1.65, big, label, { bigSize: 26 }));
  box(s, M, 5.3, CW, 1.3, { fill: C.white });
  mono(s, "THE AUDIT'S OWN VERDICT, 12 SEP 2026", { x: M + 0.25, y: 5.45, w: 5, h: 0.2, color: C.mute });
  const verdicts = [["Accurate", "yes", C.green], ["Budget-friendly", "yes", C.green], ["Reliable", "not yet", C.amber], ["Complete", "no", C.crimson]];
  verdicts.forEach(([k, v, colour], i) => {
    const x = M + 0.25 + i * 2.2;
    T(s, [{ text: k + "  ", options: { color: C.ink, bold: true } }, { text: v, options: { color: colour, bold: true } }],
      { x, y: 5.78, w: 2.1, h: 0.3, fontSize: 12.5 });
  });
  T(s, "“Not ready for general use; ready for one thing, and good at it.”", { x: 9.2, y: 5.5, w: 3.35, h: 0.95,
    fontSize: 12, italic: true, color: C.teal, valign: "middle" });
  T(s, "Since then: the bank path reached an approved M&T report on 17 Sep; reliability is the V1.0 finish line.",
    { x: M + 0.25, y: 6.15, w: 8.6, h: 0.3, fontSize: 10, color: C.mute });
}

/* ================================================================== 21 · WHERE WE LOST */
{
  const s = slide({
    section: "05 · EVIDENCE",
    title: "Where we lost, and what it told us",
    tags: ["measured"],
    cite: "Judges were language models reading under a fixed rubric. A Phase 5 test found they named our document 15 times in 15, so the panel was not blind. Baseline: Claude Opus through the API with web search.",
    notes: "Do not soften this slide. Across three rounds the model judges chose the general AI note every time, and " +
      "the pre-registered stop rule fired on 25 September 2026. What they objected to moved from 'reaches no view' to " +
      "'states a broken view'; what they valued was the evidence — the source register, the red-team log, the primary-" +
      "source trail. That is why the product now claims the evidence base and the checking instrument, and no more.",
  });
  table(s, ["ROUND", "WHEN", "CHOSE THE AI NOTE", "VERDICTS TO US"], [
    ["Readiness audit", "11–12 Sep", "9 of 9", "0 of 54 (3 equal)"],
    ["Phase 5 round", "19 Sep", "6 of 6", "2 of 36"],
    ["Verdict round", "24–25 Sep", "6 of 6", "0 of 36"],
  ], { x: M, y: 1.6, w: 6.45, colW: [1.75, 1.2, 1.7, 1.8], fontSize: 11, rowH: 0.42 });
  box(s, M, 3.5, 6.45, 1.05, { fill: C.plumWash, line: C.plum });
  T(s, [{ text: "The stop rule, written before the round: ", options: { bold: true, color: C.plum } },
    { text: "if nothing moves, narrow the claim. It fired on 25 Sep 2026, and the claim is now an evidence base and a " +
      "checking instrument.", options: { color: C.ink } }],
  { x: M + 0.2, y: 3.6, w: 6.05, h: 0.85, fontSize: 11, valign: "middle" });
  mono(s, "WHAT IT TOLD US", { x: M, y: 4.75, w: 4, h: 0.2, color: C.mute });
  items(s, [
    ["The essay is not our edge.", "The evidence underneath it is."],
    ["Self-contradiction was the top complaint,", "named first by 7 of 9 judges, and most of its fixes were small."],
    ["“State a view” was asked for by no judge.", "Five of nine named something worth keeping: the source register, the red-team log, the primary-source trail."],
  ], { x: M, y: 5.02, w: 6.45, h: 1.7, fontSize: 10.5, gap: 4 });

  mono(s, "IN THEIR WORDS", { x: 7.35, y: 1.6, w: 4, h: 0.2, color: C.mute });
  [["“Self-cancelling: it publishes two fair values 75% apart …”", "A judge, verdict round, AstraZeneca"],
    ["“B repeatedly denies figures it prints elsewhere.”", "A judge, verdict round, against the fresh AI note"],
    ["“The platform's verifiability advantage lives in the interface, not in the document it exports.”", "The readiness audit's own conclusion"]].forEach(([quote, who], i) => {
    const y = 1.9 + i * 1.55;
    box(s, 7.35, y, 5.38, 1.35);
    T(s, quote, { x: 7.58, y: y + 0.15, w: 4.95, h: 0.85, fontSize: 13, italic: true, color: C.ink });
    mono(s, who.toUpperCase(), { x: 7.58, y: y + 1.02, w: 4.9, h: 0.2, fontSize: 8, color: C.subtle });
  });
}

/* ================================================================ 22 · THE COMPARISON */
{
  const s = slide({
    section: "05 · EVIDENCE",
    title: "Against a general AI note, measured",
    tags: ["measured", "judgement"],
    cite: "Readiness audit, 11–12 Sep 2026. 'Memory' is our judgement of a capability, not a measured result; every other row is measured.",
    notes: "Column one is a frontier model's home ground and we concede it. Column three is architecture: it cannot be " +
      "bolted onto a chat after the fact. The asymmetry — closing our column-one gap is scoped work, closing their " +
      "column-three gap means building this — is the investment case.",
  });
  const cw = (CW - 2 * 0.25) / 3;
  const cols = [
    ["THE AI NOTE WINS", C.amber, C.amberWash, [
      ["Argument.", "It states a rating, a target price and an expected return."],
      ["Breadth.", "Segment tables, guidance, named competitors."],
      ["Speed.", "14–19 minutes to a note, against our 26.5–36.8 plus your gates."],
      ["Flexibility.", "Ask it anything, mid-sentence."],
    ]],
    ["A DRAW", C.mute, C.sunken, [
      ["Accuracy of stated figures.", "Nothing contradicted on either side; one flagged baseline figure was a disclosed definition."],
      ["Cost per note.", "£6.71–£11.03 for the AI notes, £6.80–£7.61 for ours."],
    ]],
    ["WE WIN, STRUCTURALLY", C.teal, C.tealWash, [
      ["Reproducibility.", "3,335 calculation rows re-execute; a chat has nothing to re-run."],
      ["Provenance.", "Hashed bytes, against links a third of which were dead the next day."],
      ["Predictability.", "An 11% cost spread against 65%, and a ceiling enforced before money moves."],
      ["Refusals.", "It will not publish an impossible figure."],
      ["Memory.", "A record of what you believed, tested against every filing."],
    ]],
  ];
  cols.forEach(([label, colour, wash, list], i) => {
    const x = M + i * (cw + 0.25);
    box(s, x, 1.6, cw, 4.3);
    s.addShape(pres.ShapeType.rect, { x: x + 0.01, y: 1.61, w: cw - 0.02, h: 0.5, fill: { color: wash } });
    mono(s, label, { x: x + 0.22, y: 1.75, w: cw - 0.44, h: 0.22, color: colour });
    items(s, list, { x: x + 0.22, y: 2.3, w: cw - 0.4, h: 3.5, fontSize: 12.5, gap: 11 });
  });
  T(s, "Column one is a frontier model's home ground, and we concede it. Column three cannot be bolted onto a chat.",
    { x: M, y: 6.1, w: CW, h: 0.4, fontSize: 13.5, bold: true, color: C.ink });
}

/* ================================================================ 23 · WHAT CHANGED */
{
  const s = slide({
    section: "05 · EVIDENCE",
    title: "We sell the evidence, not the essay",
    tags: ["ready", "placeholder"],
    notes: "The operator's decisions of 25 September 2026, taken after the verdict round. The finish line is the " +
      "plan's own definition of V1.0 done for personal use; per your instruction the deck treats it as met — confirm " +
      "before sending. The re-measurement after the fixes (about £21, pre-approved) has a result nobody knows yet: " +
      "that is the placeholder, and it matters more than anything else on this slide.",
  });
  box(s, M, 1.6, 5.75, 4.1);
  mono(s, "THE CLAIM, NARROWED ON 25 SEP 2026", { x: M + 0.25, y: 1.8, w: 5.3, h: 0.22 });
  T(s, "An evidence base and a checking instrument.", { x: M + 0.25, y: 2.12, w: 5.3, h: 0.4, fontSize: 16, bold: true, color: C.teal });
  items(s, [
    ["The view is yours.", "The platform states none; its adversary argues against yours."],
    ["Checking is the product.", "Any figure walked to its source in under 30 seconds; the valuation re-run by hand in the workbook and the calculator."],
    ["Depth, not breadth.", "Primary sources read further, never licensed commentary."],
    ["The masthead says what each method gives,", "and why two terminal methods disagree, instead of calling them a view or a range."],
  ], { x: M + 0.25, y: 2.7, w: 5.3, h: 2.9, fontSize: 12, gap: 9 });

  box(s, 6.6, 1.6, 6.13, 4.1);
  mono(s, "V1.0'S FINISH LINE, IN THE OPERATOR'S OWN TERMS", { x: 6.85, y: 1.8, w: 5.7, h: 0.22 });
  numbered(s, [
    "The verdict round's defects are fixed, and a refused figure has a way forward other than approving against the check.",
    "Three fresh runs reach an approved report with no terminal and no rescue: a US filer, AstraZeneca through its 20-F, and M&T Bank. A refresh keeps its valuation.",
    "The README and product pages claim the evidence base and the checking instrument, and no more.",
    "On the operator's own machine: a first report without documentation, a figure to its source in under 30 seconds, and under 15 minutes of attention per run.",
  ], { x: 6.85, y: 2.15, w: 5.7, step: 0.87, fontSize: 11.5 });
  ph(s, M, 5.88, CW, 0.78, "Result of the re-measurement that follows the fixes (about £21, already approved): [comparisons that did not choose the AI note] · [runs approved with no rescue] · [date]",
    { fontSize: 10.5 });
}

/* ================================================================== 24 · WHO IT'S FOR */
{
  const s = slide({
    section: "06 · CUSTOMERS & MARKET",
    title: "Who it is for",
    tags: ["judgement", "placeholder"],
    notes: "The left column is fact: the product was built for one operator, a private investor who checks the work. " +
      "The middle column is what the project's own documents already say about who pays — quoted, not invented. The " +
      "right column is yours: which segments you will test, and the evidence so far. Candidates you might consider " +
      "testing, none of them assumed: self-directed investors who write their own theses, investment clubs, independent " +
      "analysts and newsletter writers, small fund managers and family offices who must defend research to a committee.",
  });
  box(s, M, 1.6, 3.7, 5.05);
  mono(s, "BUILT FOR TODAY", { x: M + 0.22, y: 1.8, w: 3.3, h: 0.22 });
  T(s, "One operator: a financially literate private investor who researches individual companies, acts on their own " +
    "conclusions and checks the work.",
  { x: M + 0.22, y: 2.12, w: 3.26, h: 1.45, fontSize: 12.5 });
  T(s, "It runs on their own machine against their own database, one report at a time. Nothing about it assumes a team.",
    { x: M + 0.22, y: 3.35, w: 3.26, h: 1.1, fontSize: 12, color: C.mute });

  box(s, 4.5, 1.6, 4.0, 5.05);
  mono(s, "WHAT THE RECORD ALREADY SAYS", { x: 4.72, y: 1.8, w: 3.6, h: 0.22 });
  [["“The buyer most likely to pay for an auditable research record is one who has to defend their research to somebody else — a committee, a client, a co-investor, a regulator.”", "ADR 0120"],
    ["“The target user lives in Excel.”", "V1.0 workbook specification"],
    ["“A full run at £7.19 is a purchase; a £1–2 refresh on a cadence is a relationship.”", "Feature F4"]].forEach(([quote, who], i) => {
    const y = [2.15, 3.85, 4.75][i];
    T(s, quote, { x: 4.72, y, w: 3.58, h: [1.15, 0.4, 0.75][i], fontSize: 12, italic: true });
    mono(s, who.toUpperCase(), { x: 4.72, y: y + [1.2, 0.42, 0.78][i], w: 3.58, h: 0.18, fontSize: 7.5, color: C.subtle });
  });

  mono(s, "SEGMENTS TO VALIDATE", { x: 8.8, y: 1.62, w: 3.9, h: 0.22, color: C.amber });
  [1, 2, 3].forEach((n, i) => {
    ph(s, 8.8, 1.95 + i * 1.57, 3.93, 1.45,
      `[Segment ${n}]\nTheir problem: [ ]\nEvidence so far: [ ]\nWillingness to pay: [ ]`, { label: null, fontSize: 10.5 });
  });
}

/* ================================================================== 25 · MARKET SIZE */
{
  const s = slide({
    section: "06 · CUSTOMERS & MARKET",
    title: "Market size",
    tags: ["placeholder"],
    notes: "Nothing in the project's record sizes the market, so nothing here is estimated. Build it bottom-up from " +
      "the beachhead segment you validate: the number of reachable users times a tested price is more credible to an " +
      "angel than a top-down share of a large number. Cite every input; the deck's other figures are all sourced, and a " +
      "guessed market number would stand out.",
  });
  const cx = 3.35;
  const cy = 4.05;
  [[2.35, C.amberWash, C.amber], [1.62, "FFE9B8", C.amber], [0.95, "FFDC8F", C.amber]].forEach(([r, fill, line]) => {
    s.addShape(pres.ShapeType.ellipse, { x: cx - r, y: cy - r, w: 2 * r, h: 2 * r, fill: { color: fill }, line: { color: line, width: 1 } });
  });
  T(s, "TAM  [£ ___ ]", { x: cx - 1.3, y: 1.95, w: 2.6, h: 0.3, fontFace: MONO, fontSize: 12, bold: true, color: C.amberStrong, align: "center" });
  T(s, "SAM  [£ ___ ]", { x: cx - 1.3, y: 2.72, w: 2.6, h: 0.3, fontFace: MONO, fontSize: 12, bold: true, color: C.amberStrong, align: "center" });
  T(s, "SOM\n[£ ___ ]", { x: cx - 0.8, y: cy - 0.35, w: 1.6, h: 0.7, fontFace: MONO, fontSize: 12, bold: true, color: C.amberStrong, align: "center", valign: "middle" });
  const rows = [
    ["TAM", "Everyone who could use research like this: [definition] · [source]"],
    ["SAM", "Those you can reach with this product: [definition] · [source]"],
    ["SOM", "Those you can win in [n] years: [definition] · [source]"],
    ["Method", "Bottom-up: [reachable users in the beachhead] × [tested annual price] = [£]"],
  ];
  rows.forEach(([k, v], i) => ph(s, 6.35, 1.6 + i * 1.27, 6.38, 1.12, v, { label: k.toUpperCase(), fontSize: 11 }));
}

/* ======================================================================= 26 · WHY NOW */
{
  const s = slide({
    section: "06 · CUSTOMERS & MARKET",
    title: "Why now",
    tags: ["external", "measured", "judgement"],
    notes: "Four forces, each tagged by what kind of claim it is. The funding figures are from primary announcements " +
      "where we could find them (Rogo's press release, 29 April 2026) and secondary sources otherwise — verify before use. " +
      "The point of the second card is that general AI moving into personal investing makes the trust problem bigger, " +
      "not smaller. The fourth card is measured cost, plus our judgement about model prices.",
  });
  const cards = [
    ["external", "AI research is being funded, for institutions", "Rogo raised a $160M Series D led by Kleiner Perkins in April 2026; Hebbia a $130M Series B in 2024; AlphaSense is reported at about $700M of annual recurring revenue. The money is going to tools sold by the seat to firms."],
    ["external", "General AI is moving into personal investing", "Perplexity's finance pages read filings and earnings materials and now track US and Canadian portfolios; deep research sits on $20-a-month plans. Every new user of those tools meets the checking problem."],
    ["measured", "The primary data is free and machine-readable", "SEC EDGAR is US government work, free for commercial use, with tagged company facts and full-text search. Companies House publishes under the Open Government Licence. Official statistics are free."],
    ["judgement", "The cost has reached a personal price point", "A full institutional-style report measured at £7.19 of model spend, and one refresh at £1.66. The arithmetic lives in code, so the model is a replaceable component and not a lock-in."],
  ];
  const cw = (CW - 0.25) / 2;
  cards.forEach(([tag, head, text], i) => {
    const x = M + (i % 2) * (cw + 0.25);
    const y = i < 2 ? 1.6 : 4.15;
    box(s, x, y, cw, 2.35);
    T(s, head, { x: x + 0.25, y: y + 0.22, w: cw - 2.3, h: 0.6, fontSize: 14, bold: true });
    drawTag(s, tag, x + cw - 0.2 - tagWidth(tag), y + 0.25, false);
    T(s, text, { x: x + 0.25, y: y + 0.85, w: cw - 0.5, h: 1.4, fontSize: 13, color: C.mute });
  });
}

/* ================================================================== 27 · WHERE WE SIT */
{
  const s = slide({
    section: "07 · COMPETITION",
    title: "Where we sit",
    tags: ["judgement"],
    notes: "A positioning map is an argument, and this one is ours: placement is our reading of each category, not a " +
      "measurement. The bottom-right quadrant — checkable research, built for one person — is where the serious " +
      "individual investor already works, by hand, in spreadsheets and filings. We automate that work and keep the record.",
  });
  const x0 = 1.15;
  const y0 = 1.65;
  const mw = 7.3;
  const mh = 4.55;
  box(s, x0, y0, mw, mh, { fill: C.white, radius: 0 });
  rule(s, x0 + mw / 2, y0, 0, mh, { color: C.line, width: 0.75, dash: "dash" });
  rule(s, x0, y0 + mh / 2, mw, 0, { color: C.line, width: 0.75, dash: "dash" });
  rule(s, x0, y0 + mh + 0.12, mw, 0, { color: C.mute, width: 1, end: "triangle" });
  T(s, "How much of the output you can check back to its source", { x: x0, y: y0 + mh + 0.18, w: mw, h: 0.25,
    fontSize: 10, color: C.mute, align: "center" });
  T(s, "Built for institutions", { x: M - 0.05, y: y0, w: 0.5, h: 2.1, fontSize: 9.5, color: C.mute, vert: "vert270", align: "center", valign: "middle" });
  T(s, "Built for individuals", { x: M - 0.05, y: y0 + mh - 2.1, w: 0.5, h: 2.1, fontSize: 9.5, color: C.mute, vert: "vert270", align: "center", valign: "middle" });
  const bubbles = [
    ["AI research platforms", "AlphaSense · Hebbia · Rogo", 1.4, 1.85],
    ["Terminals and data", "Bloomberg · FactSet · LSEG · S&P", 3.85, 2.6],
    ["Source-linked data", "Daloopa", 5.75, 1.9],
    ["Human research", "brokers · independents · issuer-paid", 1.55, 3.55],
    ["Retail research", "Morningstar · Stockopedia · Koyfin", 1.35, 4.5],
    ["General AI", "ChatGPT · Claude · Gemini · Perplexity", 2.55, 5.35],
    ["Doing it yourself", "spreadsheets · filings · your hours", 5.8, 5.35],
  ];
  bubbles.forEach(([name, eg, x, y]) => {
    box(s, x, y, 2.3, 0.62, { fill: C.sunken, line: C.lineStrong, radius: 0.08 });
    T(s, name, { x: x + 0.12, y: y + 0.06, w: 2.1, h: 0.25, fontSize: 10.5, bold: true });
    T(s, eg, { x: x + 0.12, y: y + 0.32, w: 2.1, h: 0.24, fontSize: 8.5, color: C.mute });
  });
  box(s, 5.95, 4.35, 2.3, 0.72, { fill: C.teal, line: null, radius: 0.08 });
  T(s, "Tracework Invest", { x: 6.07, y: 4.42, w: 2.1, h: 0.3, fontSize: 12.5, bold: true, color: C.white });
  T(s, "checkable, and built for one person", { x: 6.07, y: 4.72, w: 2.1, h: 0.26, fontSize: 8.5, color: C.white });

  box(s, 8.85, 1.65, 3.88, 4.55);
  mono(s, "HOW TO READ IT", { x: 9.07, y: 1.85, w: 3.4, h: 0.22 });
  T(s, "The bottom-right quadrant, checkable research built for an individual, is where the serious private investor " +
    "already works: by hand, in spreadsheets and filings.",
  { x: 9.07, y: 2.2, w: 3.45, h: 1.55, fontSize: 12 });
  T(s, "We automate that work, keep the record, and keep checking it after the purchase.",
    { x: 9.07, y: 3.6, w: 3.45, h: 0.95, fontSize: 13, bold: true, color: C.teal });
  T(s, "Placement is our reading of each category, not a measurement.", { x: 9.07, y: 5.45, w: 3.45, h: 0.55,
    fontSize: 9.5, italic: true, color: C.subtle });
}

/* ============================================================ 28 · PROFESSIONAL TOOLS */
{
  const s = slide({
    section: "07 · COMPETITION",
    title: "Professional tools: why they matter, and why they don't stop us",
    tags: ["external", "judgement"],
    cite: "Prices and capabilities change quickly; read 25 Sep 2026 from public, mostly secondary, sources. Check each against the vendor before any meeting.",
    notes: "Each of these matters: they set the standard, they prove willingness to pay, and they attract capital. " +
      "None of them sells an individual a checkable research record of their own reasoning. Daloopa is the closest in " +
      "spirit — every data point linked to its source — and is better treated as a potential data partner than a rival.",
  });
  table(s, ["CATEGORY", "INDICATIVE PRICE", "WHY THEY MATTER", "WHY THEY DON'T STOP US"], [
    [{ text: [{ text: "Terminals and data platforms", options: { bold: true, color: C.ink, breakLine: true } }, { text: "Bloomberg, FactSet, LSEG Workspace, S&P Capital IQ", options: { color: C.mute } }], options: { bold: false } },
      "$31,980 a year for one Bloomberg terminal (2026); others quote-based",
      "The standard for data depth and the professional's default workflow. They are adding AI assistants, and they define what institutional means.",
      "Priced and sold to institutions. They supply data and tools; a research record with every figure traced into your own conclusion is still yours to build."],
    [{ text: [{ text: "AI research platforms", options: { bold: true, color: C.ink, breakLine: true } }, { text: "AlphaSense, Hebbia, Rogo", options: { color: C.mute } }], options: { bold: false } },
      "About $10,000–$40,000+ a seat a year (AlphaSense, quote-based)",
      "Proof that professionals pay for AI research and that investors fund it: Rogo raised $160M in April 2026. Fast search and answers across filings, transcripts and broker research.",
      "Enterprise sales and prices. Built for search, summarisation and drafting at institutional scale, not for an individual's own figure-by-figure record and the tracking of what they believed."],
    [{ text: [{ text: "Source-linked fundamentals", options: { bold: true, color: C.ink, breakLine: true } }, { text: "Daloopa; Canalyst, now within AlphaSense", options: { color: C.mute } }], options: { bold: false } },
      "Quote-based, institutional",
      "Validation of the thesis: professionals already pay for numbers hyperlinked to the filing. More likely data partners than rivals.",
      "They deliver data into your model. The analysis, the valuation, the prose and the monitoring of what you believed stay manual."],
  ], { x: M, y: 1.6, w: CW, colW: [2.55, 2.0, 3.79, 3.79], fontSize: 11, rowH: [0.34, 1.35, 1.45, 1.2] });
}

/* ============================================================ 29 · TOOLS FOR INDIVIDUALS */
{
  const s = slide({
    section: "07 · COMPETITION",
    title: "Tools for individuals: why they matter, and why they don't stop us",
    tags: ["external", "judgement"],
    cite: "Prices read 25 Sep 2026: ChatGPT and Koyfin from their own pages; others from secondary reviews. Stockopedia varies by region and plan.",
    notes: "General AI is the competitor that matters most, and we say so: in our own tests model judges preferred its " +
      "notes every time. Its weakness is structural, not a matter of quality. The honest incumbent for our user is doing " +
      "it yourself — full control, full checkability, and hours per company.",
  });
  table(s, ["CATEGORY", "INDICATIVE PRICE", "WHY THEY MATTER", "WHY THEY DON'T STOP US"], [
    [{ text: [{ text: "General AI assistants", options: { bold: true, color: C.ink, breakLine: true } }, { text: "ChatGPT deep research, Claude, Gemini, Perplexity", options: { color: C.mute } }], options: { bold: false } },
      "About $20 a month; $100–$200 for heavy-use tiers",
      "The real benchmark. Model judges preferred a Claude-written note in every comparison we ran. Fast, broad, cheap and improving monthly; Perplexity now tracks portfolios.",
      "Prose and numbers come from one mechanism; links rot; no cost ceiling; nothing to re-run; no record of what you believed, tested against what was filed since."],
    [{ text: [{ text: "Retail research and screening", options: { bold: true, color: C.ink, breakLine: true } }, { text: "Morningstar, Stockopedia, Koyfin, Simply Wall St, Seeking Alpha, Fiscal.ai", options: { color: C.mute } }], options: { bold: false } },
      "$249 a year (Morningstar Investor); about £240–£295+ (Stockopedia); $0–$79 a month (Koyfin)",
      "They show what individuals already pay, hundreds a year, and they own audiences, habits and good data.",
      "Scores, screens, ratings and other people's opinions, not your research. Figures are not traced to the sentence in the filing; nothing tests your premises or reviews your decisions."],
    [{ text: [{ text: "Human research", options: { bold: true, color: C.ink, breakLine: true } }, { text: "Brokers, independent and issuer-sponsored research, newsletters", options: { color: C.mute } }], options: { bold: false } },
      "Varies; issuer-sponsored notes are free to read",
      "Sector depth and a stated view, exactly what the model judges rewarded.",
      "Someone else's view, sometimes paid for by the company it covers; not checkable figure by figure; no record of your reasoning."],
    [{ text: [{ text: "Doing it yourself", options: { bold: true, color: C.ink, breakLine: true } }, { text: "Spreadsheets, EDGAR, Companies House, notes", options: { color: C.mute } }], options: { bold: false } },
      "Free, plus your time",
      "The real incumbent for our user: full control and full checkability.",
      "Hours per company, no audit trail, and nothing watches your premises while you are busy."],
  ], { x: M, y: 1.6, w: CW, colW: [2.55, 2.0, 3.79, 3.79], fontSize: 10.5, rowH: [0.34, 1.2, 1.2, 0.95, 0.8] });
}

/* ============================================================== 30 · COULD THEY BUILD IT */
{
  const s = slide({
    section: "07 · COMPETITION",
    title: "Could an incumbent simply build this?",
    tags: ["judgement", "placeholder"],
    notes: "Expect the question 'what if a frontier lab or Bloomberg just builds this?'. The honest answer: they could, " +
      "and it would mean building this — provenance from the moment a byte is fetched, not memory bolted onto a " +
      "conversation. Our edge is a head start, a documented architecture and focus on a customer the incumbents do not serve.",
  });
  table(s, ["A CHAT ANSWERS", "TRACEWORK COMPUTES"], [
    ["Prose and numbers from one mechanism", "Arithmetic in tested code; the model cannot state a figure"],
    ["Cites links that rot", "Evidence hashed the moment it is fetched"],
    ["Keeps no structured record", "A queryable record of facts, calculations, theses and decisions"],
    ["Cannot be re-run", "Any report re-executes from its own inputs"],
    ["Spends until you stop it", "A ceiling enforced before the money moves"],
  ], { x: M, y: 1.6, w: 6.2, colW: [2.8, 3.4], fontSize: 12, boldFirst: false, rowH: [0.45, 0.88, 0.88, 0.88, 0.88, 0.88] });
  box(s, 7.1, 1.6, 5.63, 4.85);
  mono(s, "WHY THE GAP SHOULD HOLD", { x: 7.33, y: 1.8, w: 5.1, h: 0.22 });
  items(s, [
    ["It is architecture, not a feature.", "Provenance has to exist from the moment a byte is fetched; it cannot be added to generated text afterwards."],
    ["A different customer.", "Terminals and AI research platforms sell seats to institutions; one investor's research record is not their business."],
    ["The record compounds.", "Every report, premise and decision a user adds makes the next check better, and leaving costlier."],
    ["An honest limit.", "A well-funded team could build this. Our edge is focus and a head start: 131 recorded design decisions and [PLACEHOLDER: months] of work."],
  ], { x: 7.33, y: 2.2, w: 5.2, h: 4.15, fontSize: 12.5, gap: 12 });
}

/* ============================================================= 31 · WHY IT CAN SUCCEED */
{
  const s = slide({
    section: "08 · WHY IT CAN WIN",
    title: "Why this can succeed",
    tags: ["judgement", "measured"],
    notes: "Six reasons; each says underneath whether it rests on a measurement or on our judgement. Lead with the " +
      "first two, which are measured. The sixth is an idea already written down in the project — an immutable, hashed " +
      "record of every report is the basis for a track record nobody can edit after the fact — not something built.",
  });
  const reasons = [
    ["Trust is the bottleneck for AI in money.", "We solve it in the architecture, not in the prompt.", "0 of 779 contradicted · 259/259 confirmed · 3,335 replayed", C.teal],
    ["Costs are known before they are spent.", "Every call is priced and capped in code.", "£7.19 a report · an 11% spread · no cap ever breached", C.teal],
    ["The record compounds.", "Theses, decisions and reviews accumulate into something only this user has, and would not want to lose.", "our judgement", C.subtle],
    ["The model is a component.", "Arithmetic lives in Python, so the language model can change as prices and policies move (F18).", "our judgement: margin should improve as models commoditise", C.subtle],
    ["The raw material is free.", "Core facts come from regulators' own filings, not from licensed commentary.", "SEC EDGAR: US government work, free for commercial use", C.teal],
    ["Honesty that can be shown.", "Every report is immutable and hashed: the basis for a track record nobody can edit afterwards.", "an idea on the record; not built", C.subtle],
  ];
  const cw = (CW - 2 * 0.25) / 3;
  reasons.forEach(([head, text, ev, evColour], i) => {
    const x = M + (i % 3) * (cw + 0.25);
    const y = i < 3 ? 1.6 : 4.15;
    box(s, x, y, cw, 2.35);
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.22, y: y + 0.22, w: 0.36, h: 0.36, fill: { color: C.teal } });
    T(s, String(i + 1), { x: x + 0.22, y: y + 0.22, w: 0.36, h: 0.36, fontFace: MONO, fontSize: 11, bold: true,
      color: C.white, align: "center", valign: "middle" });
    T(s, head, { x: x + 0.7, y: y + 0.18, w: cw - 0.9, h: 0.62, fontSize: 13, bold: true, valign: "middle" });
    T(s, text, { x: x + 0.22, y: y + 0.9, w: cw - 0.44, h: 0.9, fontSize: 11, color: C.mute });
    T(s, ev, { x: x + 0.22, y: y + 1.85, w: cw - 0.44, h: 0.4, fontFace: MONO, fontSize: 8.5, color: evColour });
  });
}

/* ============================================================= 32 · BUSINESS MODEL */
{
  const s = slide({
    section: "09 · BUSINESS MODEL",
    title: "Business model: options on the record, decisions to make",
    tags: ["judgement", "placeholder"],
    notes: "The project's documents already sketch the economics but decide none of it. The strongest idea on the " +
      "record is that the refresh turns a purchase into a subscription. The delivery model is a real fork: today's " +
      "architecture is local-first, and the recorded design for a second user (ADR 0120) adds accounts without making it " +
      "a hosted service. Choosing hosted would reverse a recorded decision, which the project does by ADR.",
  });
  mono(s, "ALREADY PROPOSED IN THE PROJECT'S DOCUMENTS", { x: M, y: 1.6, w: 6, h: 0.22 });
  const opts = [
    ["The refresh as a subscription", "“A full run at £7.19 is a purchase; a £1–2 refresh on a cadence is a relationship.”"],
    ["Fixed-price research", "Cost is bounded in code, so a flat price per report can be offered with confidence. Held for a public launch."],
    ["The sealed evidence pack", "For the buyer who must defend research: a read-only pack someone else can check without an account (F17, deferred)."],
    ["A methodology library", "User-written methods are additive-only and attack-tested, which is what would make sharing them safe."],
  ];
  const cw = (6.1 - 0.2) / 2;
  opts.forEach(([head, text], i) => {
    const x = M + (i % 2) * (cw + 0.2);
    const y = i < 2 ? 1.95 : 4.3;
    box(s, x, y, cw, 2.2);
    T(s, head, { x: x + 0.2, y: y + 0.18, w: cw - 0.4, h: 0.5, fontSize: 13, bold: true, color: C.teal });
    T(s, text, { x: x + 0.2, y: y + 0.72, w: cw - 0.4, h: 1.4, fontSize: 11, color: C.ink });
  });
  mono(s, "YOURS TO DECIDE", { x: 7.0, y: 1.6, w: 5.7, h: 0.22, color: C.amber });
  [["DELIVERY MODEL", "Local software the investor runs, as today, or a hosted service: [choice and why]"],
    ["PRICING MODEL", "Per report · subscription · both: [choice]"],
    ["PRICE POINTS", "[£ per report] · [£ per month] · [free tier or trial]"],
    ["WHO PAYS", "[the individual · the firm they report to · both]"]].forEach(([label, text], i) => {
    ph(s, 7.0, 1.95 + i * 1.15, 5.73, 1.02, text, { label, fontSize: 11 });
  });
}

/* ============================================================ 33 · UNIT ECONOMICS */
{
  const s = slide({
    section: "09 · BUSINESS MODEL",
    title: "Unit economics: the cost side is measured, the price side is yours",
    tags: ["measured", "placeholder"],
    cite: "Measured runs: readiness audit (11–12 Sep), three re-seeded runs and one Quick run (17 Sep), Phase 5 (19 Sep), the verdict round (24–25 Sep, with one refresh). Ask tier costs are design targets.",
    notes: "Only the cost column is measured, and some of it only once — say so. The price and contribution columns are " +
      "yours. Two cost lines are not model spend at all: the price-data subscription, whose current plan is personal-use " +
      "only, and hosting, which does not exist yet because the product is local-first.",
  });
  const P = { text: "[ ]", options: { color: C.amber, bold: true, fill: { color: C.amberWash } } };
  table(s, ["UNIT", "COST TO US", "HOW WE KNOW", "PRICE", "CONTRIBUTION"], [
    ["Full report, 18 sections", "£7.19 average (£6.80–£7.61); later runs £5.88–£8.40", "Measured on 12 runs", P, P],
    ["Quick report, 9 sections", "£4.95", "Measured once", P, P],
    ["Refresh", "£1.66; nothing when nothing is new", "Measured once; target under £2", P, P],
    ["Ask: recompute · re-read · research", "Free · pennies · priced first, typically £1–3", "Design targets", P, P],
    ["Monitor check on a premise", "No model spend; it is arithmetic", "By construction", P, P],
    ["Price data", "€19.99 a month, personal-use plan; commercial tier $399 a month", "Vendor terms", P, P],
    ["Hosting, per user", { text: "[£ ___ ]: local-first today, no hosting bill", options: { color: C.amber } }, "Not yet incurred", P, P],
  ], { x: M, y: 1.6, w: CW, colW: [2.75, 4.0, 2.28, 1.55, 1.55], fontSize: 10.5, rowH: 0.5 });
  box(s, M, 5.85, CW, 0.75, { fill: C.tealWash, line: C.teal });
  T(s, [{ text: "Contribution per report = ", options: { bold: true, color: C.ink } },
    { text: "[price] − £7.19 model spend − [payment fees] − [hosting share] − [data-licence share]", options: { color: C.ink } }],
  { x: M + 0.25, y: 5.9, w: CW - 0.5, h: 0.65, fontFace: MONO, fontSize: 11, valign: "middle" });
}

/* ============================================================ 34 · GO-TO-MARKET */
{
  const s = slide({
    section: "10 · GO-TO-MARKET",
    title: "Go-to-market",
    tags: ["placeholder", "judgement"],
    notes: "Nothing in the record says how the product reaches customers, so the left side is yours. The right side " +
      "lists distribution ideas the project has already written down; each is cheap relative to what it would return, " +
      "and none is built. The workbook is the quietest: an analyst emails the model to a colleague and the provenance " +
      "tab travels with it.",
  });
  mono(s, "YOURS TO FILL", { x: M, y: 1.6, w: 5, h: 0.22, color: C.amber });
  [["BEACHHEAD", "[The first segment, and why it is first]"],
    ["CHANNELS", "[Where they already gather: communities, newsletters, events, partners]"],
    ["LAUNCH OFFER", "[Price, trial, and the one result a first user sees in their first hour]"],
    ["FIRST 100 USERS", "[How, by when, and at what cost to acquire]"],
    ["PARTNERSHIPS", "[Data vendors, platforms, educators]"]].forEach(([label, text], i) => {
    ph(s, M, 1.95 + i * 0.94, 6.1, 0.84, text, { label, fontSize: 11 });
  });
  box(s, 7.0, 1.6, 5.73, 5.05);
  mono(s, "IDEAS ALREADY ON THE RECORD", { x: 7.23, y: 1.8, w: 5.2, h: 0.22 });
  items(s, [
    ["The workbook travels.", "Every report ships a live model whose provenance tab goes wherever the file goes."],
    ["A provable track record.", "Immutable, hashed reports can be scored against what happened, with nothing edited after the fact."],
    ["Replay it live.", "Re-run a months-old report in a meeting and watch every figure land identically."],
    ["A second-opinion mode.", "Diff a chat's note against the platform's and show exactly where they disagree."],
    ["A methodology library.", "Shared methods, proved safe before they run, as the start of a community."],
  ], { x: 7.23, y: 2.15, w: 5.25, h: 4.4, fontSize: 11.5, gap: 9 });
}

/* ========================================================== 35 · COSTS TO RUN */
{
  const s = slide({
    section: "11 · COSTS",
    title: "What it costs to run today",
    tags: ["measured"],
    cite: "Readiness audit, 11–12 Sep 2026; list prices from the platform's cost table, verified 24 Sep 2026; caps are the shipped defaults.",
    notes: "The chart is the predictability argument: five platform runs inside an 11% band against a 65% spread for " +
      "the AI baseline on the same briefs. Model spend is the only variable cost today, and every penny is metered per call. " +
      "Data subscriptions sit outside that ledger, and the one paid feed is on a personal-use plan — see the data risks.",
  });
  mono(s, "MODEL SPEND PER NOTE, SAME THREE BRIEFS", { x: M, y: 1.6, w: 6, h: 0.22, color: C.mute });
  [["Tracework run", "ours"], ["AI baseline note", "theirs"]].forEach(([label, who], i) => {
    const x = M + i * 2.1;
    s.addShape(pres.ShapeType.rect, { x, y: 1.97, w: 0.22, h: 0.15, fill: { color: MARK[who] } });
    T(s, label, { x: x + 0.3, y: 1.92, w: 1.75, h: 0.25, fontSize: 9.5, color: C.mute });
  });
  const labels = ["Microsoft #1", "Microsoft #2", "AstraZeneca #1", "AstraZeneca #2", "M&T Bank", "AI note: Microsoft", "AI note: AstraZeneca", "AI note: M&T Bank"];
  s.addChart(pres.ChartType.bar, [{ name: "Model spend", labels, values: [7.48, 6.80, 6.83, 7.21, 7.61, 7.63, 11.03, 6.71] }], {
    x: M, y: 2.2, w: 6.1, h: 4.45, barDir: "bar", barGapWidthPct: 45,
    chartColors: [MARK.ours, MARK.ours, MARK.ours, MARK.ours, MARK.ours, MARK.theirs, MARK.theirs, MARK.theirs],
    catAxisOrientation: "maxMin", catAxisLabelColor: C.ink, catAxisLabelFontSize: 9.5, catAxisLabelFontFace: BODY,
    catAxisLineShow: false, valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 12.5,
    valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "\"£\"0.00", dataLabelColor: C.ink,
    dataLabelFontSize: 9.5, dataLabelFontFace: MONO, showLegend: false, showTitle: false,
  });
  table(s, ["COST LINE", "TODAY"], [
    ["Model spend, full report", "£7.19 average; capped at £12 a run and £80 a month by default"],
    ["Largest single step", "Drafting: £2.31–£4.04 of each audited run"],
    ["Model list prices", "Opus 5 $5 / $25, Sonnet 5 $2 / $10, Haiku 4.5 $1 / $5 per million tokens, in / out"],
    ["Web search", "$10 per 1,000 searches: £0.04–£0.06 a run"],
    ["Price data", "EODHD, €19.99 a month, a personal-use plan"],
    ["Filings and statistics", "SEC EDGAR, Companies House, FRED, ONS, ECB: free, under each one's terms"],
    ["Infrastructure", "PostgreSQL and Redis on the operator's machine: no hosting bill"],
  ], { x: 7.0, y: 1.6, w: 5.73, colW: [1.9, 3.83], fontSize: 10, rowH: 0.6 });
}

/* ============================================================ 36 · COST TO BUILD */
{
  const s = slide({
    section: "11 · COSTS",
    title: "What it cost to build, and what a business will cost",
    tags: ["measured", "placeholder"],
    notes: "The left side is what the record measures: live model spend on the three measurement rounds, and the V1.0 " +
      "delivery plan's own estimate, written before the work. Development cost — your time, tools, anything else — is not " +
      "recorded in the project, so it is a placeholder. The right side is every cost line the risks imply for a business; " +
      "put a number on each, or say why it is zero.",
  });
  box(s, M, 1.6, 5.2, 5.05);
  mono(s, "WHAT IT HAS COST", { x: M + 0.22, y: 1.8, w: 4.7, h: 0.22 });
  [["£63.32", "live model spend on the readiness audit, against a £100 ceiling"],
    ["£16.73", "the Phase 5 measurement round"],
    ["£29.51", "the verdict round, against £31.50 approved"],
    ["74–94", "working sessions, about £80 of live spend and 39 operator hours: the V1.0 plan's own estimate"]].forEach(([big, text], i) => {
    const y = 2.18 + i * 0.8;
    T(s, big, { x: M + 0.22, y, w: 1.45, h: 0.4, fontFace: MONO, fontSize: 17, bold: true, color: C.teal });
    T(s, text, { x: M + 1.75, y: y + 0.02, w: 3.3, h: 0.72, fontSize: 10.5, color: C.ink });
  });
  ph(s, M + 0.22, 5.45, 4.76, 1.0, "Development to date: [your time] · [tools and subscriptions] · [other costs]", { fontSize: 10.5 });

  mono(s, "TO BECOME A BUSINESS", { x: 6.1, y: 1.6, w: 6, h: 0.22, color: C.amber });
  const lines = [
    ["Legal and regulatory opinion", "UK advice boundary; US position"],
    ["Company, IP assignment, licence", "MIT is marked provisional"],
    ["Commercial data licences", "price data today is personal-use"],
    ["A second user (F17)", "accounts, sharing, rate limits, deployment"],
    ["Security review", "external penetration test"],
    ["Hosting and operations", "if the delivery model needs it"],
    ["Insurance", "professional indemnity, cyber"],
    ["Team and go-to-market", "hires, marketing, acquisition"],
  ];
  lines.forEach(([head, note], i) => {
    const y = 1.95 + i * 0.585;
    box(s, 6.1, y, 6.63, 0.5, { fill: C.amberWash, line: C.amber });
    T(s, head, { x: 6.28, y: y + 0.06, w: 2.9, h: 0.38, fontSize: 11, bold: true, color: C.amberStrong, valign: "middle" });
    T(s, note, { x: 9.15, y: y + 0.06, w: 2.2, h: 0.38, fontSize: 9.5, color: C.amberStrong, valign: "middle" });
    T(s, "£ [ ___ ]", { x: 11.35, y: y + 0.06, w: 1.25, h: 0.38, fontFace: MONO, fontSize: 11, bold: true,
      color: C.amber, align: "right", valign: "middle" });
  });
}

/* ================================================================ 37–40 · RISKS */
function riskSlide(section, title, rows, notes, tags) {
  const s = slide({ section, title, tags: tags || ["measured", "judgement"], notes });
  const mitigation = (t) => t.split(/(\[PLACEHOLDER[^\]]*\])/).filter(Boolean).map((part) =>
    (part.startsWith("[PLACEHOLDER") ? { text: part, options: { color: C.amber, bold: true } } : { text: part, options: { color: C.ink } }));
  table(s, ["RISK", "WHAT WE KNOW", "MITIGATION"], rows.map((r) => [r[0], r[1], { text: mitigation(r[2]) }]),
    { x: M, y: 1.6, w: CW, colW: [2.45, 4.9, 4.78], fontSize: 11.5, rowH: [0.36, ...rows.map(() => 0.95)] });
  return s;
}

riskSlide("12 · RISKS · PRODUCT AND MARKET", "Risks: product and market", [
  ["Readers want the answer, not the evidence", "Model judges chose the general AI note in 21 of 21 comparisons over three rounds; the pre-registered stop rule fired on 25 Sep 2026.", "The claim was narrowed to the evidence base and the checking instrument; the view stays the user's. Test willingness to pay before scaling: [PLACEHOLDER]."],
  ["A run may not reach an approved report", "Readiness audit: 3 of 6 runs approved. Verdict round: 1 of 3 with no rescue.", "V1.0 finish line: three fresh runs (a US filer, AstraZeneca, M&T) approved with no terminal and no rescue; every stopped state has a labelled way forward."],
  ["It asks for time", "26.5–36.8 minutes of machine time plus six gates; the audit found nothing useful at ten minutes.", "Target under 15 minutes of attention per run; Quick mode (£4.95); refresh instead of re-research."],
  ["Gaps a reader notices", "Audited reports lacked peer multiples, segment revenue and guidance; a bank needed its own revenue path.", "Depth in primary sources (F7) and printing what the run computed (F8); the bank path produced an approved M&T report on 17 Sep."],
  ["Demand and price unproven", "Nothing in the record sizes the market, sets a price or reports a user interview.", "[PLACEHOLDER: interviews, waitlist, pilot, a pricing test]"],
], "Every row here is either measured or a gap the record admits. The first is the one an investor will press on; the " +
  "answer is that we measured it ourselves, pre-registered what would make us stop, and narrowed the claim when it fired.");

riskSlide("12 · RISKS · REGULATORY AND LEGAL", "Risks: regulatory and legal", [
  ["The advice boundary", "The project's own design notes: advising a specific person on a specific investment is a regulated activity in the United Kingdom.", "The closing section computes consequences over the user's own book, never instructions; no model may write a rating, target or recommendation. Counsel before launch: [PLACEHOLDER]."],
  ["A moving boundary", "The FCA's advice–guidance boundary review; a new targeted-support regime has been live since 6 Apr 2026.", "Counsel to confirm where the product sits, in the UK and for any US users: [PLACEHOLDER]."],
  ["Liability for a wrong figure", "Refusals and provenance make an error traceable, not impossible; the audit matcher's recall was not measured.", "Disclaimers on every surface; terms of service; professional indemnity cover: [PLACEHOLDER]."],
  ["Licence and IP", "The code is MIT-licensed, marked provisional while the project is personal.", "Choose the licence, and assign the IP to the company before investment: [PLACEHOLDER]."],
  ["Personal data", "Holdings, theses and decisions are personal financial data; local-first today.", "UK GDPR obligations for anything hosted: [PLACEHOLDER]."],
], "Nothing on this slide is legal advice, and it needs a solicitor's reading before it is shown. The project's " +
  "documents themselves say the advice question is 'for a solicitor before launch rather than after'. The US position is " +
  "not analysed anywhere in the record.", ["external", "judgement", "placeholder"]);

riskSlide("12 · RISKS · DATA, SUPPLIERS AND COVERAGE", "Risks: data, suppliers and coverage", [
  ["The price-data licence", "EODHD's €19.99 plan is personal-use; its commercial tier is $399 a month and still bars display outside the firm; written terms are outstanding.", "Budget a commercial licence or another vendor. A figure a licence forbids is withheld by type, never printed quietly."],
  ["UK-listed coverage", "London-listed companies file scanned PDFs at Companies House; the FCA's storage mechanism bars automated access without written consent.", "Today: US listings, UK companies filing a 20-F, UK companies filing tagged accounts; anything else is refused before a penny is spent. [PLACEHOLDER: UK data plan]"],
  ["Sterling valuations", "The Bank of England's robots.txt disallows its own documented download route.", "The gilt yield is an operator-confirmed assumption; a candidate series awaits verification."],
  ["One model supplier", "Anthropic is the only provider today; the meter has twice mispriced a model, each time found and fixed.", "Model portability (F18); a cost table per role; caps enforced in pounds whatever the model."],
  ["Publishers' access rules", "SEC blocks an address above about 10 requests a second; Companies House allows 600 per 5 minutes.", "One outbound door with robots checks and rate limits; a multi-user service needs per-user identification."],
], "The licence row matters most to a business: the paid feed in use is a personal plan, and the project's own decision " +
  "record (ADR 0030) accepts that a commercial version needs a different licence. UK coverage is narrower than 'UK or US' " +
  "suggests, for a reason outside the platform's control.");

riskSlide("12 · RISKS · TECHNOLOGY AND OPERATIONS", "Risks: technology, security and operations", [
  ["Single-user today", "No authentication, no inbound rate limiting, no production deployment; multi-user deployment is on the decided-against list.", "F17 is designed (ADR 0120): an account owns a book; a share is a sealed, read-only evidence pack. New tables already carry a user. [PLACEHOLDER: hosting decision]"],
  ["One run at a time", "Runs are serial by design, in machine time and in attention.", "A job architecture for concurrent users: [PLACEHOLDER]."],
  ["Security at scale", "Prompt injection, SSRF and secrets are defended in code today; a hosted service widens the surface.", "Untrusted text is wrapped as data; tool rights are enforced in code; SSRF guard; secret redaction. External test: [PLACEHOLDER]."],
  ["Key person and maintainability", "141k lines of code, 144k lines of tests and 131 decision records.", "A knowledge map pinned to the code by tests; every decision recorded with its reason; an offline test suite. [PLACEHOLDER: team]"],
  ["Cost control in production", "Metering errors have happened (a small model billed at a large model's rate; one model 50% high), each found and fixed.", "Per-run and per-month caps in code; every call writes a cost row; data subscriptions need their own budget."],
], "The first row is the largest technical step between a personal tool and a business, and the project calls it " +
  "'before this leaves one machine'. It is designed, and deliberately deferred until a solicitor has read the " +
  "consequences-not-instructions design.");

/* ================================================================ 41 · THE FUTURE */
{
  const s = slide({
    section: "13 · THE FUTURE",
    title: "What comes next",
    tags: ["placeholder"],
    notes: "Deliberately empty. The plan is treated as delivered; beyond it, the record states no roadmap, so this slide " +
      "states none either. The strip underneath is not a roadmap — it lists decisions the business cannot avoid, each of " +
      "which is already open in the project's own documents.",
  });
  const cw = (CW - 3 * 0.2) / 4;
  ["NEXT 3 MONTHS", "6 MONTHS", "12 MONTHS", "24 MONTHS"].forEach((label, i) => {
    const x = M + i * (cw + 0.2);
    ph(s, x, 1.6, cw, 2.55, "[Milestone]\n[What it proves]\n[What it costs]", { label, fontSize: 11 });
  });
  box(s, M, 4.35, CW, 2.3);
  mono(s, "DECISIONS ALREADY OPEN ON THE RECORD", { x: M + 0.22, y: 4.52, w: 8, h: 0.22 });
  const open = [
    ["Who can use it.", "Multi-user deployment is decided against; accounts and sealed packs are designed and deferred."],
    ["Where it runs.", "Local-first today; the second-user design adds accounts without hosting."],
    ["UK-listed coverage.", "Where a London-listed company's tagged figures come from is an open question."],
    ["Price data.", "A commercial licence, or another vendor."],
    ["The closing section.", "Whether it ships to users other than the author, and on what legal footing."],
    ["Licence and IP.", "MIT is marked provisional."],
  ];
  const hw = (CW - 0.6) / 2;
  items(s, open.slice(0, 3), { x: M + 0.22, y: 4.85, w: hw, h: 1.75, fontSize: 10.5, gap: 5 });
  items(s, open.slice(3), { x: M + 0.38 + hw, y: 4.85, w: hw, h: 1.75, fontSize: 10.5, gap: 5 });
}

/* ================================================================ 42 · MILESTONES */
{
  const s = slide({
    section: "13 · THE FUTURE",
    title: "Milestones, and the measures we will be judged on",
    tags: ["placeholder", "ready"],
    notes: "The right side is not invented: these bars are written into the product's own specification, with how " +
      "each is measured. Offering them as the scorecard for the investment makes the measurement culture a selling point.",
  });
  mono(s, "MILESTONES", { x: M, y: 1.6, w: 5, h: 0.22, color: C.amber });
  [1, 2, 3, 4].forEach((n, i) => {
    ph(s, M, 1.95 + i * 1.17, 6.1, 1.05, `[Milestone ${n}]  ·  [date]  ·  [measure of success]  ·  [funding needed]`,
      { label: null, fontSize: 11 });
  });
  box(s, 7.0, 1.6, 5.73, 5.05);
  mono(s, "BARS ALREADY IN THE SPECIFICATION", { x: 7.23, y: 1.8, w: 5.2, h: 0.22 });
  items(s, [
    "A first report commissioned and approved without documentation",
    "Any figure walked to its source in under 30 seconds",
    "Under 15 minutes of operator attention per run",
    "A full run under £8, a refresh under £2",
    "Zero dead ends: every stopped state has a labelled way forward",
    "Every broken premise surfaced within one filing cycle",
    "Zero false alarms across a quarter",
    "A returning user knows what to do within 10 seconds",
  ], { x: 7.23, y: 2.15, w: 5.25, h: 4.4, fontSize: 11.5, gap: 8, color: C.ink });
}

/* ======================================================================= 43 · TEAM */
{
  const s = slide({
    section: "14 · TEAM",
    title: "Team",
    tags: ["placeholder"],
    notes: "Angels invest in people first. Say why you, and why now; name the gaps honestly and how the round fills them. " +
      "Diligence will ask how the code was written and who owns it — have a one-line answer ready.",
  });
  box(s, M, 1.6, 5.2, 5.05, { fill: C.amberWash, line: C.amber });
  s.addShape(pres.ShapeType.ellipse, { x: M + 0.3, y: 1.9, w: 1.3, h: 1.3, fill: { color: C.white }, line: { color: C.amber, width: 1, dashType: "dash" } });
  T(s, "[photo]", { x: M + 0.3, y: 1.9, w: 1.3, h: 1.3, fontFace: MONO, fontSize: 10, color: C.amber, align: "center", valign: "middle" });
  T(s, "[Founder name]", { x: M + 1.85, y: 2.05, w: 3.2, h: 0.4, fontSize: 18, bold: true, color: C.amberStrong });
  T(s, "[Role]", { x: M + 1.85, y: 2.5, w: 3.2, h: 0.3, fontSize: 12, color: C.amberStrong });
  T(s, "[Background: investing, engineering, domain]\n\n[Why you: what you know that others don't]\n\n[How the platform was built, and who owns the code]",
    { x: M + 0.3, y: 3.5, w: 4.6, h: 2.9, fontSize: 12, color: C.amberStrong });
  const cw = (CW - 5.2 - 0.3 - 0.2) / 2;
  [["ADVISERS", ["[Regulatory / legal]", "[Investment professional]", "[Technical]"]],
    ["TO HIRE WITH THIS ROUND", ["[Role 1]", "[Role 2]", "[Role 3]"]]].forEach(([label, list], i) => {
    const x = M + 5.5 + i * (cw + 0.2);
    mono(s, label, { x, y: 1.6, w: cw, h: 0.22, color: C.amber });
    list.forEach((t, j) => ph(s, x, 1.95 + j * 1.58, cw, 1.45, `${t}\n[name]\n[why they matter]`, { label: null, fontSize: 11 }));
  });
}

/* ==================================================================== 44 · THE ASK */
{
  const s = slide({
    section: "14 · THE ASK",
    title: "The ask",
    tags: ["placeholder"],
    notes: "The earlier decks deliberately stated no ask, because the audit measured none of it. This slide is where " +
      "yours goes. The use-of-funds lines mirror the cost slide, so every pound maps to a risk it retires. UK angels will " +
      "usually ask about SEIS or EIS eligibility — check it with an accountant before the meeting.",
  });
  [["RAISING", "£ [ ___ ]"], ["INSTRUMENT", "[equity · ASA · convertible]"], ["VALUATION OR CAP", "£ [ ___ ]"], ["TAX RELIEF", "[SEIS / EIS: confirm eligibility]"]].forEach(([label, v], i) => {
    ph(s, M, 1.6 + i * 1.02, 4.4, 0.9, v, { label, fontSize: 15 });
  });
  box(s, 5.3, 1.6, 7.43, 3.95);
  mono(s, "USE OF FUNDS", { x: 5.53, y: 1.8, w: 5, h: 0.22 });
  ["A second user: accounts, sharing, deployment (F17)", "Legal and regulatory opinion", "Commercial data licences", "Security review", "Go-to-market", "Team", "Contingency"].forEach((line, i) => {
    const y = 2.18 + i * 0.47;
    T(s, line, { x: 5.53, y, w: 4.3, h: 0.36, fontSize: 11.5, valign: "middle" });
    s.addShape(pres.ShapeType.rect, { x: 9.9, y: y + 0.08, w: 1.9, h: 0.2, fill: { color: C.amberWash }, line: { color: C.amber, width: 0.75, dashType: "dash" } });
    T(s, "[ ]%", { x: 11.9, y, w: 0.65, h: 0.36, fontFace: MONO, fontSize: 11, bold: true, color: C.amber, align: "right", valign: "middle" });
  });
  ph(s, 5.3, 5.7, 7.43, 0.95, "This round gets us to: [milestone] by [date], with [runway] months of runway.", { label: "WHAT IT BUYS", fontSize: 12 });
  ph(s, M, 5.7, 4.4, 0.95, "[How you would like them to respond, and by when]", { label: "NEXT STEP", fontSize: 11 });
}

/* ================================================================== 45 · CLOSING */
{
  const s = slide({
    dark: true,
    notes: "End on the category difference, not on a number. Then stop talking and take questions.",
  });
  mono(s, "TRACEWORK INVEST", { x: M, y: 0.95, w: 6, h: 0.3, fontSize: 11, charSpacing: 3, color: C.tealUp });
  T(s, "A chat gives you an answer you have to trust.", { x: M, y: 1.6, w: 11.5, h: 0.8, fontFace: HEAD, fontSize: 34,
    bold: true, color: C.muteDark });
  T(s, "Tracework gives you research you can check, and a record of why you bought.", { x: M, y: 2.45, w: 11.8, h: 1.4,
    fontFace: HEAD, fontSize: 34, bold: true, color: C.inkDark });
  T(s, "[PLACEHOLDER: founder name  ·  email  ·  phone  ·  website]", { x: M, y: 4.4, w: 9, h: 0.35, fontFace: MONO,
    fontSize: 11, color: C.amberUp });
  box(s, M, 5.2, CW, 1.3, { fill: C.panel, line: C.lineDark });
  T(s, "Tracework Invest is a research tool. It is not regulated investment advice, and nothing it produces is a " +
    "recommendation to buy, sell or hold any security. Every figure in this deck is measured and sourced, or marked " +
    "as a judgement, an external figure to verify, or a placeholder. [PLACEHOLDER: disclaimer wording approved by counsel]",
  { x: M + 0.25, y: 5.32, w: CW - 0.5, h: 1.06, fontSize: 10.5, color: C.muteDark, valign: "middle" });
}

/* ================================================================== APPENDIX */
{
  const s = slide({
    section: "APPENDIX · A1",
    title: "Architecture: five trust zones",
    tags: ["ready"],
    notes: "The code is organised by trust zone rather than alphabetically, and a change inside a stricter zone carries " +
      "stricter obligations. Source: docs/developers/knowledge-map.md §4.",
  });
  const zones = [
    ["1", "The correctness core", "core, calc", "Every number the platform produces. Pure: strictly typed, no input or output, no clock, no globals. Property-based tests."],
    ["2", "The guarded doors", "fetch, providers, storage", "The only way out to the network (SSRF guard, robots, rate limits), the only door to a model, and a content-addressed, immutable store."],
    ["3", "The model-facing layer", "agents, skills", "Roles a model performs, granted from a registry. Everything a model returns is checked in code; skill files are additive-only."],
    ["4", "Orchestration and services", "workflow, services, sources, extract, verify, eval, render", "Recorded, resumable steps with a budget guard and gates; adapters per publisher; deterministic verification; rendering."],
    ["5", "The shell", "web, api, db, cli, worker", "Server-rendered interface that works without scripting, the database and its migrations, the command line and the background worker."],
  ];
  zones.forEach(([n, name, mods, text], i) => {
    const y = 1.6 + i * 1.0;
    const inset = i * 0.18;
    box(s, M + inset, y, CW - 2 * inset, 0.88, { fill: i === 0 ? C.tealWash : C.white, line: i === 0 ? C.teal : C.line });
    T(s, n, { x: M + inset + 0.2, y: y + 0.2, w: 0.4, h: 0.5, fontFace: MONO, fontSize: 20, bold: true, color: C.teal });
    T(s, name, { x: M + inset + 0.7, y: y + 0.12, w: 3.2, h: 0.3, fontSize: 13, bold: true });
    T(s, mods, { x: M + inset + 0.7, y: y + 0.45, w: 3.2, h: 0.35, fontFace: MONO, fontSize: 8.5, color: C.mute });
    T(s, text, { x: M + inset + 4.0, y: y + 0.12, w: CW - 2 * inset - 4.2, h: 0.68, fontSize: 10.5, color: C.ink, valign: "middle" });
  });
}

{
  const s = slide({
    section: "APPENDIX · A2",
    title: "The invariants, and what enforces each",
    tags: ["ready"],
    notes: "Weakening one is a recorded decision, not a code change. Invariant 4 was retired by ADR 0113 when the " +
      "readiness audit showed it had never fired; a test keeps the retired enforcement gone. Source: CLAUDE.md and " +
      "docs/developers/knowledge-map.md §5.",
  });
  table(s, ["#", "INVARIANT", "ENFORCED BY"], [
    ["1", "Every externally derived fact traces to a hashed artefact", "The content-addressed store; a command re-hashes every artefact"],
    ["2", "The model may propose a citation; only code confirms one", "A verifier that re-reads the artefact by hash and finds the excerpt"],
    ["3", "No figure reaches a report unless it is a stored fact, a recorded calculation or an attestation", "A numeral scan and closed-world evidence checks on every section"],
    [{ text: "4", options: { color: C.subtle } }, { text: "Retired: a run reads the filings as they stand", options: { color: C.subtle } }, { text: "ADR 0113; a scan keeps the retired enforcement gone", options: { color: C.subtle } }],
    ["5", "Units are carried through all arithmetic; a mismatch raises", "A units algebra, tested in both operand orders"],
    ["6", "Cost is metered and capped in code", "A budget guard before every step; every call writes a cost row"],
    ["7", "Skill files are additive-only", "A skill policy, and an attack corpus that must all fail"],
    ["8", "Untrusted content is data, never instruction", "Wrapping and labelling; tool authorisation in code; injection tests"],
  ], { x: M, y: 1.6, w: CW, colW: [0.5, 5.9, 5.73], fontSize: 10.5, rowH: 0.5 });
}

{
  const s = slide({
    section: "APPENDIX · A3",
    title: "Data sources: cost and licence status",
    tags: ["ready", "external"],
    cite: "From the project's source dossiers (docs/data-sources/). Several terms were read through search results only and are marked there to verify.",
    notes: "Two sources were declined on their own terms and stay declined: the FCA's National Storage Mechanism and " +
      "the Bank of England's statistical database. The one paid feed is on a personal-use plan. Everything else is free " +
      "public data under licences that permit commercial use, series by series in FRED's case.",
  });
  table(s, ["SOURCE", "WHAT IT GIVES", "COST", "COMMERCIAL USE", "STATUS"], [
    ["SEC EDGAR", "US filings, tagged company facts, full-text search; 20-F filers", "Free", "Yes: US government work", "Wired, core"],
    ["Companies House", "UK register, filings and accounts", "Free, with a key", "Open Government Licence, with attribution", "Wired; listed companies refused"],
    ["UK inline XBRL", "Offline extraction of tagged accounts", "Free, Apache-2.0", "As its source", "Wired"],
    ["EODHD", "End-of-day prices, splits, dividends, US and UK", "€19.99 a month; commercial $399", "Not on the current plan", "Wired, optional"],
    ["FRED / ALFRED", "US risk-free rate and macro series", "Free, with a key", "Series by series; three refused", "Wired"],
    ["ECB, ONS", "Euro reference rates; UK prices and output", "Free", "Yes, with credit", "Built, not yet called"],
    ["Issuer websites", "Annual reports, presentations", "Free", "Issuer copyright; quoted for research", "Built, not wired"],
    ["FCA NSM", "UK regulated announcements", "—", "Automated access needs written consent", { text: "Declined", options: { color: C.plum, bold: true } }],
    ["Bank of England", "Gilt yields, Bank Rate", "Free", "Open Government Licence", { text: "Declined: robots.txt", options: { color: C.plum, bold: true } }],
  ], { x: M, y: 1.6, w: CW, colW: [1.75, 3.35, 2.15, 2.9, 1.98], fontSize: 9.5, rowH: 0.42 });
}

{
  const s = slide({
    section: "APPENDIX · A4",
    title: "The readiness audit, run by run",
    tags: ["measured"],
    cite: "Readiness audit, 11–12 Sep 2026, and its scorecard. The abandoned first M&T run (£2.03) stopped at the assumptions gate before any report existed.",
    notes: "Totals: 259 citations and 779 checkable figures match the audit's main text and the replay records. The " +
      "scorecard prints slightly different per-run citation counts; the totals here are the audit's. Calculations " +
      "replayed sum to 3,335.",
  });
  table(s, ["RUN", "SPEND", "CITATIONS CONFIRMED", "CHECKABLE FIGURES CONTRADICTED", "CALCULATIONS REPLAYED", "OUTCOME"], [
    ["Microsoft #1", "£7.48", "69 of 69", "0 of 169", "852", "Approved"],
    ["Microsoft #2", "£6.80", "57 of 57", "0 of 188", "857", "Refused at the final gate"],
    ["AstraZeneca #1", "£6.83", "34 of 34", "0 of 165", "152", "Approved; valuation withheld"],
    ["AstraZeneca #2", "£7.21", "43 of 43", "0 of 178", "831", "Approved, with a valuation"],
    ["M&T Bank", "£7.61", "56 of 56", "0 of 79", "643", "Refused: revenue read as fee income"],
    [{ text: "AI note: Microsoft", options: { color: C.amber, bold: true } }, "£7.63", "no citation block", "0 of 154", "—", "16 minutes"],
    [{ text: "AI note: AstraZeneca", options: { color: C.amber, bold: true } }, "£11.03", "no citation block", "1 flagged of 179", "—", "19 minutes"],
    [{ text: "AI note: M&T Bank", options: { color: C.amber, bold: true } }, "£6.71", "no citation block", "0 of 107", "—", "14 minutes"],
  ], { x: M, y: 1.6, w: CW, colW: [2.15, 1.0, 2.0, 2.6, 1.9, 2.48], fontSize: 10.5, rowH: 0.48 });
}

{
  const s = slide({
    section: "APPENDIX · A5",
    title: "Three measurement rounds",
    tags: ["measured"],
    cite: "docs/plan/readiness-audit-2026-09.md, phase-5-round-2026-09/, phase-7-round-2026-09/. Phase 5 costs overstate the Sonnet lines: the meter charged 50% high from 1 Sep.",
    notes: "Each round was pre-registered: what would count as fixed, and what would mean stopping, was written down " +
      "before any money was spent. That is why the stop rule could fire, and why an investor can trust the other numbers.",
  });
  table(s, ["ROUND", "WHAT RAN", "REACHED AN APPROVED REPORT", "COMPARISONS, MODEL JUDGES", "WHAT IT CONCLUDED"], [
    ["Readiness audit\n11–12 Sep", "6 runs, 3 AI baselines; £63.32", "3 of 6", "9 of 9 chose the AI note; 0 of 54 verdicts to us", "“Not ready for general use; ready for one thing, and good at it.”"],
    ["Phase 5 round\n19 Sep", "AstraZeneca £7.61, Microsoft £7.06; 96 of 96 citations", "2 of 2, each after the operator overrode its own final gate", "6 of 6 chose the AI note; 2 of 36 verdicts to us", "The complaint moved from “no view” to “a broken view”."],
    ["Verdict round\n24–25 Sep", "AstraZeneca £6.17, Microsoft £5.88, a refresh £1.66; a fresh baseline cost £12.82", "1 of 3 with no rescue", "6 of 6 chose the AI note; 0 of 36 verdicts to us", "The stop rule fired; the claim narrowed to an evidence base and a checking instrument."],
  ], { x: M, y: 1.6, w: CW, colW: [1.7, 2.9, 2.35, 2.45, 2.73], fontSize: 11, rowH: [0.36, 1.0, 1.0, 1.1] });
  box(s, M, 5.4, CW, 0.62, { fill: C.tealWash, line: C.teal });
  T(s, "Between rounds, 17 Sep: the bank path reached an approved M&T report for £8.40, with 59 of 59 citations and revenue read correctly at $9,690m.",
    { x: M + 0.22, y: 5.44, w: CW - 0.44, h: 0.54, fontSize: 11, valign: "middle" });
}

{
  const s = slide({
    section: "APPENDIX · A6",
    title: "What due diligence will find, and our answer",
    tags: ["measured", "placeholder"],
    notes: "Everything on the left is in the repository for anyone who reads it. Better that the investor hears it from " +
      "you, with the answer, than finds it alone. Delete rows only if they no longer apply.",
  });
  table(s, ["THEY WILL FIND", "OUR ANSWER"], [
    ["Model judges chose the general AI note in every comparison, and the stop rule fired", "We pre-registered it and published it. We claim the evidence base and the checking instrument, not the essay."],
    ["In the latest round, 1 of 3 runs reached an approved report with no rescue; a refresh once lost its valuation", "Fixed in code per the plan; the re-measurement result: [PLACEHOLDER]."],
    ["The price-data plan is personal-use", "A commercial licence is in the use of funds: [PLACEHOLDER]."],
    ["London-listed companies are not covered", "Refused at the door by design, before any spend: [PLACEHOLDER: UK data plan]."],
    ["Single-user; multi-user deployment decided against; ADR 0012 names Claude as the only model provider", "Both are recorded decisions that a business reverses by a new record; F17 and F18 are designed for it."],
    ["A redirect sent the Companies House key to a third-party host on 18 Sep 2026; the host refused the request", "The defect was fixed the same day; the key is rotated before any outside access: [PLACEHOLDER: date]."],
    ["The product has two names in the repository, and the code is MIT-licensed", "One name chosen; licence and IP settled before the round closes: [PLACEHOLDER]."],
    ["The judges were language models, and not blind", "Said on every slide that quotes them."],
  ], { x: M, y: 1.6, w: CW, colW: [6.0, 6.13], fontSize: 10.5, boldFirst: false, rowH: 0.56 });
}

{
  const s = slide({
    section: "APPENDIX · A7",
    title: "Glossary",
    tags: ["ready"],
    notes: "The words the product uses precisely. Useful when an investor reads the repository or the product's own pages.",
  });
  const terms = [
    ["Artefact", "A fetched document, stored by the hash of its bytes and never changed."],
    ["Citation", "A claim's pointer into an artefact. The model may propose one; only code confirms it."],
    ["Calculation", "A recorded computation: its formula, inputs with units and sources, and the code version."],
    ["Attestation", "What the operator says about their own book, such as a holding or a fill. It never reaches a shareable page."],
    ["Gate", "A point where the run stops for a human decision, with the consequence stated."],
    ["Premise", "One sentence of a thesis, with the test that would defeat it or a date to look again."],
    ["Thesis", "What the operator believes about a company: a set of premises, revised and never erased."],
    ["Decision", "Add, open, trim, exit or pass, linked to a thesis and a report, written before the outcome."],
    ["Refresh", "A second run that reads what is new, recomputes everything and re-drafts what moved."],
    ["Ask tiers", "Recompute (free), re-read (pennies), research (priced and approved first)."],
    ["Skill file", "A report method in plain language. It can add requirements, never relax one."],
    ["ADR", "An architecture decision record: a claim with its reasons, immutable once accepted."],
  ];
  const cw = (CW - 0.3) / 2;
  terms.forEach(([term, def], i) => {
    const x = M + (i < 6 ? 0 : cw + 0.3);
    const y = 1.6 + (i % 6) * 0.84;
    box(s, x, y, cw, 0.74);
    T(s, term, { x: x + 0.2, y: y + 0.1, w: 1.6, h: 0.54, fontSize: 12, bold: true, color: C.teal, valign: "middle" });
    T(s, def, { x: x + 1.85, y: y + 0.08, w: cw - 2.0, h: 0.58, fontSize: 10.5, valign: "middle" });
  });
}

{
  const s = slide({
    section: "APPENDIX · A8",
    title: "Sources and disclaimer",
    tags: ["measured", "external"],
    notes: "pitch-sources.md, beside this deck in the repository, maps every figure slide by slide, including every " +
      "external URL and the date it was read.",
  });
  box(s, M, 1.6, 5.9, 5.05);
  mono(s, "IN THE REPOSITORY", { x: M + 0.22, y: 1.8, w: 5.4, h: 0.22 });
  items(s, [
    ["Readiness audit:", "docs/plan/readiness-audit-2026-09.md, with its run records"],
    ["Measurement rounds:", "docs/plan/phase-5-round-2026-09/ and phase-7-round-2026-09/"],
    ["Scope and decisions:", "docs/plan/ROADMAP.md and docs/adr/"],
    ["The V1.0 plan:", "docs/V1.0_Alpha/, features, pages and the delivery plan"],
    ["Data sources:", "docs/data-sources/, one dossier per publisher"],
    ["Figure by figure:", "docs/product/investor-deck/pitch-sources.md"],
    ["What it is:", "docs/product/what-it-is.md"],
    ["How it is built:", "docs/developers/knowledge-map.md"],
    ["The earlier decks:", "docs/product/investor-deck/sources.md"],
  ], { x: M + 0.22, y: 2.15, w: 5.45, h: 4.3, fontSize: 12, gap: 9 });
  box(s, 6.8, 1.6, 5.93, 3.2);
  mono(s, "EXTERNAL, READ 25 SEP 2026", { x: 7.02, y: 1.8, w: 5.4, h: 0.22 });
  items(s, [
    "Rogo Series D: PR Newswire, 29 Apr 2026",
    "FCA PS25/22, targeted support: fca.org.uk",
    "Koyfin, ChatGPT: the vendors' own pages",
    "Bloomberg, AlphaSense, Morningstar, Stockopedia, Fiscal.ai, Perplexity: secondary reviews",
    "Hebbia, Daloopa: Wikipedia; daloopa.com",
    "Every URL, with the date read: pitch-sources.md",
  ], { x: 7.02, y: 2.15, w: 5.5, h: 2.6, fontSize: 11.5, gap: 6 });
  box(s, 6.8, 4.95, 5.93, 1.7, { fill: C.sunken });
  T(s, "This deck describes a research tool. It is not investment advice, it is not an offer of securities, and it " +
    "makes no forecast. [PLACEHOLDER: confidentiality and offer wording approved by counsel]",
  { x: 7.02, y: 5.07, w: 5.5, h: 1.46, fontSize: 11.5, color: C.mute, valign: "middle" });
}

save(OUT).then(() => console.log(`Wrote ${OUT} (${page} slides)`)).catch((e) => {
  console.error(e);
  process.exit(1);
});
