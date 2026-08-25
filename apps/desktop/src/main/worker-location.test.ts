import { describe, expect, it } from 'vitest';
import { join } from 'node:path';

import { createWorkerEnvironment, resolveWorkerLocation } from './worker-location';

describe('worker location', () => {
  it('resolves the repository venv from electron-vite output', () => {
    const repositoryRoot = join('C:', 'workspace', 'impeller-reliability');
    const location = resolveWorkerLocation({
      isPackaged: false,
      appPath: join(repositoryRoot, 'apps', 'desktop', 'out', 'main'),
      resourcesPath: '',
    });
    expect(location.command).toBe(
      join(repositoryRoot, 'tools', 'python-worker', '.venv', 'Scripts', 'python.exe'),
    );
    expect(location.arguments).toEqual(['-I', '-m', 'impeller_reliability.worker.main']);
  });

  it('resolves and sanitizes packaged worker state', () => {
    const location = resolveWorkerLocation({
      isPackaged: true,
      appPath: 'ignored',
      resourcesPath: join('C:', 'portable', 'resources'),
    });
    expect(location.executablePath).toBe(
      join('C:', 'portable', 'resources', 'python-worker', 'impeller-reliability-worker.exe'),
    );
    expect(
      createWorkerEnvironment(
        { SystemRoot: 'C:\\Windows', SECRET: 'must-not-cross-boundary' },
        'C:\\state',
      ),
    ).toEqual({
      SystemRoot: 'C:\\Windows',
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
      PYTHONNOUSERSITE: '1',
      IMPELLER_STATE_DIR: 'C:\\state',
    });
  });
});
