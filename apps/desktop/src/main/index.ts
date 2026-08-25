import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  shell,
  type OpenDialogOptions,
  type SaveDialogOptions,
} from 'electron';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
  projectDraftSchema,
  projectUpdateMetadataPayloadSchema,
  runtimeStatusSchema,
  type DesktopError,
  type DesktopResult,
  type ProjectDraft,
  type ProjectOverview,
  type RecentProject,
  type RuntimeStatus,
  type WorkerErrorResponse,
} from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from './channels';
import { JsonlLogger } from './logging';
import { RecentProjectsStore } from './recent-projects';
import { WorkerClient, type WorkerLifecycleEvent } from './worker-client';
import { resolveWorkerLocation } from './worker-location';

let mainWindow: BrowserWindow | null = null;
let workerClient: WorkerClient | null = null;
let restartPromise: Promise<RuntimeStatus> | null = null;
let quitting = false;
let rendererCloseApproved = false;
const applicationInstanceId = randomUUID();

declare const __APPLICATION_VERSION__: string;

const testUserDataPath = process.env['IMPELLER_TEST_USER_DATA'];
if (process.env['NODE_ENV'] === 'test' && testUserDataPath !== undefined) {
  app.setPath('userData', resolve(testUserDataPath));
}

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'impeller',
    privileges: {
      standard: true,
      secure: true,
      stream: true,
      codeCache: true,
    },
  },
]);

const status: RuntimeStatus = {
  applicationVersion: __APPLICATION_VERSION__,
  electronVersion: process.versions.electron,
  workerStatus: 'starting',
  workerVersion: null,
  protocolVersion: null,
  sqliteStatus: 'pending',
  mode: app.isPackaged ? 'packaged' : 'development',
  message: 'Запуск локального расчётного контура…',
};

function snapshotStatus(): RuntimeStatus {
  return runtimeStatusSchema.parse({ ...status });
}

function emitStatus(): RuntimeStatus {
  const snapshot = snapshotStatus();
  if (mainWindow !== null && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(IPC_CHANNELS.statusChanged, snapshot);
  }
  return snapshot;
}

function applyWorkerLifecycle(event: WorkerLifecycleEvent): void {
  status.workerStatus = event.state;
  if (event.state === 'starting') {
    status.sqliteStatus = 'pending';
    status.message = 'Запуск локального расчётного контура…';
  } else if (event.state === 'ready') {
    status.message = 'Локальный контур готов к работе.';
  } else if (event.state === 'unavailable') {
    status.sqliteStatus = 'error';
    status.message = `Worker недоступен${event.reason === null ? '.' : `: ${event.reason}`}`;
  } else if (event.state === 'stopping') {
    status.message = 'Остановка локального расчётного контура…';
  } else {
    status.sqliteStatus = 'pending';
    status.message = 'Локальный расчётный контур остановлен.';
  }
  emitStatus();
}

async function refreshStatus(): Promise<RuntimeStatus> {
  const client = workerClient;
  if (client === null) return snapshotStatus();
  try {
    const handshake = await client.request('system.handshake', {});
    const storage = await client.request('storage.health', {});
    if (!handshake.ok) throw new Error(handshake.error.code);
    if (!storage.ok) throw new Error(storage.error.code);
    status.workerVersion = handshake.result.workerVersion;
    status.protocolVersion = handshake.result.protocolVersions.includes(1) ? 1 : null;
    status.sqliteStatus = storage.result.status;
    if (storage.result.status !== 'ok') throw new Error('storage_health_failed');
    status.workerStatus = 'ready';
    status.message = 'Локальный контур готов к работе.';
    client.markReady();
  } catch (error) {
    status.workerStatus = 'unavailable';
    status.sqliteStatus = 'error';
    status.message = `Worker недоступен: ${String(error)}`;
    emitStatus();
  }
  return snapshotStatus();
}

function restartWorker(): Promise<RuntimeStatus> {
  if (restartPromise !== null) return restartPromise;
  const client = workerClient;
  if (client === null) return Promise.reject(new Error('worker_unavailable'));
  const currentRestart = (async () => {
    await client.restart();
    return refreshStatus();
  })().finally(() => {
    if (restartPromise === currentRestart) restartPromise = null;
  });
  restartPromise = currentRestart;
  return currentRestart;
}

