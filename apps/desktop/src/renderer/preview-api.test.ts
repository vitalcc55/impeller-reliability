import { describe, expect, it } from 'vitest';

import type { RuntimeStatus } from '@impeller-reliability/contracts';

import { createPreviewApi } from './preview-api';

describe('browser preview api', () => {
  it('provides deterministic ready state without Electron preload', async () => {
    const api = createPreviewApi('ready');
    const status = await api.system.getStatus();
    expect(status).toMatchObject({
      workerStatus: 'ready',
      sqliteStatus: 'ok',
      mode: 'development',
    });
    expect(status.message).toContain('без сохранения');
    await expect(api.system.ping()).resolves.toMatchObject({ workerStatus: 'ready' });
  });

  it('provides an actionable unavailable state', async () => {
    const api = createPreviewApi('unavailable');
    const status = await api.system.getStatus();
    expect(status).toMatchObject({
      workerStatus: 'unavailable',
      sqliteStatus: 'error',
    });
    expect(status.message).toContain('смоделирован');
    await expect(api.system.ping()).rejects.toThrow('preview_worker_unavailable');
    const observed: RuntimeStatus[] = [];
    const unsubscribe = api.system.subscribeStatus((nextStatus) => observed.push(nextStatus));
    await expect(api.system.restart()).resolves.toMatchObject({ workerStatus: 'ready' });
    expect(observed).toHaveLength(1);
    unsubscribe();
  });

  it('advances validation polling even when the first active snapshot is unchanged', async () => {
    const api = createPreviewApi('ready');
    const jobId = '8ab377f2-cfd8-4983-86ea-25f5d0171bd7';

    const started = await api.runPackageValidation.selectAndStart({ jobId });
    expect(started).toMatchObject({ ok: true, result: { state: 'running' } });
    await expect(api.runPackageValidation.discard(jobId)).resolves.toMatchObject({
      ok: false,
      error: { code: 'operation_in_progress' },
    });
    await expect(
      api.runPackageValidation.selectAndStart({
        jobId: '40f4acbf-5f06-4d75-a65c-382141d785aa',
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'operation_in_progress' } });
    const firstPoll = await api.runPackageValidation.get(jobId);
    expect(firstPoll).toMatchObject({ ok: true, result: { state: 'running' } });
    const secondPoll = await api.runPackageValidation.get(jobId);
    expect(secondPoll).toMatchObject({
      ok: true,
      result: { state: 'completed', report: { structuralVerdict: 'passed' } },
    });
    await expect(api.runPackageValidation.cancel(jobId)).resolves.toMatchObject({
      ok: true,
      result: { state: 'completed' },
    });
    await api.system.restart();
    await expect(api.runPackageValidation.get(jobId)).resolves.toMatchObject({
      ok: false,
      error: { code: 'entity_not_found' },
    });
  });

  it('models import progress, persisted source reads and explicit specimen binding', async () => {
    const api = createPreviewApi('ready');
    await api.project.create({
      name: 'M03B preview',
      projectNumber: '',
      description: '',
      status: 'draft',
    });
    const jobId = 'ec7cc676-e40d-4ad7-b038-83e0035dc212';
    await expect(
      api.runPackageImport.selectAndStart({ jobId, allowDiagnosticPartial: false }),
    ).resolves.toMatchObject({ ok: true, result: { state: 'copying' } });
    await expect(api.runPackageImport.get(jobId)).resolves.toMatchObject({
      ok: true,
      result: { state: 'copying' },
    });
    const completed = await api.runPackageImport.get(jobId);
    expect(completed).toMatchObject({
      ok: true,
      result: { state: 'completed', result: { disposition: 'existing' } },
    });
    const replacementJobId = '31871fa4-2088-4f0d-bcb4-dd5454294edc';
    await expect(
      api.runPackageImport.selectAndStart({
        jobId: replacementJobId,
        allowDiagnosticPartial: false,
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'operation_in_progress' } });
    await expect(
      api.runPackageImport.selectAndStart({
        jobId: replacementJobId,
        replaceJobId: jobId,
        allowDiagnosticPartial: false,
      }),
    ).resolves.toMatchObject({ ok: true, result: { state: 'copying' } });
    const listed = await api.importedRun.list();
    expect(listed).toMatchObject({ ok: true, result: [{ sourceIntegrity: 'verified' }] });
    if (!listed.ok || listed.result[0] === undefined) throw new Error('missing preview import');
    const summary = listed.result[0];
    await expect(api.importedRun.get(summary.localImportId)).resolves.toMatchObject({
      ok: true,
      result: { summary: { runId: summary.runId } },
    });
    await expect(
      api.importedRun.bindSpecimen({
        sourceSpecimenId: summary.sourceSpecimenId,
        localSpecimenId: null,
        expectedRevision: summary.bindingRevision,
        actor: 'local_user',
        reason: 'Явно оставить без привязки',
      }),
    ).resolves.toMatchObject({ ok: true, result: { recordRevision: 1 } });
  });
});
