import { app, BrowserWindow, ipcMain, net, protocol, shell } from 'electron';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

import { runtimeStatusSchema, type RuntimeStatus } from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from './channels';
import { JsonlLogger } from './logging';
import { WorkerClient, type WorkerLifecycleEvent } from './worker-client';
import { resolveWorkerLocation } from './worker-location';

let mainWindow: BrowserWindow | null = null;
let workerClient: WorkerClient | null = null;
let restartPromise: Promise<RuntimeStatus> | null = null;
let quitting = false;

declare const __APPLICATION_VERSION__: string;

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
    const handshake = await client.request('system.handshake');
    const storage = await client.request('storage.health');
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

function registerIpc(logPath: string): void {
  ipcMain.handle(IPC_CHANNELS.getStatus, () => snapshotStatus());
  ipcMain.handle(IPC_CHANNELS.ping, async () => {
    const client = workerClient;
    if (client === null) throw new Error('worker_unavailable');
    const response = await client.request('system.ping');
    if (!response.ok || response.result.pong !== true) throw new Error('worker_ping_failed');
    return refreshStatus();
  });
  ipcMain.handle(IPC_CHANNELS.restart, () => restartWorker());
  ipcMain.handle(IPC_CHANNELS.openLog, async () => {
    const result = await shell.openPath(logPath);
    if (result !== '') throw new Error(`open_log_failed:${result}`);
  });
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
  const ping = await workerClient?.request('system.ping');
  await mkdir(dirname(smokeOutput), { recursive: true });
  await writeFile(
    smokeOutput,
    JSON.stringify(
      {
        schemaVersion: 1,
        passed:
          runtime.workerStatus === 'ready' && runtime.sqliteStatus === 'ok' && ping?.ok === true,
        runtime,
        pingOk: ping?.ok === true,
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
    registerIpc(logPath);
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
