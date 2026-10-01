import { randomUUID } from 'node:crypto';
import { mkdirSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication } from '@playwright/test';
import type { MaterialIdentity } from '@impeller-reliability/contracts';

const root = resolve(import.meta.dirname, '../../../..');
function launch(directory: string): Promise<ElectronApplication> {
  return electron.launch({
    args: [join(root, 'apps/desktop/out/main/index.js')],
    cwd: root,
    env: {
      ...process.env,
      NODE_ENV: 'test',
      IMPELLER_AUTOMATED_PROJECT_PATH: join(directory, 'materials.irproj'),
      IMPELLER_AUTOMATED_R130RUN_PATH: join(
        root,
        'fixtures/contracts/r130run/v1/source-materials/protocol_revision_2.r130run',
      ),
      IMPELLER_TEST_USER_DATA: join(directory, 'user-data'),
    },
  });
}
async function importMaterial(app: ElectronApplication): Promise<MaterialIdentity> {
  const page = await app.firstWindow();
  await page.getByRole('button', { name: 'Создать проект', exact: true }).click();
  await page.getByRole('button', { name: 'Результаты R130SH', exact: true }).click();
  await page.getByRole('button', { name: 'Импортировать результат R130SH', exact: true }).click();
  await expect(page.getByText('Импорт завершён', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Проверить и показать материалы', exact: true }),
  ).toBeEnabled();
  return page.evaluate(async () => {
    const api = window.impeller;
    if (api === undefined) throw new Error('preload_missing');
    const project = await api.project.getOverview();
    const sources = await api.importedRun.list();
    if (!project.ok || !sources.ok || sources.result[0] === undefined)
      throw new Error('source_missing');
    const source = sources.result[0];
    return {
      kind: 'protocol',
      materialId: '2',
      origin: {
        projectId: project.result.projectId,
        localImportId: source.localImportId,
        packageId: source.packageId,
        runId: source.runId,
        exportRevision: source.exportRevision,
        outerPackageSha256: source.outerPackageSha256,
      },
    };
  });
}

test('recovers native copies across application processes after decline and consent', async () => {
  const directory = join(root, '.tmp/.codex/evidence/material-capacity-e2e');
  rmSync(directory, { recursive: true, force: true });
  mkdirSync(directory, { recursive: true });
  const first = await launch(directory);
  let selected: MaterialIdentity;
  try {
    selected = await importMaterial(first);
    await first.evaluate(({ shell }) => {
      shell.openPath = () => Promise.resolve('');
    });
    const page = await first.firstWindow();
    const result = await page.evaluate(async (identity: MaterialIdentity) => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_missing');
      for (let index = 0; index < 64; index += 1) {
        const result = await api.importedRun.openMaterial({
          identity,
          operationId: crypto.randomUUID(),
        });
        if (!result.ok || !result.result.opened) throw new Error(`open_failed:${index}`);
      }
      return 64;
    }, selected);
    expect(result).toBe(64);
  } finally {
    await first.close();
  }
  const second = await launch(directory);
  try {
    await second.evaluate(({ shell }) => {
      shell.openPath = () => Promise.resolve('');
    });
    const page = await second.firstWindow();
    await page.getByRole('button', { name: 'Открыть проект', exact: true }).click();
    await page.getByRole('button', { name: 'Результаты R130SH', exact: true }).click();
    await page.getByRole('button', { name: 'Проверить и показать материалы', exact: true }).click();
    const pdf = page.getByRole('button', { name: 'Открыть исходный PDF', exact: true });
    await expect(pdf).toBeEnabled();
    await pdf.click();
    const confirmation = page.getByRole('dialog', {
      name: 'Освободить временные копии материалов?',
    });
    await expect(confirmation).toBeVisible();
    await expect(
      confirmation.getByRole('button', { name: 'Сохранить копии', exact: true }),
    ).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(confirmation).not.toBeVisible();
    await expect(page.getByRole('alert')).toContainText('file_too_large');
    await expect(pdf).toBeFocused();
    await pdf.click();
    await confirmation.getByRole('button', { name: 'Освободить копии', exact: true }).click();
    await expect(confirmation).not.toBeVisible();
    await expect(
      page.getByRole('status').filter({ hasText: 'Открытие передано системной программе' }),
    ).toBeVisible();
  } finally {
    await second.close();
  }
});

test('a pending OS result cannot strand real Main worker restart or quit', async () => {
  const directory = join(root, '.tmp/.codex/evidence/material-handoff-e2e');
  rmSync(directory, { recursive: true, force: true });
  mkdirSync(directory, { recursive: true });
  const app = await launch(directory);
  const mainProcess = app.process();
  let closed = false;
  app.on('close', () => {
    closed = true;
  });
  try {
    const identity = await importMaterial(app);
    await app.evaluate(({ shell }) => {
      shell.openPath = () => {
        process.env['IR_TEST_OS_REQUESTS'] = String(
          Number(process.env['IR_TEST_OS_REQUESTS'] ?? '0') + 1,
        );
        return new Promise<string>(() => {});
      };
    });
    const page = await app.firstWindow();
    const opening = page.evaluate(
      async (identity: MaterialIdentity) =>
        window.impeller?.importedRun.openMaterial({ identity, operationId: crypto.randomUUID() }),
      identity,
    );
    await expect.poll(() => app.evaluate(() => process.env['IR_TEST_OS_REQUESTS'])).toBe('1');
    const restarted = await page.evaluate(async () => window.impeller?.system.restart());
    expect(restarted).toMatchObject({ workerStatus: 'ready' });
    expect(await opening).toMatchObject({
      ok: false,
      error: { code: 'material_open_unconfirmed' },
    });
    // This direct Main restart bypasses App's renderer reattach orchestration.
    const reattached = await page.evaluate(async () => window.impeller?.project.open());
    expect(reattached).toMatchObject({
      ok: true,
      result: { projectId: identity.origin.projectId },
    });
    const closing = page.evaluate(
      async (command) => window.impeller?.importedRun.openMaterial(command),
      { identity, operationId: randomUUID() },
    );
    // Closing the renderer destroys this IPC wait; consume that transport result.
    void closing.catch(() => undefined);
    await expect.poll(() => app.evaluate(() => process.env['IR_TEST_OS_REQUESTS'])).toBe('2');
    const exited = app.waitForEvent('close');
    await app.evaluate(({ app }) => {
      setImmediate(() => app.quit());
    });
    await exited;
    expect(mainProcess.exitCode).toBe(0);
  } finally {
    if (!closed) await app.close();
  }
});
