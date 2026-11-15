import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  use: { baseURL: "http://127.0.0.1:8001", trace: "retain-on-failure" },
  webServer: {
    command: "../../.venv/bin/python ../../scripts/e2e_server.py",
    url: "http://127.0.0.1:8001/api/v1/system/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
  projects: [
    {
      name: "desktop-chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(existsSync("/Applications/Google Chrome.app")
          ? { channel: "chrome" }
          : {}),
      },
    },
  ],
  reporter: [
    ["list"],
    ["json", { outputFile: "../../runtime/e2e-results.json" }],
  ],
});
