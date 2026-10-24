import { expect, test } from "@playwright/test";
import { ACCOUNTS, login } from "./helpers";

test("a wrong password is refused with a Hebrew message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("מייל").fill(ACCOUNTS.volunteer.email);
  await page.getByLabel("סיסמה").fill("definitely-wrong-password");
  await page.getByRole("button", { name: "כניסה" }).click();
  await expect(page.getByText("מייל או סיסמה שגויים")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("a volunteer signs in and reaches the home screen", async ({ page }) => {
  await login(page, ACCOUNTS.volunteer);
  await expect(page.getByText(/^שלום,/)).toBeVisible();
});

test("the phone layout has no horizontal scrolling on the home screen", async ({ page }) => {
  await login(page, ACCOUNTS.volunteer);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
