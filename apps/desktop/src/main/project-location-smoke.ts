import type { BrowserWindow } from 'electron';
import { randomUUID } from 'node:crypto';
import { readFile, rename, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import {
  createDesktopResultSchema,
  projectOverviewSchema,
  type ProjectOverview,
} from '@impeller-reliability/contracts';

import { RecentProjectsStore } from './recent-projects';

// Exercise the same renderer → Preload → Main routes as a user, including a
// recent entry from a previous installation, without creating an unsafe case.
export async function verifyProjectLocationSmoke(
  window: BrowserWindow,
  resourcesPath: string,
  userDataPath: string,
  overview: ProjectOverview,
): Promise<void> {
  const path = join(resourcesPath, `Проверка расположения ${randomUUID()}.irproj`);
  const recentPath = join(userDataPath, 'state', 'recent-projects.json');
  const originalRecent = await readFile(recentPath);
  const originalPath = process.env['IMPELLER_AUTOMATED_PROJECT_PATH'];
  try {
    process.env['IMPELLER_AUTOMATED_PROJECT_PATH'] = path;
    await new RecentProjectsStore(recentPath).touch({ ...overview, path });
    const draft = { name: 'Location probe', projectNumber: '', description: '', status: 'draft' };
    for (const expression of [
      `window.impeller.project.create(${JSON.stringify(draft)})`,
      'window.impeller.project.open()',
      `window.impeller.project.openRecent(${JSON.stringify(path)})`,
    ]) {
      const raw: unknown = await window.webContents.executeJavaScript(expression);
      const response = createDesktopResultSchema(projectOverviewSchema).parse(raw);
      if (
        response.ok ||
        response.error.code !== 'validation_error' ||
        !response.error.message.includes('переместите')
      ) {
        throw new Error('smoke_project_location_boundary');
      }
    }
    try {
      await stat(path);
    } catch (error: unknown) {
      if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return;
      throw error;
    }
    throw new Error('smoke_project_created_inside_installation');
  } finally {
    if (originalPath === undefined) delete process.env['IMPELLER_AUTOMATED_PROJECT_PATH'];
    else process.env['IMPELLER_AUTOMATED_PROJECT_PATH'] = originalPath;
    const temporaryPath = `${recentPath}.${randomUUID()}.tmp`;
    await writeFile(temporaryPath, originalRecent);
    await rename(temporaryPath, recentPath);
  }
}
