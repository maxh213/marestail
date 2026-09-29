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
  const response = await page.goto(spec.url, { waitUntil: "load", timeout: spec.timeout });
  await page.addStyleTag({ content: styleSheet(spec.hide) });
  return { context, page, errors, status: response ? response.status() : null };
};

const found = (page, selector, timeout) =>
  page.waitForSelector(selector, { state: "attached", timeout }).then(
    () => true,
    () => false,
  );

const boxKey = async (page, selector, element = null) => {
  const { box } = await page.evaluate(read, { selector, element, styles: [], inside: null, mustNotChange: [] });
  return JSON.stringify(box);
};

const settled = async (page, timeout, key) => {
  const deadline = Date.now() + timeout;
  let last = await key();
  while (Date.now() < deadline) {
    await page.waitForTimeout(SETTLE_MS);
    const next = await key();
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
  return (await settled(page, spec.timeout, () => boxKey(page, spec.selector))) ? null : "settle";
};

const largestShown = (nodes) => {
  const area = (node) => {
    const rect = node.getBoundingClientRect();
    return rect.width * rect.height;
  };
  const shown = (node) => {
    const style = getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && area(node) > 0;
  };
  return nodes.reduce((best, node, index) => (shown(node) && (best < 0 || area(node) > area(nodes[best])) ? index : best), -1);
};

const largestOf = async (page, selector) => {
  const index = await page.$$eval(selector, largestShown);
  return index < 0 ? null : (await page.$$(selector))[index];
};

const centre = (element) => element.evaluate((node) => node.scrollIntoView({ block: "center", inline: "nearest" }));

const reportedPrepare = async (page, spec) => {
  if (!spec.selector) {
    await page.waitForTimeout(SETTLE_MS);
    return { failure: null, element: null };
  }
  if (!(await found(page, spec.selector, spec.timeout))) return { failure: "not_found", element: null };
  const element = await largestOf(page, spec.selector);
  if (!element) return { failure: "not_visible", element: null };
  await centre(element);
  const steady = await settled(page, spec.timeout, () => boxKey(page, spec.selector, element));
  return { failure: steady ? null : "settle", element };
};

const read = ({ selector, element: given, styles, inside, mustNotChange }) => {
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
  const element = given ?? (selector ? document.querySelector(selector) : null);
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

const markupOf = (element) => {
  const opening = (node) => {
    const shallow = node.cloneNode(false).outerHTML;
    return shallow.slice(0, shallow.length - `</${node.localName}>`.length);
  };
  const ancestors = [];
  for (let node = element.parentElement; node && node !== document.documentElement; node = node.parentElement) ancestors.unshift(opening(node));
  return { ancestors, html: element.outerHTML };
};

const frameSource = (element) => {
  if (element.tagName !== "IFRAME") return null;
  return { src: element.hasAttribute("src") ? element.src : null };
};

const largestInside = () => {
  const nodes = [...document.body.querySelectorAll("*")];
  const area = (node) => {
    const rect = node.getBoundingClientRect();
    return rect.width * rect.height;
  };
  const shown = (node) => {
    const style = getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && area(node) > 0;
  };
  const best = nodes.filter(shown).reduce((kept, node) => (kept && area(kept) >= area(node) ? kept : node), null);
  if (!best) return null;
  const rect = best.getBoundingClientRect();
  const tag = best.tagName.toLowerCase();
  const suffix = best.classList.length ? `.${best.classList[0]}` : "";
  const name = best.id ? `${tag}#${best.id}` : `${tag}${suffix}`;
  const box = { x: Math.round(rect.x + scrollX), y: Math.round(rect.y + scrollY), width: Math.round(rect.width), height: Math.round(rect.height) };
  return { name, box };
};

const loadAlone = async (browser, spec, frame) => {
  const context = await browser.newContext({ viewport: { width: frame.width, height: frame.height } });
  await context.route(/.*/, blocker(spec.block));
  try {
    const page = await context.newPage();
    const response = await page.goto(frame.url, { waitUntil: "load", timeout: spec.timeout });
    const status = response ? response.status() : null;
    const largest = status !== null && status >= 400 ? null : await page.evaluate(largestInside);
    return { ...frame, status, largest };
  } catch (error) {
    return { ...frame, status: null, largest: null, error: String(error.message).split("\n")[0] };
  } finally {
    await context.close();
  }
};

const frameOf = async (browser, spec, element, box) => {
  const source = element ? await element.evaluate(frameSource) : null;
  if (!source?.src) return source && { url: null };
  return loadAlone(browser, spec, { url: source.src, width: box.width, height: box.height });
};

const reportedMeasure = async (page, errors, spec) => {
  const { failure, element } = await reportedPrepare(page, spec);
  if (failure) return { failure, element };
  const measured = await page.evaluate(read, { ...spec.query, element });
  const scrolled = await page.evaluate(() => Math.round(scrollY));
  const geometry = { viewport: spec.viewport, ...measured, errors, scrollY: scrolled };
  await pictures(page, geometry, spec.out);
  const markup = element ? await element.evaluate(markupOf) : null;
  return { failure: null, element, geometry, markup };
};

const reportedLoad = async (browser, spec) => {
  const { context, page, errors, status } = await openPage(browser, spec);
  try {
    if (status !== null && status >= 400) return { status, failure: "status" };
    const { element, ...taken } = await reportedMeasure(page, errors, spec);
    const frame = taken.failure ? null : await frameOf(browser, spec, element, taken.geometry.box);
    return { status, ...taken, frame };
  } finally {
    await context.close();
  }
};

const reported = async (spec) => {
  const browser = await chromium.launch();
  try {
    await warm(browser, spec);
    process.stdout.write(`${JSON.stringify(await reportedLoad(browser, spec))}\n`);
  } finally {
    await browser.close();
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

const MODES = { capture, reported };
const [mode, raw] = process.argv.slice(2);
await (mode === "--check" ? check() : MODES[mode](JSON.parse(raw)));
