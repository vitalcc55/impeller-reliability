import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
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
      'cancelClose',
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
      'releaseLocalWorkspace',
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
    await page.evaluate(async () => {
      const api: unknown = Reflect.get(window, 'impeller');
      if (typeof api !== 'object' || api === null) throw new Error('api_missing');
      const system: unknown = Reflect.get(api, 'system');
      if (typeof system !== 'object' || system === null) throw new Error('system_api_missing');
      const confirmClose: unknown = Reflect.get(system, 'confirmClose');
      if (typeof confirmClose !== 'function') throw new Error('confirm_close_missing');
      await Reflect.apply(confirmClose, system, []);
    });
    await expect(page.getByLabel('Название проекта')).toHaveValue('Несохранённый draft');
    const movedProjectPath = join(evidenceRoot, 'Перемещённый проект.irproj');
    const detachedWorkerId = workerProcessIds(mainProcessId)[0];
    if (detachedWorkerId === undefined) throw new Error('detached_worker_process_missing');
    process.kill(detachedWorkerId);
    await expect(page.getByText('Проект отсоединён от worker')).toBeVisible();
    renameSync(projectPath, movedProjectPath);
    await page.getByRole('button', { name: 'Диагностика' }).click();
    await page.getByRole('button', { name: 'Перезапустить ядро' }).click();
    await expect(page.getByRole('dialog', { name: 'Есть несохранённые изменения' })).toBeVisible();
    await page.getByRole('button', { name: 'Перезапустить и сохранить черновик' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await page.getByRole('button', { name: 'Проекты' }).click();
    await expect(page.getByText('Проект отсоединён от worker')).toBeVisible();
    await expect(page.getByText(/Локальный черновик сохранён/u)).toBeVisible();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Несохранённый draft');
    await page.getByRole('button', { name: 'Отказаться от локального черновика' }).click();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Несохранённый draft');
    await expect(page.getByText(/Будет очищена только локальная форма/u)).toBeVisible();
    await page.getByRole('button', { name: 'Удалить только локальный черновик' }).click();
    await expect(page.getByRole('heading', { name: /Проект объединяет испытания/u })).toBeVisible();
    await expect(page.getByText(projectPath)).toBeVisible();
    expect(existsSync(movedProjectPath)).toBe(true);
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await expect(page.getByRole('heading', { name: 'Новый проект' })).toBeVisible();
    await page.screenshot({
      path: resolve(import.meta.dirname, '../../../../.tmp/.codex/evidence/renderer.png'),
      fullPage: true,
    });
  } finally {
    await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test('reattaches the active project when the optional recent-projects store is corrupt', async () => {
  const evidenceRoot = resolve(
    import.meta.dirname,
    '../../../../.tmp/.codex/evidence/m02-e2e-recent',
  );
  const projectPath = join(evidenceRoot, 'Recovery project.irproj');
  const userDataPath = join(evidenceRoot, 'user-data');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(join(userDataPath, 'state'), { recursive: true });
  writeFileSync(join(userDataPath, 'state', 'recent-projects.json'), '{broken', 'utf8');
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
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await expect(page.getByRole('heading', { name: 'Новый проект' })).toBeVisible();
    await page.getByLabel('Название проекта').fill('Локальный черновик recovery');
    const mainProcessId = app.process().pid;
    if (mainProcessId === undefined) throw new Error('electron_main_process_missing');
    const workerId = workerProcessIds(mainProcessId)[0];
    if (workerId === undefined) throw new Error('worker_process_missing');
    process.kill(workerId);
    await expect(page.getByText('Проект отсоединён от worker')).toBeVisible();
    await page.getByRole('button', { name: 'Диагностика' }).click();
    await page.getByRole('button', { name: 'Перезапустить ядро' }).click();
    await page.getByRole('button', { name: 'Перезапустить и сохранить черновик' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await page.getByRole('button', { name: 'Проекты' }).click();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Локальный черновик recovery');
    await expect(page.getByText('Проект отсоединён от worker')).not.toBeVisible();
    await page.getByRole('button', { name: 'Сохранить изменения' }).click();
    await expect(page.getByText('Номер проекта не задан · редакция 2')).toBeVisible();
    const movedProjectPath = join(evidenceRoot, 'Moved recovery project.irproj');
    const attachedWorkerId = workerProcessIds(mainProcessId)[0];
    if (attachedWorkerId === undefined) throw new Error('reattached_worker_process_missing');
    process.kill(attachedWorkerId);
    await expect(page.getByText('Проект отсоединён от worker')).toBeVisible();
    renameSync(projectPath, movedProjectPath);
    await page.getByRole('button', { name: 'Диагностика' }).click();
    await page.getByRole('button', { name: 'Перезапустить ядро' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await page.getByRole('button', { name: 'Проекты' }).click();
    await page.getByRole('button', { name: 'Отказаться от локального черновика' }).click();
    await page.getByRole('button', { name: 'Удалить только локальный черновик' }).click();
    const reopenErrorCode = await page.evaluate(async (path) => {
      const api: unknown = Reflect.get(window, 'impeller');
      if (typeof api !== 'object' || api === null) return 'api_missing';
      const project: unknown = Reflect.get(api, 'project');
      if (typeof project !== 'object' || project === null) return 'project_api_missing';
      const openRecent: unknown = Reflect.get(project, 'openRecent');
      if (typeof openRecent !== 'function') return 'open_recent_missing';
      const result: unknown = await Reflect.apply(openRecent, project, [path]);
      if (typeof result !== 'object' || result === null) return 'result_missing';
      const error: unknown = Reflect.get(result, 'error');
      if (typeof error !== 'object' || error === null) return 'error_missing';
      const code: unknown = Reflect.get(error, 'code');
      return typeof code === 'string' ? code : 'code_missing';
    }, projectPath);
    expect(reopenErrorCode).toBe('storage_error');
  } finally {
    await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test('closes when the renderer crashes after acknowledging a dirty close request', async () => {
  const evidenceRoot = resolve(
    import.meta.dirname,
    '../../../../.tmp/.codex/evidence/m02-e2e-close',
  );
  const userDataPath = join(evidenceRoot, 'user-data');
  const projectPath = join(evidenceRoot, 'close-after-ack.irproj');
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
  let exited = false;
  try {
    const page = await app.firstWindow();
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await page.getByLabel('Название проекта').fill('Несохранённый close ACK draft');
    const exitPromise = new Promise<void>((resolveExit) => {
      app.process().once('exit', () => resolveExit());
    });
    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.close();
    });
    await expect(page.getByRole('dialog', { name: 'Есть несохранённые изменения' })).toBeVisible();
    await app.evaluate(async ({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      if (window === undefined) throw new Error('browser_window_missing');
      const rendererGone = new Promise<void>((resolveGone) => {
        window.webContents.once('render-process-gone', () => resolveGone());
      });
      window.webContents.forcefullyCrashRenderer();
      await rendererGone;
    });
    await Promise.race([
      exitPromise,
      new Promise<never>((_resolve, reject) =>
        setTimeout(() => reject(new Error('renderer_crash_close_timeout')), 5_000),
      ),
    ]);
    exited = true;
  } finally {
    if (!exited) await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test('finishes an accepted save before closing the application', async () => {
  const evidenceRoot = resolve(
    import.meta.dirname,
    '../../../../.tmp/.codex/evidence/m02-e2e-save-close',
  );
  const userDataPath = join(evidenceRoot, 'user-data');
  const projectPath = join(evidenceRoot, 'save-before-close.irproj');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(evidenceRoot, { recursive: true });
  const launchEnvironment = {
    ...process.env,
    NODE_ENV: 'test',
    IMPELLER_AUTOMATED_PROJECT_PATH: projectPath,
    IMPELLER_TEST_USER_DATA: userDataPath,
  };
  const firstApp = await electron.launch({
    args: [join(resolve(import.meta.dirname, '../..'), 'out/main/index.js')],
    cwd: resolve(import.meta.dirname, '../../../..'),
    env: launchEnvironment,
  });
  let firstExited = false;
  try {
    const page = await firstApp.firstWindow();
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await page.getByLabel('Название проекта').fill('Сохранено перед закрытием');
    const exitPromise = new Promise<void>((resolveExit) => {
      firstApp.process().once('exit', () => resolveExit());
    });
    await page.getByRole('button', { name: 'Сохранить изменения' }).click();
    await firstApp.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.close();
    });
    await Promise.race([
      exitPromise,
      new Promise<never>((_resolve, reject) =>
        setTimeout(() => reject(new Error('inflight_save_close_timeout')), 10_000),
      ),
    ]);
    firstExited = true;
  } finally {
    if (!firstExited) await firstApp.close();
  }

  const secondApp = await electron.launch({
    args: [join(resolve(import.meta.dirname, '../..'), 'out/main/index.js')],
    cwd: resolve(import.meta.dirname, '../../../..'),
    env: launchEnvironment,
  });
  try {
    const page = await secondApp.firstWindow();
    await page.getByRole('button', { name: /Сохранено перед закрытием/u }).click();
    await expect(page.getByLabel('Название проекта')).toHaveValue('Сохранено перед закрытием');
  } finally {
    await secondApp.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test('closes when the renderer reloads after acknowledging a dirty close request', async () => {
  const evidenceRoot = resolve(
    import.meta.dirname,
    '../../../../.tmp/.codex/evidence/m02-e2e-close-reload',
  );
  const userDataPath = join(evidenceRoot, 'user-data');
  const projectPath = join(evidenceRoot, 'close-after-reload.irproj');
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
  let exited = false;
  try {
    const page = await app.firstWindow();
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await page.getByLabel('Название проекта').fill('Несохранённый reload draft');
    const exitPromise = new Promise<void>((resolveExit) => {
      app.process().once('exit', () => resolveExit());
    });
    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.close();
    });
    await expect(page.getByRole('dialog', { name: 'Есть несохранённые изменения' })).toBeVisible();
    void page.reload().catch(() => undefined);
    await Promise.race([
      exitPromise,
      new Promise<never>((_resolve, reject) =>
        setTimeout(() => reject(new Error('renderer_reload_close_timeout')), 5_000),
      ),
    ]);
    exited = true;
  } finally {
    if (!exited) await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test('closes within a bound when the renderer cannot acknowledge the close request', async () => {
  const evidenceRoot = resolve(
    import.meta.dirname,
    '../../../../.tmp/.codex/evidence/m02-e2e-hang',
  );
  const userDataPath = join(evidenceRoot, 'user-data');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(evidenceRoot, { recursive: true });
  const app = await electron.launch({
    args: [join(resolve(import.meta.dirname, '../..'), 'out/main/index.js')],
    cwd: resolve(import.meta.dirname, '../../../..'),
    env: {
      ...process.env,
      NODE_ENV: 'test',
      IMPELLER_TEST_USER_DATA: userDataPath,
    },
  });
  let exited = false;
  try {
    const page = await app.firstWindow();
    await page.evaluate(() => {
      setTimeout(() => {
        for (;;) {
          // Deliberately blocks the renderer to prove the Main-owned close fallback.
        }
      }, 0);
    });
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    const exitPromise = new Promise<void>((resolveExit) => {
      app.process().once('exit', () => resolveExit());
    });
    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.close();
    });
    await Promise.race([
      exitPromise,
      new Promise<never>((_resolve, reject) =>
        setTimeout(() => reject(new Error('renderer_unresponsive_close_timeout')), 5_000),
      ),
    ]);
    exited = true;
  } finally {
    if (!exited) await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});