function registerIpc(logPath: string, stateDirectory: string, logger: JsonlLogger): void {
  const recentProjects = new RecentProjectsStore(join(stateDirectory, 'recent-projects.json'));
  ipcMain.handle(IPC_CHANNELS.getStatus, () => snapshotStatus());
  ipcMain.handle(IPC_CHANNELS.ping, async () => {
    const client = workerClient;
    if (client === null) throw new Error('worker_unavailable');
    const response = await client.request('system.ping', {});
    if (!response.ok || response.result.pong !== true) throw new Error('worker_ping_failed');
    return refreshStatus();
  });
  ipcMain.handle(IPC_CHANNELS.restart, () => restartWorker());
  ipcMain.handle(IPC_CHANNELS.confirmClose, () => {
    rendererCloseApproved = true;
    app.quit();
  });
  ipcMain.handle(IPC_CHANNELS.openLog, async () => {
    const result = await shell.openPath(logPath);
    if (result !== '') throw new Error(`open_log_failed:${result}`);
  });
  ipcMain.handle(IPC_CHANNELS.projectCreate, async (_event, rawDraft: unknown) => {
    const draft = projectDraftSchema.parse(rawDraft);
    const automatedPath = approvedAutomatedProjectPath();
    if (automatedPath !== null) {
      return createProject(workerClient, recentProjects, logger, automatedPath, draft);
    }
    const options: SaveDialogOptions = {
      title: 'Создать проект Impeller Reliability',
      defaultPath: 'Новый проект.irproj',
      buttonLabel: 'Создать проект',
      filters: [{ name: 'Проект Impeller Reliability', extensions: ['irproj'] }],
      properties: ['createDirectory', 'showOverwriteConfirmation'],
    };
    const selection =
      mainWindow === null
        ? await dialog.showSaveDialog(options)
        : await dialog.showSaveDialog(mainWindow, options);
    if (selection.canceled || selection.filePath === '') return cancelledResult<ProjectOverview>();
    const path = selection.filePath.toLowerCase().endsWith('.irproj')
      ? selection.filePath
      : `${selection.filePath}.irproj`;
    return createProject(workerClient, recentProjects, logger, path, draft);
  });
  ipcMain.handle(IPC_CHANNELS.projectOpen, async () => {
    const automatedPath = approvedAutomatedProjectPath();
    if (automatedPath !== null) {
      return openProject(workerClient, recentProjects, logger, automatedPath);
    }
    const options: OpenDialogOptions = {
      title: 'Открыть проект Impeller Reliability',
      buttonLabel: 'Открыть проект',
      properties: ['openDirectory'],
    };
    const selection =
      mainWindow === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(mainWindow, options);
    if (selection.canceled || selection.filePaths[0] === undefined) {
      return cancelledResult<ProjectOverview>();
    }
    return openProject(workerClient, recentProjects, logger, selection.filePaths[0]);
  });
  ipcMain.handle(IPC_CHANNELS.projectOpenRecent, async (_event, rawPath: unknown) => {
    if (typeof rawPath !== 'string') {
      return failureResult<ProjectOverview>(
        'validation_error',
        'Путь не входит в список недавних проектов.',
      );
    }
    try {
      if (!(await recentProjects.contains(rawPath))) {
        return failureResult<ProjectOverview>(
          'validation_error',
          'Путь не входит в список недавних проектов.',
        );
      }
    } catch {
      return failureResult<ProjectOverview>(
        'storage_error',
        'Не удалось проверить список недавних проектов.',
      );
    }
    return openProject(workerClient, recentProjects, logger, rawPath);
  });
  ipcMain.handle(IPC_CHANNELS.projectClose, () =>
    runProjectOperation(workerClient, async (client) => client.request('project.close', {})),
  );
  ipcMain.handle(IPC_CHANNELS.projectGetOverview, () =>
    runProjectOperation(workerClient, async (client) => client.request('project.getOverview', {})),
  );
  ipcMain.handle(IPC_CHANNELS.projectUpdateMetadata, async (_event, rawCommand: unknown) => {
    const command = projectUpdateMetadataPayloadSchema.parse(rawCommand);
    const result = await runProjectOperation(workerClient, async (client) =>
      client.request('project.updateMetadata', command),
    );
    if (result.ok) await touchRecentSafely(recentProjects, result.result, logger);
    return result;
  });
  ipcMain.handle(IPC_CHANNELS.projectCreateBackup, () =>
    runProjectOperation(workerClient, async (client) => client.request('project.createBackup', {})),
  );
  ipcMain.handle(
    IPC_CHANNELS.projectListRecent,
    async (): Promise<DesktopResult<readonly RecentProject[]>> => {
      try {
        return { ok: true, result: await recentProjects.list() };
      } catch {
        return failureResult('storage_error', 'Не удалось прочитать список недавних проектов.');
      }
    },
  );
}

