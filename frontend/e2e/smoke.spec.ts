import { expect, test, type Page } from "@playwright/test";

const SECTIONS = ["Overview", "Changes", "Scanner", "Demand", "Shelf", "Opportunities", "Weekly brief", "Alerts", "Replay"];

async function login(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /sign in|log in/i }).click();
  await expect(page.locator("a.market-card").first()).toBeVisible();
}

/** The sidebar is always visible on desktop and an off-canvas drawer on mobile, so open it when needed. */
async function openSection(page: Page, name: string) {
  await page.locator(".topbar").waitFor(); // the shell renders after the route changes; check the menu only once it exists
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("navigation", { name: "Market sections" }).getByRole("link", { name, exact: true }).click();
}

async function openFirstMarket(page: Page) {
  await login(page);
  await page.locator("a.market-card").first().click();
  await expect(page).toHaveURL(/\/markets\/\d+$/);
}

test("redirects to login when signed out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("every section opens without a console error or horizontal overflow", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await openFirstMarket(page);

  for (const name of SECTIONS) {
    await openSection(page, name);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Market sections" }).getByRole("link", { name, exact: true })).toHaveClass(/active/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${name} scrolls horizontally`).toBeLessThanOrEqual(1);
  }
  expect(errors).toEqual([]);
});

test("switching markets keeps the open section", async ({ page }) => {
  await openFirstMarket(page);
  await openSection(page, "Shelf");
  const switcher = page.getByLabel("Current market");
  const options = await switcher
    .locator("option:not([disabled])")
    .evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value));
  test.skip(options.length < 2, "needs two markets");
  const other = options.find((value) => !page.url().endsWith(`/markets/${value}/shelf`));
  await switcher.selectOption(other!);
  await expect(page).toHaveURL(new RegExp(`/markets/${other}/shelf$`));
});

test("light and dark themes both render the dashboard", async ({ page }) => {
  await openFirstMarket(page);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(background).not.toBe("rgba(0, 0, 0, 0)");
  }
});

test("an unknown market shows a not-found state, not a crash", async ({ page }) => {
  await login(page);
  await page.goto("/markets/99999/shelf");
  await expect(page.getByText("Market not found")).toBeVisible();
});

test("a background scan shows progress and then its result", async ({ page }) => {
  await openFirstMarket(page);
  await page.getByRole("button", { name: "Run live scan" }).click();
  await page.getByRole("button", { name: /scan now/i }).click();
  await expect(page.getByRole("progressbar", { name: "Scan progress" })).toBeVisible();
  await expect(page.getByText("Scan complete.")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("progressbar")).toHaveCount(0);
});

test("the home page is one page with every section, and the sidebar has no market list or new-market link", async ({ page }) => {
  await login(page);
  for (const id of ["#top", "#about", "#offer", "#markets", "#add"]) await expect(page.locator(id)).toBeAttached();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("See the shelf");
  await page.locator("a.market-card").first().click();
  await expect(page).toHaveURL(/\/markets\/\d+$/);
  const sidebar = page.locator("#app-sidebar");
  await expect(sidebar.getByRole("link", { name: "All markets" })).toHaveCount(0);
  await expect(sidebar.getByRole("link", { name: "New market" })).toHaveCount(0);
});
