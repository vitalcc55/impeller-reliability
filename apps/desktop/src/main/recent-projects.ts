import { randomUUID } from 'node:crypto';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

import {
  recentProjectsSchema,
  type ProjectOverview,
  type RecentProject,
} from '@impeller-reliability/contracts';

const MAX_RECENT_PROJECTS = 10;

export class RecentProjectsStore {
  public constructor(private readonly path: string) {}

  public async list(): Promise<readonly RecentProject[]> {
    try {
      return recentProjectsSchema.parse(JSON.parse(await readFile(this.path, 'utf8')));
    } catch (error) {
      if (isMissingFile(error)) return [];
      throw error;
    }
  }

  public async contains(path: string): Promise<boolean> {
    return (await this.list()).some((project) => project.path === path);
  }

  public async touch(overview: ProjectOverview): Promise<readonly RecentProject[]> {
    const current = await this.list();
    const next: readonly RecentProject[] = [
      {
        path: overview.path,
        name: overview.name,
        projectNumber: overview.projectNumber,
        lastOpenedAtUtc: new Date().toISOString(),
      },
      ...current.filter((project) => project.path !== overview.path),
    ].slice(0, MAX_RECENT_PROJECTS);
    await this.write(next);
    return next;
  }

  private async write(projects: readonly RecentProject[]): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true });
    const temporaryPath = `${this.path}.${randomUUID()}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(projects, null, 2)}\n`, 'utf8');
    await rename(temporaryPath, this.path);
  }
}

function isMissingFile(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
