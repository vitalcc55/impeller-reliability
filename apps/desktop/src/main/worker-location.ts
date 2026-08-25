import { join, resolve } from 'node:path';

export interface WorkerLocationInput {
  readonly isPackaged: boolean;
  readonly appPath: string;
  readonly resourcesPath: string;
}

export interface WorkerLocation {
  readonly command: string;
  readonly arguments: readonly string[];
  readonly cwd: string;
  readonly executablePath: string | null;
}

export function resolveWorkerLocation(input: WorkerLocationInput): WorkerLocation {
  if (input.isPackaged) {
    const cwd = join(input.resourcesPath, 'python-worker');
    const executablePath = join(cwd, 'impeller-reliability-worker.exe');
    return { command: executablePath, arguments: [], cwd, executablePath };
  }

  const projectRoot = resolve(input.appPath, '..', '..', '..', '..');
  const workerRoot = join(projectRoot, 'tools', 'python-worker');
  return {
    command: join(workerRoot, '.venv', 'Scripts', 'python.exe'),
    arguments: ['-I', '-m', 'impeller_reliability.worker.main'],
    cwd: workerRoot,
    executablePath: null,
  };
}

const SAFE_ENVIRONMENT_KEYS = [
  'SystemRoot',
  'WINDIR',
  'TEMP',
  'TMP',
  'LOCALAPPDATA',
  'Path',
  'PATH',
] as const;

export function createWorkerEnvironment(
  source: NodeJS.ProcessEnv,
  stateDirectory: string,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {};
  for (const key of SAFE_ENVIRONMENT_KEYS) {
    const value = source[key];
    if (value !== undefined && value !== '') environment[key] = value;
  }
  environment['PYTHONIOENCODING'] = 'utf-8';
  environment['PYTHONUTF8'] = '1';
  environment['PYTHONNOUSERSITE'] = '1';
  environment['IMPELLER_STATE_DIR'] = stateDirectory;
  return environment;
}
