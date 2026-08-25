import { app, BrowserWindow, ipcMain, shell } from 'electron';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

import { runtimeStatusSchema, type RuntimeStatus } from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from './channels';
import { JsonlLogger } from './logging';
import { WorkerClient } from './worker-client';
import { resolveWorkerLocation } from './worker-location';

let mainWindow: BrowserWindow | null = null;
let workerClient: WorkerClient | null = null;
let quitting = false;

declare const __APPLICATION_VERSION__: string;

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

async function refreshStatus(): Promise<RuntimeStatus> {
  const client = workerClient;
  if (client === null) return snapshotStatus();
  try {
    const handshake = await client.request('system.handshake');
    const storage = await client.request('storage.health');
    if (!handshake.ok || !storage.ok) throw new Error('worker_health_failed');
    const workerVersion = handshake.result['workerVersion'];
    const protocolVersions = handshake.result['protocolVersions'];
    status.workerStatus = 'ready';
    status.workerVersion = typeof workerVersion === 'string' ? workerVersion : null;
    status.protocolVersion =
      Array.isArray(protocolVersions) && protocolVersions[0] === 1 ? 1 : null;
    status.sqliteStatus = storage.result['status'] === 'ok' ? 'ok' : 'error';
    status.message = 'Локальный контур готов к работе.';
  } catch (error) {
    status.workerStatus = 'unavailable';
    status.sqliteStatus = 'error';
    status.message = `Worker недоступен: ${String(error)}`;
  }
  return snapshotStatus();
}

function registerIpc(logPath: string): void {
  ipcMain.handle(IPC_CHANNELS.getStatus, () => snapshotStatus());
  ipcMain.handle(IPC_CHANNELS.ping, async () => {
    const response = await workerClient?.request('system.ping');
    if (response?.ok !== true) throw new Error('worker_ping_failed');
    return refreshStatus();
  });
  ipcMain.handle(IPC_CHANNELS.openLog, async () => {
    const result = await shell.openPath(logPath);
    if (result !== '') throw new Error(`open_log_failed:${result}`);
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
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event) => event.preventDefault());
  mainWindow.webContents.session.setPermissionRequestHandler(
    (_webContents, _permission, callback) => callback(false),
  );
  mainWindow.once('ready-to-show', () => {
    if (process.env['IMPELLER_SMOKE_OUTPUT'] === undefined) mainWindow?.show();
  });
  if (process.env['ELECTRON_RENDERER_URL'] !== undefined) {
    await mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL']);
  } else {
    await mainWindow.loadFile(join(__dirname, '../renderer/index.html'));
  }
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
      },
      null,
      2,
    ),
    'utf8',
  );
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
    );
    registerIpc(logPath);
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
