import { mkdirSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { _electron as electron, expect, test } from '@playwright/test';
import type { MaterialOrigin } from '@impeller-reliability/contracts';

test('shows producer inspections, photos and saved protocol revisions before binding and after reopen', async () => {
  const root = resolve(import.meta.dirname, '../../../..');
  const evidenceRoot = join(root, '.tmp/.codex/evidence/producer-material-e2e');
  const fixtures = join(root, 'fixtures/contracts/r130run/v1/source-materials');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(evidenceRoot, { recursive: true });
  const app = await electron.launch({
    args: [join(root, 'apps/desktop/out/main/index.js')],
    cwd: root,
    env: {
      ...process.env,
      NODE_ENV: 'test',
      IMPELLER_AUTOMATED_PROJECT_PATH: join(evidenceRoot, 'materials.irproj'),
      IMPELLER_AUTOMATED_R130RUN_PATH: join(fixtures, 'protocol_revision_1.r130run'),
      IMPELLER_TEST_USER_DATA: join(evidenceRoot, 'user-data'),
    },
  });
  try {
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.getByRole('button', { name: 'Создать проект', exact: true }).click();
    await page.getByRole('button', { name: 'Результаты R130SH', exact: true }).click();
    await page.getByRole('button', { name: 'Импортировать результат R130SH', exact: true }).click();
    await expect(page.getByText('Импорт завершён', { exact: true })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Проверить и показать материалы', exact: true }),
    ).toBeEnabled();
    const initial = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_missing');
      const project = await api.project.getOverview();
      const sources = await api.importedRun.list();
      if (!project.ok || !sources.ok || sources.result[0] === undefined)
        throw new Error('source_missing');
      const source = sources.result[0];
      const origin = {
        projectId: project.result.projectId,
        localImportId: source.localImportId,
        packageId: source.packageId,
        runId: source.runId,
        exportRevision: source.exportRevision,
        outerPackageSha256: source.outerPackageSha256,
      };
      const inspections = await api.importedRun.listInspectionPage({ origin });
      if (!inspections.ok) throw new Error('inspections_missing');
      const inspectionId = inspections.result.items.find(
        (item) => item.data?.stage === 'post_rbd',
      )?.materialId;
      if (inspectionId == null) throw new Error('inspection_identity_missing');
      const inspection = await api.importedRun.getInspection({ origin, inspectionId });
      const photos = await api.importedRun.listPhotoPage({ origin });
      const protocol = await api.importedRun.getProtocol({ origin });
      return {
        origin,
        inspections,
        inspectionId,
        inspection,
        photos,
        protocol,
        binding: source.localSpecimenId,
      };
    });
    expect(initial.binding).toBeNull();
    expect(initial.origin.exportRevision).toBe(2);
    expect(initial.inspections).toMatchObject({
      ok: true,
      result: { verification: { scope: 'source_material_metadata' } },
    });
    expect(initial.inspection).toMatchObject({
      ok: true,
      result: {
        item: {
          state: 'verified',
          data: { runElapsedS: '0', stage: 'post_rbd', findings: { cracks: false } },
        },
      },
    });
    expect(initial.protocol).toMatchObject({
      ok: true,
      result: {
        item: { materialId: '1', data: { revisionNumber: '1', runId: initial.origin.runId } },
      },
    });
    if (!initial.photos.ok) throw new Error('photos_missing');
    expect(initial.photos.result.items.map((item) => item.data?.mediaType).sort()).toEqual([
      'image/jpeg',
      'image/png',
    ]);
    expect(initial.photos.result.items.some((item) => item.data?.inspectionId === null)).toBe(true);
    await page.getByRole('button', { name: 'Проверить и показать материалы', exact: true }).click();
    await page
      .getByRole('button', { name: `Показать осмотр ${initial.inspectionId}`, exact: true })
      .click();
    const inspectionUi = page.getByRole('region', { name: 'Деталь осмотра' });
    await expect(inspectionUi.getByText('Нет (false)', { exact: true }).first()).toBeVisible();
    await expect(inspectionUi.getByText('0', { exact: true })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Открыть исходный PDF', exact: true }),
    ).toBeEnabled();
    for (const photo of initial.photos.result.items) {
      if (photo.materialId === null) throw new Error('photo_identity_missing');
      await expect(
        page.getByRole('button', { name: `Открыть фотографию ${photo.materialId}`, exact: true }),
      ).toBeEnabled();
    }
    await app.evaluate(
      (_electron, sourcePath) => {
        process.env['IMPELLER_AUTOMATED_R130RUN_PATH'] = sourcePath;
      },
      join(fixtures, 'protocol_revision_2.r130run'),
    );
    await page.getByRole('button', { name: 'Импортировать результат R130SH', exact: true }).click();
    await expect(
      page
        .getByRole('region', { name: 'Импортированные запуски', exact: true })
        .getByRole('button'),
    ).toHaveCount(2);
    const previous = page
      .getByRole('region', { name: 'Импортированные запуски', exact: true })
      .getByRole('button')
      .filter({ hasText: 'rev 2' });
    const latest = page
      .getByRole('region', { name: 'Импортированные запуски', exact: true })
      .getByRole('button')
      .filter({ hasText: 'rev 3' });
    await previous.click();
    await latest.click();
    await page.getByRole('button', { name: 'Проверить и показать материалы', exact: true }).click();
    const protocolRegion = page.getByRole('region', { name: 'Лабораторный протокол', exact: true });
    await expect(
      protocolRegion.locator('dl > div').filter({ hasText: 'Редакция протокола' }).locator('dd'),
    ).toHaveText('2');
    await expect(
      protocolRegion.locator('dl > div').filter({ hasText: 'Редакция экспорта' }).locator('dd'),
    ).toHaveText('3');
    const reopened = await page.evaluate(
      async ({ oldOrigin, inspectionId }: { oldOrigin: MaterialOrigin; inspectionId: string }) => {
        const api = window.impeller;
        if (api === undefined) throw new Error('preload_missing');
        const closed = await api.project.close();
        const opened = await api.project.open();
        if (!closed.ok || !opened.ok) throw new Error('reopen_failed');
        const oldProtocol = await api.importedRun.getProtocol({ origin: oldOrigin });
        const sources = await api.importedRun.list();
        if (!sources.ok) throw new Error('sources_missing');
        const source = sources.result.find((item) => item.exportRevision === 3);
        if (source === undefined) throw new Error('new_revision_missing');
        const origin = {
          ...oldOrigin,
          localImportId: source.localImportId,
          exportRevision: source.exportRevision,
          outerPackageSha256: source.outerPackageSha256,
        };
        return {
          oldProtocol,
          inspections: await api.importedRun.listInspectionPage({ origin: oldOrigin }),
          inspection: await api.importedRun.getInspection({ origin: oldOrigin, inspectionId }),
          latestProtocol: await api.importedRun.getProtocol({ origin }),
          binding: source.localSpecimenId,
        };
      },
      { oldOrigin: initial.origin, inspectionId: initial.inspectionId },
    );
    expect(reopened.oldProtocol).toEqual(initial.protocol);
    expect(reopened.inspections).toEqual(initial.inspections);
    expect(reopened.inspection).toEqual(initial.inspection);
    expect(reopened.latestProtocol).toMatchObject({
      ok: true,
      result: {
        origin: { exportRevision: 3 },
        item: { materialId: '2', data: { revisionNumber: '2' } },
      },
    });
    expect(reopened.binding).toBeNull();
    expect(errors).toEqual([]);
  } finally {
    await app.close();
  }
});
