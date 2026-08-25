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
});
