import { expect, test } from "@playwright/test";
import { ACCOUNTS, login } from "./helpers";

// A trainee with at least one recorded ride, so the ride selector and the assessment form are present.
async function openTraineeWithRides(page: import("@playwright/test").Page) {
  await page.goto("/manage/trainees/day/4");
  await page.locator("a.card.list-link", { hasNotText: "0 נסיעות" }).first().click();
  await expect(page.getByLabel("סיכום החניך")).toBeVisible();
}

test("the trainee page shows totals, one ride at a time, and the shift summary last", async ({ page }) => {
  await login(page, ACCOUNTS.admin);
  await openTraineeWithRides(page);
  const header = page.getByLabel("סיכום החניך");
  await expect(header.getByText("תאריך עלייה אחרון")).toBeVisible();
  await expect(header.getByRole("button", { name: /הוספת נסיעה/ })).toBeVisible();

  await expect(page.getByLabel("בחירת נסיעה")).toBeVisible();
  // Only one ride's assessment form is on screen at a time.
  await expect(page.getByText("הערכת החונך")).toHaveCount(1);

  // The shift summary is the last content section: after the ride selector in document order.
  const order = await page.evaluate(() => {
    const sel = [...document.querySelectorAll("label")].find((l) => l.textContent?.includes("בחירת נסיעה"));
    const summary = [...document.querySelectorAll("strong")].find((s) => s.textContent?.includes("סיכום חניכה למשמרת"));
    if (!sel || !summary) return "missing";
    return sel.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING ? "summary-after-selector" : "wrong";
  });
  expect(order).toBe("summary-after-selector");
});

test("assessments are dropdowns with a neutral placeholder, not preselected", async ({ page }) => {
  await login(page, ACCOUNTS.admin);
  await openTraineeWithRides(page);
  for (const label of [/לחץ דם/, /דופק/, /נשימות/, /טפסים/]) {
    const field = page.getByLabel(label);
    await expect(field).toHaveValue("");
    await expect(field.locator("option", { hasText: "בחירת הערכה" })).toHaveCount(1);
  }
});