function approvedAutomatedProjectPath(): string | null {
  const isAutomated =
    process.env['NODE_ENV'] === 'test' || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined;
  if (!isAutomated) return null;
  const rawPath = process.env['IMPELLER_AUTOMATED_PROJECT_PATH'];
  if (rawPath === undefined || !rawPath.toLowerCase().endsWith('.irproj')) return null;
  return resolve(rawPath);
}

async function createProject(
  client: WorkerClient | null,
  recentProjects: RecentProjectsStore,
  logger: JsonlLogger,
  path: string,
  draft: ProjectDraft,
): Promise<DesktopResult<ProjectOverview>> {
  const result = await runProjectOperation(client, async (readyClient) =>
    readyClient.request('project.create', {
      path,
      applicationInstanceId,
      applicationVersion: __APPLICATION_VERSION__,
      draft,
    }),
  );
  if (result.ok) await touchRecentSafely(recentProjects, result.result, logger);
  return result;
}

async function openProject(
  client: WorkerClient | null,
  recentProjects: RecentProjectsStore,
  logger: JsonlLogger,
  path: string,
): Promise<DesktopResult<ProjectOverview>> {
  const result = await runProjectOperation(client, async (readyClient) =>
    readyClient.request('project.open', { path, applicationInstanceId }),
  );
  if (result.ok) await touchRecentSafely(recentProjects, result.result, logger);
  return result;
}

async function touchRecentSafely(
  recentProjects: RecentProjectsStore,
  overview: ProjectOverview,
  logger: JsonlLogger,
): Promise<void> {
  try {
    await recentProjects.touch(overview);
  } catch (error) {
    try {
      await logger.write({
        severity: 'warning',
        component: 'main',
        event: 'recent_projects_update_failed',
        details: { projectId: overview.projectId, error: String(error) },
      });
    } catch {
      process.stderr.write('recent_projects_update_failed\n');
    }
  }
}

async function runProjectOperation<TResult>(
  client: WorkerClient | null,
  operation: (readyClient: WorkerClient) => Promise<OperationResponse<TResult>>,
): Promise<DesktopResult<TResult>> {
  if (client === null) return failureResult('worker_unavailable', 'Расчётное ядро недоступно.');
  try {
    const response = await operation(client);
    return response.ok ? { ok: true, result: response.result } : fromWorkerError(response);
  } catch (error) {
    return failureResult(
      'worker_unavailable',
      `Операция с проектом не выполнена: ${String(error)}`,
    );
  }
}

type OperationResponse<TResult> =
  | {
      readonly ok: true;
      readonly result: TResult;
    }
  | WorkerErrorResponse;

function fromWorkerError<TResult>(response: WorkerErrorResponse): DesktopResult<TResult> {
  const error: DesktopError = response.error;
  return { ok: false, error };
}

function cancelledResult<TResult>(): DesktopResult<TResult> {
  return failureResult('cancelled', 'Операция отменена пользователем.');
}

function failureResult<TResult>(
  code: DesktopError['code'],
  message: string,
): DesktopResult<TResult> {
  return { ok: false, error: { code, message, details: {}, retryable: false } };
}

function registerRendererProtocol(): void {
  const rendererRoot = resolve(__dirname, '../renderer');
  protocol.handle('impeller', (request) => {
    const requestUrl = new URL(request.url);
    if (requestUrl.host !== 'app') return new Response(null, { status: 404 });
    let relativePath: string;
    try {
      relativePath = decodeURIComponent(requestUrl.pathname).replace(/^\/+/, '');
    } catch {
      return new Response(null, { status: 400 });
    }
    const resourcePath = resolve(rendererRoot, relativePath);
    if (resourcePath !== rendererRoot && !resourcePath.startsWith(`${rendererRoot}${sep}`)) {
      return new Response(null, { status: 403 });
    }
    return net.fetch(pathToFileURL(resourcePath).toString());
  });
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 960,
    height: 680,
    minWidth: 760,
    minHeight: 560,
    show: false,
    backgroundColor: '#f7f8fa',
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });
  const rendererUrl = process.env['ELECTRON_RENDERER_URL'] ?? 'impeller://app/index.html';
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, targetUrl) => {
    if (targetUrl !== rendererUrl) event.preventDefault();
  });
  mainWindow.webContents.session.setPermissionRequestHandler(
    (_webContents, _permission, callback) => callback(false),
  );
  mainWindow.on('close', (event) => {
    if (quitting || rendererCloseApproved || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined)
      return;
    event.preventDefault();
    mainWindow?.webContents.send(IPC_CHANNELS.closeRequested);
  });
  mainWindow.once('ready-to-show', () => {
    if (process.env['IMPELLER_SMOKE_OUTPUT'] === undefined) mainWindow?.show();
  });
  await mainWindow.loadURL(rendererUrl);
}

