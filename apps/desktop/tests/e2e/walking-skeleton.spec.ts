import { execFileSync } from 'node:child_process';
import { join, resolve } from 'node:path';

import { _electron as electron, expect, test } from '@playwright/test';

function workerProcessIds(parentProcessId: number): readonly number[] {
  const command = `$owned = [System.Collections.Generic.HashSet[int]]::new(); $owned.Add(${String(parentProcessId)}) | Out-Null; $snapshot = @(Get-CimInstance Win32_Process); $changed = $true; while ($changed) { $changed = $false; foreach ($item in $snapshot) { if ($owned.Contains([int]$item.ParentProcessId) -and $owned.Add([int]$item.ProcessId)) { $changed = $true } } }; $workers = @($snapshot | Where-Object { $owned.Contains([int]$_.ProcessId) -and $_.CommandLine -match 'impeller_reliability\\.worker\\.main' }); $workerIds = [System.Collections.Generic.HashSet[int]]::new(); foreach ($worker in $workers) { $workerIds.Add([int]$worker.ProcessId) | Out-Null }; @($workers | Where-Object { -not $workerIds.Contains([int]$_.ParentProcessId) } | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress`;
  const output = execFileSync('pwsh.exe', ['-NoProfile', '-Command', command], {
    encoding: 'utf8',
  }).trim();
  if (output === '') return [];
  const parsed: unknown = JSON.parse(output);
  if (typeof parsed === 'number') return [parsed];
  if (Array.isArray(parsed) && parsed.every((value) => typeof value === 'number')) return parsed;
  throw new Error('unexpected_worker_process_query_result');
}

test('renderer reflects worker failure and controlled restart through the narrow preload API', async () => {
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
    await expect(page.getByText('Готов', { exact: true })).toBeVisible();
    await expect(page.getByText('ok', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => Reflect.has(window, 'process'))).toBe(false);
    expect(await page.evaluate(() => Reflect.has(window, 'require'))).toBe(false);
    expect(
      await page.evaluate(() => {
        const api: unknown = Reflect.get(window, 'impeller');
        return typeof api === 'object' && api !== null ? Reflect.ownKeys(api) : [];
      }),
    ).toEqual(['system']);
    expect(
      await page.evaluate(() => {
        const api: unknown = Reflect.get(window, 'impeller');
        if (typeof api !== 'object' || api === null) return [];
        const system: unknown = Reflect.get(api, 'system');
        return typeof system === 'object' && system !== null ? Reflect.ownKeys(system).sort() : [];
      }),
    ).toEqual(['getStatus', 'openLog', 'ping', 'restart', 'subscribeStatus']);

    const mainProcessId = app.process().pid;
    if (mainProcessId === undefined) throw new Error('electron_main_process_missing');
    await expect.poll(() => workerProcessIds(mainProcessId)).toHaveLength(1);
    const initialWorkerId = workerProcessIds(mainProcessId)[0];
    if (initialWorkerId === undefined) throw new Error('worker_process_missing');
    process.kill(initialWorkerId);

    await expect(page.getByText('Недоступен', { exact: true })).toBeVisible();
    await expect(page.getByText(/Worker недоступен/u)).toBeVisible();
    await page.getByRole('button', { name: 'Перезапустить ядро' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await expect(page.getByText('Готов', { exact: true })).toBeVisible();
    await expect.poll(() => workerProcessIds(mainProcessId)).toHaveLength(1);
    expect(workerProcessIds(mainProcessId)[0]).not.toBe(initialWorkerId);

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
