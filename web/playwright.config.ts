import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  timeout: 45000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.SG_E2E_BASE_URL || "http://127.0.0.1:8486",
    headless: true,
    launchOptions: {
      executablePath: process.env.SG_CHROME_PATH || "/usr/bin/google-chrome",
      args: ["--no-sandbox"],
    },
    trace: "retain-on-failure",
  },
});
