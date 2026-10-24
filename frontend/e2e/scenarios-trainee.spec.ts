// Requirements §7 (trip journal) and §12 (acceptance): adding a ride and writing a personal note.
// These write data, so they run only on the disposable e2e database (scripts/e2e-stack.sh).
import { expect, test } from "@playwright/test";
import { ACCOUNTS, login } from "./helpers";

test("adding a ride counts once and selects the new ride", async ({ page }) => {
  await login(page, ACCOUNTS.admin);
  await page.goto("/manage/trainees/day/4");
  await page.locator("a.card.list-link", { hasNotText: "0 נסיעות" }).first().click();
  const header = page.getByLabel("סיכום החניך");
  // The header reads "<name>" then "<n> נסיעות": the count is the second strong element.
  const countCell = header.locator("strong").nth(1);
  const countBefore = Number((await countCell.textContent()) ?? "0");

  await header.getByRole("button", { name: /הוספת נסיעה/ }).click();
  // Pick the first shift offered for this trainee.
  await page.getByRole("button", { name: /יום .* · (בוקר|ערב|תגבור)$/ }).first().click();
  await expect(page.getByText("נסיעה חדשה במכשיר")).toBeVisible();

  const description = "תרחיש בדיקה e2e: כאב בחזה";
  const field = page.getByLabel("תיאור המקרה");
  await field.fill(description);
  // The text must still be there before saving (a re-render must not clear what the person typed).
  await expect(field).toHaveValue(description);
  await page.getByRole("button", { name: "שמירת תיאור" }).click();

  // The saved ride is counted once: the header total rises by exactly one.
  await expect(countCell).toHaveText(String(countBefore + 1));
  await expect(page.getByLabel("בחירת נסיעה")).toHaveValue(/.+/);
  await expect(page.getByLabel("בחירת נסיעה")).toContainText(description);
});

test("a volunteer writes a personal note and it is still there after reload", async ({ page }) => {
  // Personal notes are for trainees only (the server answers 403 for other levels).
  await login(page, ACCOUNTS.trainee);
  await page.goto("/notes");
  await expect(page.getByText("סיכומים אישיים")).toBeVisible(); // wait for the page to load before typing
  const text = `הערה אישית e2e ${Date.now()}`;
  await page.getByPlaceholder("מה למדתי, מה הרגשתי…").fill(text);
  await page.getByRole("button", { name: "שמירה" }).click();
  await expect(page.getByText(text)).toBeVisible();
  await page.reload();
  await expect(page.getByText(text)).toBeVisible();
});
