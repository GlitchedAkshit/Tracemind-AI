import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

const nav = (page: Page, name: string) =>
  page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name, exact: true });

function trackConsoleErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(err.message));
  return errors;
}

test("demo: overview → incident → evidence → assistant → report", async ({ page }) => {
  const errors = trackConsoleErrors(page);
  await page.goto("/login");
  await page.getByRole("button", { name: /explore the synthetic demo project/i }).click();

  await expect(page.getByText("synthetic demo data").first()).toBeVisible();
  await expect(page.getByText("active incidents under investigation")).toBeVisible();
  await expect(page.locator("[data-particle-tier]").first()).toHaveAttribute("data-particle-tier", /fluid|cpu|static/);

  await nav(page, "Incidents").click();
  await expect(page.getByRole("heading", { name: "Detected incidents" })).toBeVisible();
  const first = page.locator("tbody tr a").first();
  await first.click();

  await expect(page.getByText(/ranking score — relative, not a probability/i)).toBeVisible();
  await expect(page.getByText("supporting evidence").first()).toBeVisible();

  // every evidence reference opens the underlying telemetry event
  await page.locator("section[aria-label^='supporting evidence'] button").first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("observed telemetry · not an inference")).toBeVisible();
  await expect(drawer.getByText("timestamp")).toBeVisible();
  await expect(drawer.getByText("service", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "What changed immediately before the first failure?" }).click();
  await expect(page.getByText(/Q › What changed immediately before the first failure\?/)).toBeVisible();
  await expect(page.getByText(/deterministic writer|anthropic\//).first()).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /export report/i }).click();
  expect((await download).suggestedFilename()).toMatch(/^tracemind-.*\.md$/);

  await nav(page, "Dependencies").click();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();

  expect(errors.filter((e) => !/favicon/i.test(e))).toEqual([]);
});

test("register → create project → upload telemetry → incidents detected", async ({ page }) => {
  const errors = trackConsoleErrors(page);
  const email = `e2e-${Date.now()}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: /no account\? register/i }).click();
  await page.getByLabel("display name").fill("E2E User");
  await page.getByLabel("email").fill(email);
  await page.getByLabel(/password/).fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();

  await nav(page, "Settings").click();
  await page.getByLabel("name", { exact: true }).fill("E2E Shop");
  await page.getByRole("button", { name: "create project" }).click();
  await expect(page.getByRole("banner").getByText("E2E Shop")).toBeVisible();

  await nav(page, "Telemetry").click();
  const sample = path.join(__dirname, "..", "public", "samples", "shopflow-sample.jsonl");
  await page.locator("#file-input").setInputFiles(sample);
  await page.getByRole("button", { name: /preview & validate/i }).click();
  await expect(page.getByText(/normalised records/i)).toBeVisible();
  await page.getByRole("button", { name: /3 · import/i }).click();
  await expect(page.getByText("view incidents →")).toBeVisible({ timeout: 60_000 });

  await page.getByText("view incidents →").click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.locator("tbody tr a").first().click();
  await page.getByRole("button", { name: /run analysis/i }).click();
  await expect(page.getByText(/ranking score — relative, not a probability/i)).toBeVisible();
  await expect(page.locator("ol li button").first()).toContainText("#1");

  expect(errors.filter((e) => !/favicon/i.test(e))).toEqual([]);
});

test("malformed upload reports rejected rows instead of dropping them", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: /no account\? register/i }).click();
  await page.getByLabel("display name").fill("E2E Malformed");
  await page.getByLabel("email").fill(`e2e-bad-${Date.now()}@example.com`);
  await page.getByLabel(/password/).fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await nav(page, "Settings").click();
  await page.getByLabel("name", { exact: true }).fill("Malformed");
  await page.getByRole("button", { name: "create project" }).click();
  await nav(page, "Telemetry").click();
  await page.locator("#file-input").setInputFiles(path.join(__dirname, "..", "public", "samples", "malformed-sample.jsonl"));
  await page.getByRole("button", { name: /preview & validate/i }).click();
  await expect(page.getByText(/rejected rows/i).first()).toBeVisible();
  await expect(page.getByText(/invalid JSON/)).toBeVisible();
  await expect(page.getByText(/missing service/).first()).toBeVisible();
});

const PAGES: [string, string][] = [
  ["/", "active incidents under investigation"],
  ["/ingest", "Upload and normalise telemetry"],
  ["/services", "Topology derived from spans and declared dependencies"],
  ["/incidents", "Detected incidents"],
  ["/evaluation", "Benchmarks on synthetic fault scenarios with known causes"],
  ["/reports", "Generated investigation reports"],
  ["/settings", "Projects, environments, analysis and access"],
];

async function signInToDemo(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /explore the synthetic demo project/i }).click();
  await expect(page.getByText("active incidents under investigation")).toBeVisible();
}

test("every page opens with no console errors", async ({ page }) => {
  const errors = trackConsoleErrors(page);
  await signInToDemo(page);
  for (const [route, marker] of PAGES) {
    await page.goto(route);
    await expect(page.getByText(marker).first(), route).toBeVisible();
  }
  await page.goto("/incidents");
  await page.locator("tbody tr a").first().click();
  await expect(page.getByText(/ranking score — relative, not a probability/i)).toBeVisible();
  expect(errors).toEqual([]);
});

test("no page scrolls sideways at phone width", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(await overflow(), "/login").toBeLessThanOrEqual(1);
  await signInToDemo(page);
  for (const [route, marker] of PAGES) {
    await page.goto(route);
    await expect(page.getByText(marker).first(), route).toBeVisible();
    expect(await overflow(), route).toBeLessThanOrEqual(1);
  }
  // the header is tight at this width: the demo badge must not run into the selectors
  const badge = await page.getByTestId("demo-badge").boundingBox();
  const selector = await page.getByRole("combobox", { name: "Environment" }).boundingBox();
  expect(badge!.x + badge!.width).toBeLessThanOrEqual(selector!.x);
  await page.goto("/incidents");
  await page.locator("tbody tr a").first().click();
  await expect(page.getByText(/ranking score — relative, not a probability/i)).toBeVisible();
  expect(await overflow(), "incident workspace").toBeLessThanOrEqual(1);
});
