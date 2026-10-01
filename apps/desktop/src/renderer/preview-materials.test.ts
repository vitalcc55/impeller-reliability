import { describe, expect, it } from 'vitest';
import {
  inspectionMaterialPageSchema,
  photoMaterialPageSchema,
  protocolMaterialDetailSchema,
  type MaterialOrigin,
} from '@impeller-reliability/contracts';
import { createPreviewApi } from './preview-api';

async function fixture() {
  const api = createPreviewApi('ready');
  const project = await api.project.open();
  const listed = await api.importedRun.list();
  if (!project.ok || !listed.ok) throw new Error('preview_missing');
  const origins: MaterialOrigin[] = listed.result.map((s) => ({
    projectId: project.result.projectId,
    localImportId: s.localImportId,
    packageId: s.packageId,
    runId: s.runId,
    exportRevision: s.exportRevision,
    outerPackageSha256: s.outerPackageSha256,
  }));
  const first = origins[0];
  const second = origins[1];
  if (first === undefined || second === undefined) throw new Error('source_missing');
  return { api, first, second };
}
describe('typed source materials in DEV preview', () => {
  it('materializes both imports explicitly without replacing their exact identities', async () => {
    const f = await fixture();
    const detail = await f.api.importedRun.get(f.first.localImportId);
    if (!detail.ok) throw new Error('source_missing');
    await f.api.importedRun.bindSpecimen({
      sourceSpecimenId: detail.result.summary.sourceSpecimenId,
      localSpecimenId: 'fa50e13e-2944-4874-9cf7-b4747d57ae09',
      expectedRevision: 1,
      actor: 'local_user',
      reason: 'Явная синтетическая привязка',
    });
    const first = await f.api.reliabilityExecution.materialize(f.first.localImportId);
    const second = await f.api.reliabilityExecution.materialize(f.second.localImportId);
    expect(second).toMatchObject({
      ok: true,
      result: {
        localImportId: f.second.localImportId,
        sourceRunId: f.second.runId,
        exportRevision: 2,
        packageKind: 'diagnostic_partial',
        lifecycleStatus: 'interrupted',
        sourceOuterPackageSha256: f.second.outerPackageSha256,
      },
    });
    if (!first.ok || !second.ok) throw new Error('execution_missing');
    expect(second.result.executionId).not.toBe(first.result.executionId);
    await expect(f.api.reliabilityExecution.getDetail(first.result.executionId)).resolves.toEqual(
      first,
    );
    await expect(f.api.reliabilityExecution.materialize(f.second.localImportId)).resolves.toEqual(
      second,
    );
    const page = await f.api.reliabilityExecution.listPage(first.result.wheelModelId);
    expect(page).toMatchObject({
      ok: true,
      result: {
        items: [
          { sourceRunId: f.first.runId, packageKind: 'final' },
          {
            sourceRunId: f.second.runId,
            packageKind: 'diagnostic_partial',
            technicalStatus: 'interrupted',
          },
        ],
      },
    });
  });
  it('preserves verification and independent enrichment for the diagnostic source', async () => {
    const f = await fixture();
    await expect(f.api.importedRun.verifySource(f.second.localImportId)).resolves.toMatchObject({
      ok: true,
      result: { localImportId: f.second.localImportId, sourceIntegrity: 'verified' },
    });
    const result = await f.api.importedRun.applyEnrichmentResolution({
      localImportId: f.second.localImportId,
      resolutionId: crypto.randomUUID(),
      sourcePayloadPath: 'run-summary.json',
      sourceField: 'customer_name',
      targetEntityType: 'customer_profile',
      targetEntityId: 'case-customer',
      targetField: 'full_name',
      decision: 'use_analyst',
      expectedTargetRevision: null,
      actor: 'local_user',
      reason: 'Синтетическая проверка',
    });
    expect(result).toMatchObject({
      ok: true,
      result: {
        summary: { localImportId: f.second.localImportId },
        enrichmentResolutions: [{ sourceField: 'customer_name' }],
      },
    });
    const primary = await f.api.importedRun.get(f.first.localImportId);
    expect(primary).toMatchObject({ ok: true, result: { enrichmentResolutions: [] } });
    await expect(f.api.importedRun.get(f.second.localImportId)).resolves.toEqual(result);
  });
  it('returns complete schema-valid source facts without fake OS success', async () => {
    const f = await fixture();
    const inspections = await f.api.importedRun.listInspectionPage({ origin: f.first });
    const photos = await f.api.importedRun.listPhotoPage({ origin: f.first });
    const protocol = await f.api.importedRun.getProtocol({ origin: f.first });
    if (!inspections.ok || !photos.ok || !protocol.ok) throw new Error('material_missing');
    expect(inspectionMaterialPageSchema.parse(inspections.result).items[0]?.data).toMatchObject({
      runElapsedS: '0',
      tripIndex: null,
      findings: { cracks: false, balancingElementsState: 'not_assessed' },
    });
    expect(photoMaterialPageSchema.parse(photos.result).items[1]?.data?.inspectionId).toBeNull();
    expect(protocolMaterialDetailSchema.parse(protocol.result).item.data).toMatchObject({
      releaseId: '9007199254740995',
      revisionNumber: '9007199254740993',
    });
    await expect(
      f.api.importedRun.openMaterial({
        identity: { origin: f.first, kind: 'protocol', materialId: '9007199254740995' },
        operationId: crypto.randomUUID(),
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'material_open_failed' } });
    const diagnostic = await f.api.importedRun.listPhotoPage({ origin: f.second });
    if (!diagnostic.ok) throw new Error('diagnostic_missing');
    expect(diagnostic.result.items.map((item) => item.state)).toEqual([
      'verified',
      'verified',
      'unavailable',
      'ambiguous',
      'too_large',
    ]);
    await expect(f.api.importedRun.getProtocol({ origin: f.second })).resolves.toMatchObject({
      ok: true,
      result: { item: { state: 'not_included', materialId: null, data: null } },
    });
  });
  it('rejects foreign origin, IDs and page cursors and preserves bounded continuation', async () => {
    const f = await fixture();
    for (const origin of [
      { ...f.first, exportRevision: 2 },
      { ...f.first, outerPackageSha256: '0'.repeat(64) },
      { ...f.first, runId: f.second.runId },
      { ...f.first, projectId: f.second.localImportId },
    ]) {
      await expect(f.api.importedRun.getProtocol({ origin })).resolves.toMatchObject({
        ok: false,
        error: { code: 'file_integrity_mismatch' },
      });
    }
    await expect(
      f.api.importedRun.getInspection({ origin: f.first, inspectionId: 'foreign' }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'entity_not_found' } });
    const page = await f.api.importedRun.listInspectionPage({ origin: f.first, limit: 1 });
    if (!page.ok || page.result.nextCursor === null) throw new Error('continuation_missing');
    expect(page.result.items).toHaveLength(1);
    expect(page.result.pageBound).toBe('item_limit');
    const next = await f.api.importedRun.listInspectionPage({
      origin: f.first,
      cursor: page.result.nextCursor,
      limit: 1,
    });
    expect(next).toMatchObject({
      ok: true,
      result: { nextCursor: null, items: [{ materialId: 'synthetic-vibration-pause' }] },
    });
    await expect(
      f.api.importedRun.listPhotoPage({ origin: f.first, cursor: page.result.nextCursor }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'validation_error' } });
    await expect(
      f.api.importedRun.listInspectionPage({ origin: f.second, cursor: page.result.nextCursor }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'validation_error' } });
    await expect(
      f.api.importedRun.listInspectionPage({ origin: f.first, limit: 51 }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'validation_error' } });
  });
  it('localizes unavailable worker and closed project without returning synthetic data', async () => {
    const f = await fixture();
    const unavailable = createPreviewApi('unavailable');
    await unavailable.project.open();
    await expect(
      unavailable.importedRun.listInspectionPage({ origin: f.first }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'worker_unavailable' } });
    await f.api.project.close();
    await expect(f.api.importedRun.getProtocol({ origin: f.first })).resolves.toMatchObject({
      ok: false,
      error: { code: 'cancelled' },
    });
  });
});
