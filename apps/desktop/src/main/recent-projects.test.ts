import { readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { RecentProjectsStore } from './recent-projects';

describe('RecentProjectsStore', () => {
  it('atomically keeps the newest canonical project entry', async () => {
    const path = join(process.env['TEMP'] ?? '.', `impeller-recent-${crypto.randomUUID()}.json`);
    try {
      const store = new RecentProjectsStore(path);
      const overview = {
        projectId: '019d2ca4-b4e6-7e18-8f5e-36ce99ab87da',
        path: 'C:\\Проекты\\Колесо.irproj',
        name: 'Колесо',
        projectNumber: 'ИР-1',
        description: '',
        status: 'draft' as const,
        recordRevision: 1,
        createdAtUtc: '2026-08-25T15:00:00.000Z',
        updatedAtUtc: '2026-08-25T15:00:00.000Z',
        createdWithApplicationVersion: '0.1.0',
        schemaVersion: 1,
      };
      await store.touch(overview);
      await store.touch({ ...overview, name: 'Колесо после изменения', recordRevision: 2 });
      expect(await store.contains(overview.path)).toBe(true);
      expect(await store.list()).toHaveLength(1);
      expect((await store.list())[0]?.name).toBe('Колесо после изменения');
      expect(JSON.parse(await readFile(path, 'utf8'))).toHaveLength(1);
    } finally {
      await rm(path, { force: true });
    }
  });
});
