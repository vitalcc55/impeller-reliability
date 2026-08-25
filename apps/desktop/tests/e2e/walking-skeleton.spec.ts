import { _electron as electron, expect, test } from '@playwright/test';
import { join, resolve } from 'node:path';

test('renderer sees only the narrow preload API and displays worker health', async () => {
  const app = await electron.launch({
    args: [join(resolve(import.meta.dirname, '../..'), 'out/main/index.js')],
    cwd: resolve(import.meta.dirname, '../../../..'),
    env: { ...process.env, NODE_ENV: 'test' },
  });
  try {
    const page = await app.firstWindow();
    await expect(
      page.getByRole('heading', { name: /Калькулятор показателей надёжности/u }),
    ).toBeVisible();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await expect(page.getByText('ready', { exact: true })).toBeVisible();
    await expect(page.getByText('ok', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => Reflect.has(window, 'process'))).toBe(false);
    expect(await page.evaluate(() => Reflect.has(window, 'require'))).toBe(false);
    expect(
      await page.evaluate(() => {
        const api: unknown = Reflect.get(window, 'impeller');
        return typeof api === 'object' && api !== null ? Reflect.ownKeys(api) : [];
      }),
    ).toEqual(['system']);
    await page.getByRole('button', { name: 'Проверить связь' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await page.screenshot({
      path: resolve(import.meta.dirname, '../../../../.tmp/.codex/evidence/renderer.png'),
      fullPage: true,
    });
  } finally {
    await app.close();
  }
});
