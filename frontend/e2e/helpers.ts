import { expect, type Page } from "@playwright/test";
import { dayIdByDate } from "./db";

// Passwords come from the environment. The seed-demo staff accounts share E2E_PASSWORD; the dataset volunteers
// (demo.v*) share E2E_DATASET_PASSWORD (printed once by `seed-dataset`). Both fall back to E2E_PASSWORD.
const STAFF_PASSWORD = process.env.E2E_PASSWORD ?? "";
const DATASET_PASSWORD = process.env.E2E_DATASET_PASSWORD ?? STAFF_PASSWORD;

// Synthetic accounts only (@example.test). Never real people.
export const ACCOUNTS = {
  thuManager: { email: "thu.manager@example.test", password: STAFF_PASSWORD },   // Thursday Day Manager (seed-demo)
  volunteer: { email: "demo.v008@example.test", password: DATASET_PASSWORD },   // Tuesday home day, no manager role
  admin: { email: "admin@example.test", password: STAFF_PASSWORD },
  thuVolunteer: { email: "demo.v004@example.test", password: DATASET_PASSWORD },  // home day Thursday (n%6=4)
  trainee: { email: "demo.v013@example.test", password: DATASET_PASSWORD },     // a dataset trainee (n%20=13)
  monVolunteer: { email: "demo.v007@example.test", password: DATASET_PASSWORD },  // home day Monday; name is unique
} as const;

export async function login(page: Page, account: { email: string; password: string }) {
  if (!account.password) throw new Error("Set E2E_PASSWORD (and E2E_DATASET_PASSWORD) to run the end-to-end tests");
  await page.goto("/login");
  await page.getByLabel("מייל").fill(account.email);
  await page.getByLabel("סיסמה").fill(account.password);
  await page.getByRole("button", { name: "כניסה" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
}

// The Hebrew "back" control of the shell, identified by its accessible name.
export function backButton(page: Page) {
  return page.getByRole("button", { name: "חזרה" });
}

// Opens the board of the nearest Thursday from the schedule list: expand the day row, then "ניהול הסידור".
export async function openNextThursdayBoard(page: Page) {
  await page.goto("/schedule?weekday=4");
  await page.getByRole("button", { name: /יום חמישי/ }).first().click();
  await page.getByRole("link", { name: "ניהול הסידור" }).first().click();
  await expect(page).toHaveURL(/\/manage\/[0-9a-f-]{36}$/);
}

// Opens the board of a day by its date (YYYY-MM-DD). Looked up on the e2e database, so it works for any date.
export async function openDayBoard(page: Page, localDate: string) {
  const id = dayIdByDate(localDate);
  await page.goto(`/manage/${id}`);
  await expect(page.getByText("טיוטה פרטית").or(page.getByText("פורסם")).first()).toBeVisible();
}
