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
});
