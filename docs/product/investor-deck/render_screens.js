/*
 * Renders the V1.0 design artboards (docs/V1.0_Alpha/design/screens/*.dc.html) to the small
 * PNGs the angel pitch deck shows. Only needed when an artboard changes: the PNGs are
 * committed, so rebuilding the deck needs pptxgenjs alone.
 *
 *   node render_screens.js            (needs playwright, with a Chromium, and sharp)
 *
 * The artboards are illustrations. Every company, price and percentage in them is invented,
 * and the deck says so beside each one.
 *
 * They were drawn before the README settled on the product's name, so the brand text is
 * substituted to match the deck. Change BRAND here and in build_pitch_deck.js together.
 */

const path = require("path");
const { chromium } = require("playwright");
const sharp = require("sharp");

const BRAND = "Tracework";
const ARTBOARDS = path.join(__dirname, "..", "..", "V1.0_Alpha", "design", "screens");
const OUT = path.join(__dirname, "screens");

/* Each artboard is 1440 x 900 CSS pixels. "content" drops the navigation rail, the search bar
   and the footer, which keeps the part a slide can make legible; "full" keeps the whole app. */
const CROPS = {
  full: { x: 0, y: 0, width: 1440, height: 900 },
  content: { x: 236, y: 56, width: 1204, height: 803 },
};

const SCREENS = [
  ["Main", "today", "full", 1400],
  ["Request", "request", "content", 1200],
  ["Report", "report", "content", 1200],
  ["Ask", "ask", "content", 1200],
  ["Thesis", "thesis", "content", 1200],
  ["Decision", "decision", "content", 1200],
  ["Alert", "monitor", "content", 1200],
  ["Review", "review", "content", 1200],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  for (const [artboard, name, crop, width] of SCREENS) {
    await page.goto("file://" + path.join(ARTBOARDS, artboard + ".dc.html"), { waitUntil: "load" });
    await page.waitForTimeout(1000);
    await page.evaluate((brand) => {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        node.nodeValue = node.nodeValue.replace(/Ageantic/g, brand);
      }
    }, BRAND);
    const shot = await page.screenshot({ clip: CROPS[crop] });
    // Rendered at twice the size and scaled down, so text stays sharp; JPEG at this quality is
    // what keeps eight screens and the deck around them under the repository's 1 MB limit on
    // an added file. A palette PNG was tried first and came out larger for these artboards.
    await sharp(shot)
      .resize({ width })
      .jpeg({ quality: 78, mozjpeg: true })
      .toFile(path.join(OUT, name + ".jpg"));
    console.log(`${artboard} -> screens/${name}.jpg`);
  }
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
