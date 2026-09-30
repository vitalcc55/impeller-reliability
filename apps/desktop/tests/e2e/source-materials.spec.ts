import { mkdirSync, rmSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { _electron as electron, expect, test } from '@playwright/test';

test('reads exact unbound materials through production Main Preload and worker, then reopens', async () => {
  const root = resolve(import.meta.dirname, '../../../..');
  const evidenceRoot = join(root, '.tmp/.codex/evidence/material-runtime-e2e');
  const projectPath = join(evidenceRoot, 'materials.irproj');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(evidenceRoot, { recursive: true });
  const app = await electron.launch({
    args: [join(root, 'apps/desktop/out/main/index.js')],
    cwd: root,
    env: {
      ...process.env,
      NODE_ENV: 'test',
      IMPELLER_AUTOMATED_PROJECT_PATH: projectPath,
      IMPELLER_AUTOMATED_R130RUN_PATH: join(
        root,
        'fixtures/contracts/r130run/v1/m9a/packages/normal_final_rbd.r130run',
      ),
      IMPELLER_TEST_USER_DATA: join(evidenceRoot, 'user-data'),
    },
  });
  try {
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await page.getByRole('button', { name: 'Результаты R130SH' }).click();
    await page.getByRole('button', { name: 'Импортировать результат R130SH' }).click();
    await expect(page.getByText('Импорт завершён')).toBeVisible();
    const material = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const project = await api.project.getOverview();
      const imports = await api.importedRun.list();
      if (!project.ok || !imports.ok || imports.result[0] === undefined)
        throw new Error('import_missing');
      const source = imports.result[0];
      const origin = {
        projectId: project.result.projectId,
        localImportId: source.localImportId,
        packageId: source.packageId,
        runId: source.runId,
        exportRevision: source.exportRevision,
        outerPackageSha256: source.outerPackageSha256,
      };
      const inspections = await api.importedRun.listInspectionPage({ origin });
      if (
        !inspections.ok ||
        inspections.result.items[0]?.materialId === null ||
        inspections.result.items[0] === undefined
      )
        throw new Error('inspection_missing');
      const inspection = await api.importedRun.getInspection({
        origin,
        inspectionId: inspections.result.items[0].materialId,
      });
      const photos = await api.importedRun.listPhotoPage({ origin });
      const protocol = await api.importedRun.getProtocol({ origin });
      const foreign = await api.importedRun.getProtocol({
        origin: { ...origin, exportRevision: origin.exportRevision + 1 },
      });
      const missing = await api.importedRun.openMaterial({
        identity: { origin, kind: 'protocol', materialId: '7' },
        operationId: crypto.randomUUID(),
      });
      return {
        origin,
        inspections,
        inspection,
        photos,
        protocol,
        foreign,
        missing,
        binding: source.localSpecimenId,
      };
    });
    expect(material.binding).toBeNull();
    expect(material.inspections).toMatchObject({
      ok: true,
      result: { verification: { scope: 'source_material_metadata' } },
    });
    expect(material.inspection).toMatchObject({
      ok: true,
      result: {
        item: {
          state: 'verified',
          data: { runElapsedS: '0', stage: 'post_rbd', findings: { cracks: false } },
        },
      },
    });
    expect(material.photos.ok).toBe(true);
    expect(material.protocol).toMatchObject({
      ok: true,
      result: { item: { state: 'not_included', data: null } },
    });
    expect(material.foreign).toMatchObject({ ok: false, error: { code: 'validation_error' } });
    expect(material.missing).toMatchObject({ ok: false, error: { code: 'file_missing' } });
    expect(JSON.stringify(material)).not.toContain('absolutePath');
    expect(readdirSync(join(evidenceRoot, 'user-data', 'state', 'source-material-copies'))).toEqual(
      ['owner'],
    );
    const reopened = await page.evaluate(async (origin) => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const closed = await api.project.close();
      const opened = await api.project.open();
      if (!closed.ok || !opened.ok) throw new Error('project_reopen_failed');
      return api.importedRun.listInspectionPage({ origin });
    }, material.origin);
    expect(reopened).toEqual(material.inspections);
    expect(errors).toEqual([]);
  } finally {
    await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});
