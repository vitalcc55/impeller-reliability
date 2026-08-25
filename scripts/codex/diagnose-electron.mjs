import { _electron as electron } from '@playwright/test';
import { join, resolve } from 'node:path';
import process from 'node:process';

const repositoryRoot = resolve(import.meta.dirname, '../..');
const application = await electron.launch({
  args: [join(repositoryRoot, 'apps/desktop/out/main/index.js')],
  cwd: repositoryRoot,
  env: { ...process.env, NODE_ENV: 'diagnostic' },
});

try {
  const page = await application.firstWindow();
  const consoleEvents = [];
  const pageErrors = [];
  page.on('console', (message) =>
    consoleEvents.push({ type: message.type(), text: message.text() }),
  );
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  await page.waitForTimeout(1_000);
  process.stdout.write(
    `${JSON.stringify(
      {
        schemaVersion: 1,
        url: page.url(),
        title: await page.title(),
        body: await page.locator('body').innerHTML(),
        consoleEvents,
        pageErrors,
      },
      null,
      2,
    )}\n`,
  );
} finally {
  await application.close();
}
