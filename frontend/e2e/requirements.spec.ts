// Product requirements §12 (acceptance scenarios) that need several people or a changing clock. Each scenario uses
// its own Thursday so they do not interfere. Time-dependent state is set on the disposable e2e database (db.ts).
import { expect, test, type Page } from "@playwright/test";
import { setWindow, firstTraineeEmailOnWeekday, sql, thursdayWithRegistrations, traineeIdByEmail } from "./db";
import { ACCOUNTS, login, openDayBoard } from "./helpers";

// These scenarios change shared state (invitations, windows, drafts), so they run once, on the phone project.
test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "shared-state scenarios run once per e2e database");
});

const INVITEE = "שירה כהן";            // unique name of demo.v007 (home Monday)
const INVITEE_ACCOUNT = ACCOUNTS.monVolunteer;
const DAY_GUEST = { date: "2026-11-05", text: /5\.11\.26/ };   // invitation, confirmation, guest access
const DAY_LATE = { date: "2026-11-12", text: /12\.11\.26/ };   // late request from another day
// ambulance cancel returns crew to waiting: a Thursday that has registrants (chosen at run time)
const DAY_CREW = { date: thursdayWithRegistrations(["2026-11-05", "2026-11-12"]), text: /./ };

async function invite(page: Page, name: string) {
  await page.getByPlaceholder(/חיפוש מתנדב/).fill(name);
  await page.getByRole("button", { name: new RegExp(name) }).first().click();
  await expect(page.getByText("ההזמנה נשלחה")).toBeVisible();
}

// 1. §12 "guest" and invitation: an invited volunteer from another day confirms availability once the window opens.
test("an invited volunteer from another day confirms availability once the window is open", async ({ page, browser }) => {
  await login(page, ACCOUNTS.thuManager);
  await openDayBoard(page, DAY_GUEST.date);
  await invite(page, INVITEE);
  setWindow(DAY_GUEST.date, "open");

  const ctx = await browser.newContext();
  const ip = await ctx.newPage();
  await login(ip, INVITEE_ACCOUNT);
  await ip.getByRole("link", { name: /להרשמה למשמרת/ }).first().click();
  await ip.getByRole("button", { name: /מגיע\/ה/ }).first().click();
  await expect(ip.getByText("נשמר")).toBeVisible();
  await ip.goto("/");
  await expect(ip.getByText("הוזמנת להירשם")).toHaveCount(0);
  await ctx.close();
});

// 2. §12 "late request" and approval: a volunteer from another day sends a late request; the Day Manager approves it.
test("a late request from a volunteer of another day is approved by the Day Manager", async ({ page, browser }) => {
  await login(page, ACCOUNTS.thuManager);
  await openDayBoard(page, DAY_LATE.date);
  await invite(page, INVITEE);
  setWindow(DAY_LATE.date, "closed");              // closing has passed: only requests are possible now

  const ctx = await browser.newContext();
  const ip = await ctx.newPage();
  await login(ip, INVITEE_ACCOUNT);
  await ip.goto(`/register/${sql(`select id from activity_days where local_date = '${DAY_LATE.date}'`)}`);
  await ip.getByRole("button", { name: "בקשה להרשמה באיחור" }).click();
  await ip.getByRole("textbox").last().fill("אשמח להגיע למשמרת ערב, אני מתפנה מוקדם");
  await ip.getByRole("button", { name: "שליחה לאחראי" }).click();
  await expect(ip.getByText(/הבקשה נשלחה לאחראי/)).toBeVisible();
  await ctx.close();

  // The manager sees the request on the board and approves it.
  await page.goto(page.url());
  await page.reload();
  const card = page.locator("section.card, div.card").filter({ hasText: "אשמח להגיע למשמרת ערב" }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "אישור", exact: true }).click();
  await expect(page.getByText("הבקשה אושרה")).toBeVisible();
});

// 3. §12 "ambulance cancellation returns assigned people to the waiting list without deleting history".
test("cancelling an ambulance returns its crew to the waiting list", async ({ page }) => {
  await login(page, ACCOUNTS.thuManager);
  await openDayBoard(page, DAY_CREW.date);
  // The evening shift is the card with its times (15:00–23:00) and a waiting list.
  const evening = page.locator("div.card").filter({ hasText: "15:00–23:00" }).filter({ hasText: "ממתינים" }).first();
  await expect(evening).toBeVisible();
  // A waiting person is a row that carries the "שבץ…" control.
  const waitingRow = evening.locator(".person").filter({ has: page.locator("select") }).first();
  await expect(waitingRow).toBeVisible();
  const waitingName = (await waitingRow.locator(".name").first().textContent())?.trim() ?? "";
  expect(waitingName.length).toBeGreaterThan(1);
  const assignSelect = waitingRow.locator("select");
  // The first real ambulance option (the empty "שבץ…" placeholder has no value).
  const value = await assignSelect.locator("option[value]:not([value=''])").first().getAttribute("value");
  expect(value).toBeTruthy();
  await assignSelect.selectOption(value!);

  // Ambulance 1 now carries the person; cancel the ambulance (confirmation accepted on purpose).
  await expect(evening.locator(".name").filter({ hasText: waitingName }).first()).toBeVisible(); // now in a crew
  page.once("dialog", (d) => d.accept());
  // In the evening card the first "ביטול" belongs to ambulance 1 (crew rows use "הסרה").
  await evening.getByRole("button", { name: "ביטול", exact: true }).first().click();
  // Back on the waiting list; the cancellation itself stays in the draft's history.
  await expect(evening.locator(".person").filter({ has: page.locator(`.name:text-is("${waitingName}")`) }).first()).toBeVisible();
});

// 4. §12 "privacy": a private personal note is not visible to a manager; a youth manager sees progress, not content.
test("a private trainee note is not shown to the Day Manager of that trainee", async ({ page, browser }) => {
  const trainee = firstTraineeEmailOnWeekday(4);
  const ctx = await browser.newContext();
  const tp = await ctx.newPage();
  await login(tp, { email: trainee, password: process.env.E2E_DATASET_PASSWORD ?? "" });
  await tp.goto("/notes");
  await expect(tp.getByText("סיכומים אישיים")).toBeVisible();
  const text = `הערה פרטית לבדיקה ${Date.now()}`;
  await tp.getByPlaceholder("מה למדתי, מה הרגשתי…").fill(text);
  await tp.getByRole("button", { name: "שמירה" }).click();
  await expect(tp.getByText(text)).toBeVisible();
  await ctx.close();

  await login(page, ACCOUNTS.thuManager);
  await page.goto(`/journal/${traineeIdByEmail(trainee)}`);
  await expect(page.getByText("בחירת נסיעה").or(page.getByText("היומן לא נמצא"))).toBeVisible();
  await expect(page.getByText(text)).toHaveCount(0);
});

test("a youth manager sees trainee progress but not the case content", async ({ page }) => {
  await login(page, { email: "youth.manager@example.test", password: process.env.E2E_PASSWORD ?? "" });
  await page.goto("/manage/trainees/day/4");
  await expect(page.getByText(/נסיעות/).first()).toBeVisible();          // progress is visible
  // Not a link: a youth manager cannot open the journal from the list (journal_access is false).
  await expect(page.locator("a.card.list-link")).toHaveCount(0);
  await page.goto(`/journal/${traineeIdByEmail(firstTraineeEmailOnWeekday(4))}`);
  await expect(page.getByText("בחירת נסיעה")).toHaveCount(0);            // case content is not shown
  await expect(page.getByText("סיכום חניכה למשמרת")).toHaveCount(0);
});
