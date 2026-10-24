// Product requirements §12: registration, scheduling and journal screens work on a phone without a wide table.
// Read-only, so it runs on the phone project only.
import { expect, test } from "@playwright/test";
import { dayIdByDate } from "./db";
import { ACCOUNTS, login } from "./helpers";

test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "layout check is for the phone viewport");
});

async function noHorizontalScroll(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("registration, schedule, board and journal screens fit a phone without horizontal scrolling", async ({ page }) => {
  await login(page, ACCOUNTS.thuManager);
  await page.goto("/");
  await noHorizontalScroll(page);
  await page.goto(`/register/${dayIdByDate("2026-11-05")}`);
  await expect(page.getByText("הרשמה למשמרת")).toBeVisible();
  await noHorizontalScroll(page);
  await page.goto("/schedule?weekday=4");
  await noHorizontalScroll(page);
  await page.goto(`/manage/${dayIdByDate("2026-11-19")}`);
  await expect(page.getByText("טיוטה פרטית").or(page.getByText("פורסם")).first()).toBeVisible();
  await noHorizontalScroll(page);
  await page.goto("/manage/trainees/day/4");
  await noHorizontalScroll(page);
});