async function runSmokeIfRequested(): Promise<void> {
  const smokeOutput = process.env['IMPELLER_SMOKE_OUTPUT'];
  if (smokeOutput === undefined) return;
  const startedAt = performance.now();
  const runtime = await refreshStatus();
  const ping = await workerClient?.request('system.ping', {});
  const automatedProjectPath = approvedAutomatedProjectPath();
  let projectScenarioPassed = false;
  if (automatedProjectPath !== null && workerClient !== null) {
    const created = await workerClient.request('project.create', {
      path: automatedProjectPath,
      applicationInstanceId,
      applicationVersion: __APPLICATION_VERSION__,
      draft: {
        name: 'Packaged smoke project',
        projectNumber: 'SMOKE-001',
        description: 'Bundled worker project container scenario.',
        status: 'draft',
      },
    });
    if (created.ok) {
      const updated = await workerClient.request('project.updateMetadata', {
        expectedRevision: created.result.recordRevision,
        metadata: {
          name: 'Packaged smoke project updated',
          projectNumber: 'SMOKE-002',
          description: 'Persisted through close and reopen.',
          status: 'active',
        },
      });
      const closed = await workerClient.request('project.close', {});
      const reopened = await workerClient.request('project.open', {
        path: automatedProjectPath,
        applicationInstanceId,
      });
      projectScenarioPassed =
        updated.ok &&
        updated.result.recordRevision === 2 &&
        closed.ok &&
        reopened.ok &&
        reopened.result.name === 'Packaged smoke project updated' &&
        reopened.result.projectNumber === 'SMOKE-002' &&
        reopened.result.recordRevision === 2;
      await workerClient.request('project.close', {});
    }
  }
  await mkdir(dirname(smokeOutput), { recursive: true });
  await writeFile(
    smokeOutput,
    JSON.stringify(
      {
        schemaVersion: 1,
        passed:
          runtime.workerStatus === 'ready' &&
          runtime.sqliteStatus === 'ok' &&
          ping?.ok === true &&
          projectScenarioPassed,
        runtime,
        pingOk: ping?.ok === true,
        projectScenarioPassed,
        elapsedMs: Math.round(performance.now() - startedAt),
        pid: process.pid,
        workerPid: workerClient?.processId ?? null,
      },
      null,
      2,
    ),
    'utf8',
  );
  const holdMs = Number(process.env['IMPELLER_SMOKE_HOLD_MS'] ?? '0');
  if (Number.isInteger(holdMs) && holdMs > 0 && holdMs <= 5_000) {
    await new Promise<void>((resolveHold) => setTimeout(resolveHold, holdMs));
  }
  rendererCloseApproved = true;
  app.quit();
}

app
  .whenReady()
  .then(async () => {
    const stateDirectory = join(app.getPath('userData'), 'state');
    const logPath = join(app.getPath('logs'), 'impeller-reliability.jsonl');
    const logger = new JsonlLogger(logPath);
    workerClient = new WorkerClient(
      resolveWorkerLocation({
        isPackaged: app.isPackaged,
        appPath: app.getAppPath(),
        resourcesPath: process.resourcesPath,
      }),
      stateDirectory,
      logger,
      applyWorkerLifecycle,
    );
    registerIpc(logPath, stateDirectory, logger);
    registerRendererProtocol();
    await logger.write({ severity: 'info', component: 'main', event: 'application_start' });
    await workerClient.start();
    await refreshStatus();
    await createWindow();
    await runSmokeIfRequested();
  })
  .catch((error: unknown) => {
    status.workerStatus = 'unavailable';
    status.sqliteStatus = 'error';
    status.message = `Ошибка запуска: ${String(error)}`;
    app.quit();
  });

app.on('window-all-closed', () => app.quit());
app.on('before-quit', (event) => {
  if (quitting || workerClient === null) return;
  event.preventDefault();
  quitting = true;
  void workerClient.shutdown().finally(() => app.exit(0));
});
