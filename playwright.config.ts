import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './apps/desktop/tests/e2e',
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: '.tmp/.codex/evidence/playwright.json' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
