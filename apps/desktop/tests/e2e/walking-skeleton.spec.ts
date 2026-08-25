import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
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
  const evidenceRoot = resolve(import.meta.dirname, '../../../../.tmp/.codex/evidence/m02-e2e');
  const projectPath = join(evidenceRoot, 'Проект с пробелами.irproj');
  const userDataPath = join(evidenceRoot, 'user-data');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(evidenceRoot, { recursive: true });
  const app = await electron.launch({
    args: [join(resolve(import.meta.dirname, '../..'), 'out/main/index.js')],
    cwd: resolve(import.meta.dirname, '../../../..'),
    env: {
      ...process.env,
      NODE_ENV: 'test',
      IMPELLER_AUTOMATED_PROJECT_PATH: projectPath,
      IMPELLER_TEST_USER_DATA: userDataPath,
    },
  });
  try {
    const page = await app.firstWindow();
    await expect(page.getByRole('heading', { name: /Проект объединяет испытания/u })).toBeVisible();
    expect(await page.evaluate(() => Reflect.has(window, 'process'))).toBe(false);
    expect(await page.evaluate(() => Reflect.has(window, 'require'))).toBe(false);
    expect(
      await page.evaluate(() => {
        const api: unknown = Reflect.get(window, 'impeller');
        return typeof api === 'object' && api !== null ? Reflect.ownKeys(api) : [];
      }),
    ).toEqual(['system', 'project']);
    expect(
      await page.evaluate(() => {
        const api: unknown = Reflect.get(window, 'impeller');
        if (typeof api !== 'object' || api === null) return [];
        const system: unknown = Reflect.get(api, 'system');
        return typeof system === 'object' && system !== null ? Reflect.ownKeys(system).sort() : [];
      }),
    ).toEqual([
      'confirmClose',
      'getStatus',
      'openLog',
      'ping',
      'restart',
      'subscribeCloseRequested',
      'subscribeStatus',
    ]);
    expect(
      await page.evaluate(() => {
        const api: unknown = Reflect.get(window, 'impeller');
        if (typeof api !== 'object' || api === null) return [];
        const project: unknown = Reflect.get(api, 'project');
        return typeof project === 'object' && project !== null
          ? Reflect.ownKeys(project).sort()
          : [];
      }),
    ).toEqual([
      'close',
      'create',
      'createBackup',
      'getOverview',
      'listRecent',
      'open',
      'openRecent',
      'updateMetadata',
    ]);

    await page.getByRole('button', { name: 'Диагностика' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await expect(page.getByText('Готов', { exact: true })).toBeVisible();
    await expect(page.getByText('ok', { exact: true })).toBeVisible();

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

    await page.getByRole('button', { name: 'Проекты' }).click();
    await expect(page.getByRole('button', { name: 'Проекты', exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await expect(page.getByRole('heading', { name: 'Новый проект' })).toBeVisible();
    await page.getByLabel('Название проекта').fill('Проект надёжности РК');
    await page.getByLabel('Номер проекта').fill('ИР-2026-001');
    await page.getByLabel('Описание').fill('Проверка packaged project container workflow.');
    await page.getByRole('combobox', { name: 'Статус' }).click();
    await page.getByRole('option', { name: 'В работе' }).click();
    await page.getByRole('button', { name: 'Сохранить изменения' }).click();
    await expect(page.getByText('Изменения сохранены. Редакция 2.')).toBeVisible();
    await expect(page.getByText('ИР-2026-001 · редакция 2')).toBeVisible();
    await page.getByRole('button', { name: 'Закрыть проект' }).click();
    await expect(page.getByText('Проект закрыт. Данные сохранены в его контейнере.')).toBeVisible();
    await page.getByRole('button', { name: /Проект надёжности РК/u }).click();
    await expect(page.getByRole('heading', { name: 'Проект надёжности РК' })).toBeVisible();
    await expect(page.getByLabel('Номер проекта')).toHaveValue('ИР-2026-001');
    await page.getByLabel('Название проекта').fill('Несохранённый draft');
    await page.getByRole('button', { name: 'Закрыть проект' }).click();
    await expect(page.getByText('Есть несохранённые изменения')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Проект надёжности РК' })).toBeVisible();
    await page.getByRole('button', { name: 'Продолжить редактирование' }).click();
    const dirtyWorkerId = workerProcessIds(mainProcessId)[0];
    if (dirtyWorkerId === undefined) throw new Error('dirty_worker_process_missing');
    process.kill(dirtyWorkerId);
    await expect(page.getByText('Проект отсоединён от worker')).toBeVisible();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Несохранённый draft');
    await page.getByRole('button', { name: 'Диагностика' }).click();
    await page.getByRole('button', { name: 'Перезапустить ядро' }).click();
    await expect(page.getByRole('dialog', { name: 'Есть несохранённые изменения' })).toBeVisible();
    await page.getByRole('button', { name: 'Перезапустить и сохранить черновик' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await page.getByRole('button', { name: 'Проекты' }).click();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Несохранённый draft');
    await expect(
      page.getByText('Ядро перезапущено. Несохранённый черновик сохранён.'),
    ).toBeVisible();

    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.close();
    });
    await expect(page.getByRole('dialog', { name: 'Есть несохранённые изменения' })).toBeVisible();
    await expect(page.getByText(/Закрыть приложение без сохранения/u)).toBeVisible();
    await page.getByRole('button', { name: 'Продолжить редактирование' }).click();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Несохранённый draft');
    await page.getByLabel('Название проекта').fill('Проект надёжности РК');
    await page.screenshot({
      path: resolve(import.meta.dirname, '../../../../.tmp/.codex/evidence/renderer.png'),
      fullPage: true,
    });
  } finally {
    await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});
