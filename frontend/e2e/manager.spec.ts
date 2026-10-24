import { expect, test } from "@playwright/test";
import { ACCOUNTS, backButton, login } from "./helpers";

test("the Day Manager hub offers signup approval, and back returns to the hub", async ({ page }) => {
  await login(page, ACCOUNTS.thuManager);
  await page.goto("/manage");
  await page.getByRole("link", { name: /אישור משתמשים/ }).click();
  await expect(page).toHaveURL(/\/manage\/signups$/);
  await expect(page.getByRole("heading", { name: "בקשות הרשמה ממתינות" })).toBeVisible();
  await backButton(page).click();
  await expect(page).toHaveURL(/\/manage$/);
});

test("signup requests show only the manager's own weekday", async ({ page }) => {
  await login(page, ACCOUNTS.thuManager);
  await page.goto("/manage/signups");
  // Either the empty state, or every card requests Thursday. Other days are never listed.
  const cards = page.locator("section.card");
  const empty = page.getByText("אין בקשות הרשמה ממתינות");
  await expect(cards.first().or(empty)).toBeVisible();
  const count = await cards.count();
  for (let i = 0; i < count; i++) {
    await expect(cards.nth(i)).toContainText("יום מבוקש: חמישי");
  }
});

test("trainee search narrows the list, shows a clear empty state, and back restores the search", async ({ page }) => {
  await login(page, ACCOUNTS.thuManager);
  await page.goto("/manage/trainees/day/4");
  const search = page.getByRole("searchbox");
  await search.fill("  zzqx-no-such  ");
  await expect(page.getByText(/לא נמצאו חניכים/)).toBeVisible();
  await search.fill("");
  await expect(page.getByRole("link").first()).toBeVisible();
  // Open a trainee after searching; "חזרה" must restore the day and the search text.
  await search.fill("מ");
  const first = page.locator("a.card.list-link").first();
  await first.click();
  await expect(page).toHaveURL(/\/journal\//);
  await backButton(page).click();
  await expect(page).toHaveURL(/\/manage\/trainees\/day\/4\?q=/);
  await expect(page.getByRole("searchbox")).toHaveValue("מ");
});
