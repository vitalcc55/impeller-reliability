import { describe, expect, it } from 'vitest';

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
    const status = await createPreviewApi('unavailable').system.getStatus();
    expect(status).toMatchObject({
      workerStatus: 'unavailable',
      sqliteStatus: 'error',
    });
    expect(status.message).toContain('смоделирован');
  });
});
