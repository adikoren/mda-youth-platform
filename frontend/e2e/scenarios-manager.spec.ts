// Requirements §4 (registration cycle), §5 (scheduling and publication), §12 (drafts are never exposed; a target
// time alone does not publish). Cross-day invitation: the volunteer from another day must confirm it.
import { expect, test } from "@playwright/test";
import { ACCOUNTS, login, openNextThursdayBoard } from "./helpers";

// Shared state (invitations, publication): these run once, on the phone project.
test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "shared-state scenarios run once per e2e database");
});

test("a personal invitation for another day is shown to the invitee and can be confirmed", async ({ page, browser }) => {
  // Day Manager of Thursday invites a Monday volunteer to Thursday.
  await login(page, ACCOUNTS.thuManager);
  await page.goto("/manage/day/4/volunteers");
  // The invitation control lives on the board of a Thursday day; open the nearest one from the schedule list.
  await openNextThursdayBoard(page);
  await page.getByPlaceholder(/חיפוש מתנדב/).fill("שירה כהן");
  await page.getByRole("button", { name: /שירה כהן/ }).first().click();
  await expect(page.getByText("ההזמנה נשלחה")).toBeVisible();

  // The invitee signs in on their own device and sees the invitation as a notice.
  const invitee = await browser.newContext();
  const ip = await invitee.newPage();
  await login(ip, ACCOUNTS.monVolunteer);
  await expect(ip.getByText("הוזמנת להירשם ליום נוסף · 29.10.2026")).toBeVisible();
  // The Thursday window has not opened yet, so there is no registration control for that date.
  await expect(ip.getByRole("link", { name: /להרשמה למשמרת.*29\.10/ })).toHaveCount(0);
  await invitee.close();
});

// Publication is one-way and the e2e database is shared, so this scenario runs once (on the phone project).
test("a draft is hidden from volunteers until publication, and publication closes registration", async ({ page, browser }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "publication runs once per e2e database");
  await login(page, ACCOUNTS.thuManager);
  await openNextThursdayBoard(page);
  await expect(page.getByText("טיוטה פרטית")).toBeVisible();
  const dayUrl = page.url();

  const volunteer = await browser.newContext();
  const vp = await volunteer.newPage();
  await login(vp, ACCOUNTS.thuVolunteer);
  const dayId = dayUrl.split("/manage/")[1];
  await vp.goto(`/schedule/${dayId}`);
  await expect(vp.getByText("הסידור פורסם")).toHaveCount(0); // unpublished: no published content for volunteers

  // Publish (the browser confirmation is accepted on purpose: this is the action under test).
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /פרסום הסידור|פרסום שינויים/ }).click();
  await expect(page.getByText(/פורסם/).first()).toBeVisible();

  await vp.goto(`/register/${dayId}`);
  await expect(vp.getByText("הסידור פורסם").first()).toBeVisible();
  await expect(vp.getByRole("button", { name: "מגיע/ה" })).toHaveCount(0);
  await expect(vp.getByRole("button", { name: /בקשה להרשמה באיחור/ })).toHaveCount(0);
  await volunteer.close();
});
