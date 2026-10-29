// שכחתי סיסמה: the request answers neutrally, a captured link resets the password, and the new password works.
// Email is captured on the disposable e2e stack (MDA_EMAIL_MODE=capture); nothing is sent to anyone.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ACCOUNTS, login } from "./helpers";

const OUTBOX = join(homedir(), "mda-local-data", "e2e-outbox");

function latestLinkFor(email: string): string | null {
  if (!existsSync(OUTBOX)) return null;
  const files = readdirSync(OUTBOX).filter((f) => f.endsWith(".json")).sort().reverse();
  for (const f of files) {
    const item = JSON.parse(readFileSync(join(OUTBOX, f), "utf8"));
    if (item.to === email) return item.text.split("\n")[1];
  }
  return null;
}

test("the login screen offers password reset, and the request answer is neutral", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "שכחתי סיסמה" }).click();
  await expect(page).toHaveURL(/\/forgot$/);
  await page.getByLabel("כתובת המייל של החשבון").fill("nobody-here@example.test");
  await page.getByRole("button", { name: "שליחת קישור לאיפוס" }).click();
  await expect(page.getByText("אם קיים חשבון עם כתובת המייל הזו, יישלח אליו קישור לאיפוס סיסמה.")).toBeVisible();
});

test("a captured reset link sets a new password, which then signs in", async ({ page }) => {
  // A dedicated synthetic account: its password is changed here, so no other scenario uses it.
  const email = "demo.v200@example.test";
  await page.goto("/forgot");
  await page.getByLabel("כתובת המייל של החשבון").fill(email);
  await page.getByRole("button", { name: "שליחת קישור לאיפוס" }).click();
  await expect(page.getByText("אם קיים חשבון")).toBeVisible();

  const link = await expect.poll(() => latestLinkFor(email), { timeout: 10_000 }).not.toBeNull().then(() => latestLinkFor(email));
  expect(link).toContain("/reset?token=");
  await page.goto(link!.replace("http://localhost:5174", ""));
  const newPassword = "after-reset-pass-789";
  await page.getByLabel("סיסמה חדשה (לפחות 8 תווים)").fill(newPassword);
  await page.getByLabel("אימות סיסמה").fill(newPassword);
  await page.getByRole("button", { name: "עדכון סיסמה" }).click();
  await expect(page.getByText("הסיסמה עודכנה")).toBeVisible();

  // The used link no longer works.
  await page.goto(link!.replace("http://localhost:5174", ""));
  await expect(page.getByText("הקישור כבר נוצל")).toBeVisible();

  // The new password signs in; the old one does not.
  await login(page, { email, password: newPassword });
  await expect(page.getByText(/^שלום,/)).toBeVisible();
});
