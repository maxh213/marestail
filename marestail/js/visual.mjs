import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const MARGIN = 24;
const SETTLE_MS = 200;
const SHOT_ATTEMPTS = 5;
const QUIET = "*, *::before, *::after { animation: none !important; transition: none !important; }";

const check = async () => {
  const browser = await chromium.launch();
  await browser.close();
};

const styleSheet = (hide) => (hide.length ? `${QUIET}\n${hide.join(", ")} { visibility: hidden !important; }` : QUIET);

const blocker = (sources) => {
  const patterns = sources.map((source) => new RegExp(source));
  return (route) => (patterns.some((pattern) => pattern.test(route.request().url())) ? route.abort() : route.continue());
};

const openPage = async (browser, spec) => {
  const context = await browser.newContext({
    viewport: { width: spec.viewport.width, height: spec.viewport.height },
    deviceScaleFactor: spec.viewport.scale,
    hasTouch: spec.viewport.touch,
  });
  await context.route(/.*/, blocker(spec.block));
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(spec.url, { waitUntil: "load", timeout: spec.timeout });
  await page.addStyleTag({ content: styleSheet(spec.hide) });
  return { context, page, errors };
};

const found = (page, selector, timeout) =>
  page.waitForSelector(selector, { state: "attached", timeout }).then(
    () => true,
    () => false,
  );

const boxKey = (page, selector) =>
  page.evaluate((wanted) => {
    const element = document.querySelector(wanted);
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return [rect.x + scrollX, rect.y + scrollY, rect.width, rect.height].map(Math.round).join(",");
  }, selector);

const settled = async (page, spec) => {
  const deadline = Date.now() + spec.timeout;
  let last = await boxKey(page, spec.selector);
  while (Date.now() < deadline) {
    await page.waitForTimeout(SETTLE_MS);
    const next = await boxKey(page, spec.selector);
    if (next === last) return true;
    last = next;
  }
  return false;
};

const scrollTo = (page, selector) =>
  page.evaluate((wanted) => document.querySelector(wanted).scrollIntoView({ block: "center", inline: "nearest" }), selector);

const prepare = async (page, spec) => {
  if (!(await found(page, spec.selector, spec.timeout))) return "not_found";
  if (spec.scroll) await scrollTo(page, spec.selector);
  if (spec.wait && !(await found(page, spec.wait, spec.timeout))) return "wait";
  return (await settled(page, spec)) ? null : "settle";
};

const read = ({ selector, styles, inside, mustNotChange }) => {
  const boxOf = (element) => {
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {
      x: Math.round(rect.x + scrollX),
      y: Math.round(rect.y + scrollY),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  };
  const pick = (wanted) => boxOf(document.querySelector(wanted));
  const shown = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const nameOf = (element) => {
    const tag = element.tagName.toLowerCase();
    if (element.id) return `${tag}#${element.id}`;
    return element.classList.length ? `${tag}.${element.classList[0]}` : tag;
  };
  const ancestorsOf = (element) => {
    const chain = [];
    for (let node = element.parentElement; node && node !== document.documentElement; node = node.parentElement) chain.push(node);
    return chain;
  };
  const crosses = (one, other) =>
    Math.min(one.x + one.width, other.x + other.width) > Math.max(one.x, other.x) &&
    Math.min(one.y + one.height, other.y + other.height) > Math.max(one.y, other.y);
  const overlapsOf = (element) => {
    const chain = ancestorsOf(element);
    const own = boxOf(element);
    const candidates = chain.flatMap((ancestor) => [...ancestor.children]);
    const others = candidates.filter((node) => node !== element && !chain.includes(node) && shown(node));
    return [...new Set(others.filter((node) => crosses(own, boxOf(node))).map(nameOf))];
  };
  const element = document.querySelector(selector);
  const computed = element ? getComputedStyle(element) : null;
  return {
    selector,
    box: boxOf(element),
    styles: Object.fromEntries(computed ? styles.map((name) => [name, computed.getPropertyValue(name)]) : []),
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    inside: { selector: inside, box: inside ? pick(inside) : null },
    must_not_change: Object.fromEntries(mustNotChange.map((wanted) => [wanted, pick(wanted)])),
    overlaps: element ? overlapsOf(element) : [],
  };
};

const clipOf = async (page, box) => {
  const [width, height] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  const x = Math.max(0, box.x - MARGIN);
  const y = Math.max(0, box.y - MARGIN);
  return { x, y, width: Math.min(width, box.x + box.width + MARGIN) - x, height: Math.min(height, box.y + box.height + MARGIN) - y };
};

const shoot = async (page, options, attempts = SHOT_ATTEMPTS) => {
  try {
    await page.screenshot({ ...options, animations: "disabled" });
  } catch (error) {
    if (attempts <= 1) throw error;
    await page.waitForTimeout(SETTLE_MS);
    await shoot(page, options, attempts - 1);
  }
};

const pictures = async (page, geometry, out) => {
  mkdirSync(out, { recursive: true });
  await shoot(page, { path: join(out, "viewport.png") });
  if (geometry.box) await shoot(page, { path: join(out, "element.png"), fullPage: true, clip: await clipOf(page, geometry.box) });
  writeFileSync(join(out, "geometry.json"), `${JSON.stringify(geometry, null, 2)}\n`);
};

const measure = async (page, errors, spec, out) => {
  const failure = await prepare(page, spec);
  const measured = await page.evaluate(read, spec.query);
  const scrolled = await page.evaluate(() => Math.round(scrollY));
  const geometry = { viewport: spec.viewport, ...measured, errors, scrollY: scrolled };
  if (out) await pictures(page, geometry, out);
  return { geometry, failure };
};

const load = async (browser, spec, out) => {
  const { context, page, errors } = await openPage(browser, spec);
  try {
    return await measure(page, errors, spec, out);
  } finally {
    await context.close();
  }
};

const warm = async (browser, spec) => {
  const { context } = await openPage(browser, spec);
  await context.close();
};

const capture = async (spec) => {
  const browser = await chromium.launch();
  try {
    await warm(browser, spec);
    const captures = [];
    for (let index = 0; index < spec.captures; index += 1) {
      const taken = await load(browser, spec, index === 0 ? spec.out : null);
      captures.push(taken);
      if (taken.failure) break;
    }
    process.stdout.write(`${JSON.stringify({ captures })}\n`);
  } finally {
    await browser.close();
  }
};

const [mode, raw] = process.argv.slice(2);
await (mode === "--check" ? check() : capture(JSON.parse(raw)));
