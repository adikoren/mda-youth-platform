// End-to-end tests (Playwright Test). They drive the real app against the local stack:
//   scripts/local-dev.sh start   (database on 5433, API on 8000, web on 5173)
// Credentials come from the environment, never from the repository:
//   E2E_PASSWORD   the local demo password (demo accounts share it)
//   E2E_ADMIN_PASSWORD (optional) the local admin password; defaults to E2E_PASSWORD
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// A Chromium headless shell already on this machine (installed for Playwright MCP) is used when the bundled
// build is missing. E2E_CHROMIUM overrides the path.
const installedShell = join(homedir(), "Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell");
const chromium = process.env.E2E_CHROMIUM ?? (existsSync(installedShell) ? installedShell : undefined);

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,      // one shared local database: keep the runs sequential
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "e2e-report" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5174", // the disposable e2e stack (scripts/e2e-stack.sh)
    locale: "he-IL",
    launchOptions: chromium ? { executablePath: chromium } : {},
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // The app is mobile-first; the phone project is the primary one.
    { name: "phone", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } } },
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
  ],
});
