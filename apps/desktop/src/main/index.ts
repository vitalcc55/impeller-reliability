import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  shell,
  type OpenDialogOptions,
  type OpenDialogReturnValue,
  type SaveDialogOptions,
} from 'electron';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
  caseDocumentAttachFileCommandSchema,
  caseDocumentCreateCommandSchema,
  caseDocumentIdPayloadSchema,
  caseDocumentListPayloadSchema,
  caseDocumentRevisionPayloadSchema,
  caseDocumentUpdatePayloadSchema,
  customerUpsertPayloadSchema,
  projectDraftSchema,
  projectUpdateMetadataPayloadSchema,
  materialPagePayloadSchema,
  materialInspectionPayloadSchema,
  materialReadPayloadSchema,
  materialOpenPayloadSchema,
  materialCancelOpenPayloadSchema,
  materialCopyReleaseDecisionSchema,
  importedRunBindingCommandSchema,
  importedRunEnrichmentResolutionCommandSchema,
  importedRunIdPayloadSchema,
  importedRunResolutionStatePayloadSchema,
  reliabilityDatasetCreateVersionCommandSchema,
  reliabilityDatasetVersionIdPayloadSchema,
  reliabilityExecutionIdPayloadSchema,
  reliabilityObservationCreateVersionCommandSchema,
  reliabilityObservationVersionIdPayloadSchema,
  reliabilityPagePayloadSchema,
  pmnCalculationCreateCommandSchema,
  pmnCalculationIdPayloadSchema,
  pmnCalculationListPagePayloadSchema,
  pmnSourceInputsPayloadSchema,
  rbdCalculationCreateCommandSchema,
  rbdCalculationIdPayloadSchema,
  rbdSourceInputsPayloadSchema,
  rptCalculationCreateCommandSchema,
  rptCalculationIdPayloadSchema,
  rptCalculationListPagePayloadSchema,
  rptSourceInputsPayloadSchema,
  runPackageImportJobPayloadSchema,
  runPackageImportStartCommandSchema,
  runPackageValidationJobPayloadSchema,
  runPackageValidationStartCommandSchema,
  runtimeStatusSchema,
  specimenCreatePayloadSchema,
  specimenIdPayloadSchema,
  specimenListPayloadSchema,
  specimenRevisionPayloadSchema,
  specimenUpdatePayloadSchema,
  wheelModelCreatePayloadSchema,
  wheelModelIdPayloadSchema,
  wheelModelListPayloadSchema,
  wheelModelRevisionPayloadSchema,
  wheelModelUpdatePayloadSchema,
  type DesktopError,
  type DesktopResult,
  type CaseDocument,
  type ProjectDraft,
  type ProjectOverview,
  type RecentProject,
  type RunPackageValidationJob,
  type MaterialOrigin,
  type ImportedRunDetail,
  type RunPackageImportJob,
  type SpecimenBinding,
  type RuntimeStatus,
  type WorkerErrorResponse,
} from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from './channels';
import {
  runCaseDocumentAttachFile,
  runCaseDocumentCreateWithFile,
  selectCaseDocumentSource,
} from './case-document-source';
import { JsonlLogger } from './logging';
import { MaterialCopies, MaterialOpener } from './material-open';
import { MaterialCopyConsent } from './material-copy-consent';
import { runSourceMaterialSmoke, type SourceMaterialSmokeEvidence } from './source-material-smoke';
import { RecentProjectsStore } from './recent-projects';
import {
  runPackageImportStart,
  runPackageValidationStart,
  selectRunPackageSource,
} from './run-package-source';
import { showSystemDialog } from './system-dialog';
import { WorkerClient, type WorkerLifecycleEvent } from './worker-client';
import { resolveWorkerLocation } from './worker-location';

let mainWindow: BrowserWindow | null = null;
let workerClient: WorkerClient | null = null;
let restartPromise: Promise<RuntimeStatus> | null = null;
let quitting = false;
type RendererCloseState = 'idle' | 'waiting-for-decision' | 'approved';
let rendererCloseState: RendererCloseState = 'idle';
let rendererReady = false;
let rendererUnavailable = false;
let closeDeliveryTimer: ReturnType<typeof setTimeout> | null = null;
let activeProjectAuthorization: { readonly path: string; readonly projectId: string } | null = null;
let materialSessionEpoch = 0;
let materialOpener: MaterialOpener | null = null;
const materialCopyConsent = new MaterialCopyConsent(materialSession);
function invalidateMaterialOpenings(): void {
  materialSessionEpoch += 1;
  materialOpener?.invalidate();
}
function materialSession(): { readonly projectId: string; readonly epoch: number } | null {
  return activeProjectAuthorization === null ||
    status.workerStatus !== 'ready' ||
    quitting ||
    restartPromise !== null
    ? null
    : { projectId: activeProjectAuthorization.projectId, epoch: materialSessionEpoch };
}
const applicationInstanceId = randomUUID();
const RENDERER_CLOSE_ACK_TIMEOUT_MS = 2_000;

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
  if (event.state !== 'ready') invalidateMaterialOpenings();
  status.workerStatus = event.state;
  if (event.state === 'starting') {
    status.sqliteStatus = 'pending';
    status.message = 'Запуск локального расчётного контура…';
  } else if (event.state === 'ready') {
    status.message = 'Локальный контур готов к работе.';
  } else if (event.state === 'unavailable') {
    status.sqliteStatus = 'error';
    status.message = 'Worker недоступен. Откройте диагностику и перезапустите ядро.';
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
  } catch {
    status.workerStatus = 'unavailable';
    status.sqliteStatus = 'error';
    status.message = 'Worker недоступен. Откройте диагностику и перезапустите ядро.';
    emitStatus();
  }
  return snapshotStatus();
}

function restartWorker(): Promise<RuntimeStatus> {
  if (restartPromise !== null) return restartPromise;
  const client = workerClient;
  if (client === null) return Promise.reject(new Error('worker_unavailable'));
  invalidateMaterialOpenings();
  const currentRestart = (async () => {
    await materialOpener?.drain();
    await client.restart();
    return refreshStatus();
  })().finally(() => {
    if (restartPromise === currentRestart) restartPromise = null;
  });
  restartPromise = currentRestart;
  return currentRestart;
}

function registerIpc(logPath: string, stateDirectory: string, logger: JsonlLogger): MaterialCopies {
  const recentProjects = new RecentProjectsStore(join(stateDirectory, 'recent-projects.json'));
  const copies = new MaterialCopies(
    join(stateDirectory, 'source-material-copies'),
    async (command) => {
      const client = workerClient;
      if (client === null) throw new Error('worker_unavailable');
      const response = await client.request('materialCopy.discard', command);
      if (!response.ok) throw new Error(`material_copy_discard_failed:${response.error.code}`);
    },
    () => workerClient?.processId ?? null,
  );
  materialOpener = new MaterialOpener({
    copies,
    session: materialSession,
    resolve: (identity, directory, copyId, byteLimit) =>
      runProjectOperation(workerClient, async (client) =>
        client.request('importedRun.resolveMaterial', {
          identity,
          outputDirectory: directory,
          copyId,
          copyByteLimit: byteLimit,
        }),
      ),
    openPath: (path) => shell.openPath(path),
    confirmRelease: (signal, command, session, requesterId) => {
      const window = mainWindow;
      if (
        signal.aborted ||
        window === null ||
        window.isDestroyed() ||
        requesterId !== window.webContents.id
      )
        return Promise.resolve(false);
      return materialCopyConsent.request(command, session, signal, requesterId, (request) => {
        window.webContents.send(IPC_CHANNELS.importedRunCopyReleaseRequested, request);
      });
    },
    cleanupFailed: () => {
      void logger
        .write({ severity: 'warning', component: 'material-copies', event: 'cleanup_failed' })
        .catch(() => {
          status.message = 'Не удалось очистить временную копию просмотра. Откройте диагностику.';
          emitStatus();
        });
    },
  });

  ipcMain.handle(IPC_CHANNELS.getStatus, () => {
    rendererReady = true;
    rendererUnavailable = false;
    return snapshotStatus();
  });
  ipcMain.handle(IPC_CHANNELS.ping, async () => {
    const client = workerClient;
    if (client === null) throw new Error('worker_unavailable');
    const response = await client.request('system.ping', {});
    if (!response.ok || response.result.pong !== true) throw new Error('worker_ping_failed');
    return refreshStatus();
  });
  ipcMain.handle(IPC_CHANNELS.restart, () => restartWorker());
  ipcMain.handle(IPC_CHANNELS.closeAcknowledged, () => clearCloseDeliveryTimer());
  ipcMain.handle(IPC_CHANNELS.confirmClose, () => {
    if (rendererCloseState !== 'waiting-for-decision') return;
    clearCloseDeliveryTimer();
    rendererCloseState = 'approved';
    app.quit();
  });
  ipcMain.handle(IPC_CHANNELS.cancelClose, () => {
    if (rendererCloseState !== 'waiting-for-decision') return;
    clearCloseDeliveryTimer();
    rendererCloseState = 'idle';
  });
  ipcMain.handle(IPC_CHANNELS.openLog, async () => {
    const result = await shell.openPath(logPath);
    if (result !== '') throw new Error(`open_log_failed:${result}`);
  });
  ipcMain.handle(IPC_CHANNELS.projectCreate, async (_event, rawDraft: unknown) => {
    invalidateMaterialOpenings();
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
    const dialogResult = await showSystemDialog(() =>
      mainWindow === null
        ? dialog.showSaveDialog(options)
        : dialog.showSaveDialog(mainWindow, options),
    );
    if (!dialogResult.ok) return { ok: false, error: dialogResult.error };
    const selection = dialogResult.result;
    if (selection.canceled || selection.filePath === '') return cancelledResult<ProjectOverview>();
    const path = selection.filePath.toLowerCase().endsWith('.irproj')
      ? selection.filePath
      : `${selection.filePath}.irproj`;
    return createProject(workerClient, recentProjects, logger, path, draft);
  });
  ipcMain.handle(IPC_CHANNELS.projectOpen, async () => {
    invalidateMaterialOpenings();
    const automatedPath = approvedAutomatedProjectPath();
    if (automatedPath !== null) {
      return openProject(workerClient, recentProjects, logger, automatedPath);
    }
    const options: OpenDialogOptions = {
      title: 'Открыть проект Impeller Reliability',
      buttonLabel: 'Открыть проект',
      properties: ['openDirectory'],
    };
    const dialogResult = await showSystemDialog(() =>
      mainWindow === null
        ? dialog.showOpenDialog(options)
        : dialog.showOpenDialog(mainWindow, options),
    );
    if (!dialogResult.ok) return { ok: false, error: dialogResult.error };
    const selection: OpenDialogReturnValue = dialogResult.result;
    if (selection.canceled || selection.filePaths[0] === undefined) {
      return cancelledResult<ProjectOverview>();
    }
    return openProject(workerClient, recentProjects, logger, selection.filePaths[0]);
  });
  ipcMain.handle(IPC_CHANNELS.projectOpenRecent, async (_event, rawPath: unknown) => {
    invalidateMaterialOpenings();
    if (typeof rawPath !== 'string') {
      return failureResult<ProjectOverview>(
        'validation_error',
        'Путь не входит в список недавних проектов.',
      );
    }
    const activeAuthorization = activeProjectAuthorization;
    if (activeAuthorization?.path !== rawPath) {
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
    }
    return openProject(
      workerClient,
      recentProjects,
      logger,
      rawPath,
      activeAuthorization?.path === rawPath ? activeAuthorization.projectId : undefined,
    );
  });
  ipcMain.handle(IPC_CHANNELS.projectClose, async () => {
    invalidateMaterialOpenings();
    const result = await runProjectOperation(workerClient, async (client) =>
      client.request('project.close', {}),
    );
    if (result.ok) activeProjectAuthorization = null;
    return result;
  });
  ipcMain.handle(IPC_CHANNELS.projectReleaseLocalWorkspace, () => {
    invalidateMaterialOpenings();
    activeProjectAuthorization = null;
  });
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
  ipcMain.handle(IPC_CHANNELS.customerGet, () =>
    runProjectOperation(workerClient, async (client) => client.request('caseCustomer.get', {})),
  );
  ipcMain.handle(IPC_CHANNELS.customerUpsert, (_event, raw: unknown) => {
    const parsed = customerUpsertPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseCustomer.upsert', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.wheelModelCreate, (_event, raw: unknown) => {
    const parsed = wheelModelCreatePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('wheelModel.create', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.wheelModelList, (_event, raw: unknown) => {
    const parsed = wheelModelListPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('wheelModel.list', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.wheelModelGet, (_event, raw: unknown) => {
    const parsed = wheelModelIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('wheelModel.get', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.wheelModelUpdate, (_event, raw: unknown) => {
    const parsed = wheelModelUpdatePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('wheelModel.update', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.wheelModelArchive, (_event, raw: unknown) => {
    const parsed = wheelModelRevisionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('wheelModel.archive', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.wheelModelRestore, (_event, raw: unknown) => {
    const parsed = wheelModelRevisionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('wheelModel.restore', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.specimenCreate, (_event, raw: unknown) => {
    const parsed = specimenCreatePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('specimen.create', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.specimenList, (_event, raw: unknown) => {
    const parsed = specimenListPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('specimen.list', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.specimenGet, (_event, raw: unknown) => {
    const parsed = specimenIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('specimen.get', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.specimenUpdate, (_event, raw: unknown) => {
    const parsed = specimenUpdatePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('specimen.update', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.specimenArchive, (_event, raw: unknown) => {
    const parsed = specimenRevisionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('specimen.archive', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.specimenRestore, (_event, raw: unknown) => {
    const parsed = specimenRevisionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('specimen.restore', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentCreate, (_event, raw: unknown) => {
    const parsed = caseDocumentCreateCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.create', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentCreateWithFile, async (_event, raw: unknown) => {
    const parsed = caseDocumentCreateCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<CaseDocument>();
    return runCaseDocumentCreateWithFile(
      parsed.data,
      selectCaseDocumentSourceFromDialog,
      (payload) =>
        runProjectOperation(workerClient, async (client) =>
          client.request('caseDocument.createWithFile', payload),
        ),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentList, (_event, raw: unknown) => {
    const parsed = caseDocumentListPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.list', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentGet, (_event, raw: unknown) => {
    const parsed = caseDocumentIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.get', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentUpdate, (_event, raw: unknown) => {
    const parsed = caseDocumentUpdatePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.update', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentAttachFile, async (_event, raw: unknown) => {
    const parsed = caseDocumentAttachFileCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<CaseDocument>();
    return runCaseDocumentAttachFile(parsed.data, selectCaseDocumentSourceFromDialog, (payload) =>
      runProjectOperation(workerClient, async (client) =>
        client.request('caseDocument.attachFile', payload),
      ),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentVerifyFile, (_event, raw: unknown) => {
    const parsed = caseDocumentIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.verifyFile', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentOpenFile, async (_event, raw: unknown) => {
    const parsed = caseDocumentIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<{ readonly opened: boolean }>();
    const resolved = await runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.resolveFile', parsed.data),
    );
    if (!resolved.ok) return resolved;
    if (process.env['NODE_ENV'] === 'test') {
      return { ok: true, result: { opened: true } };
    }
    try {
      const shellError = await shell.openPath(resolved.result.absolutePath);
      if (shellError !== '') {
        return failureResult<{ readonly opened: boolean }>(
          'storage_error',
          'Управляемую копию документа не удалось открыть.',
        );
      }
      return { ok: true, result: { opened: true } };
    } catch {
      return failureResult<{ readonly opened: boolean }>(
        'storage_error',
        'Управляемую копию документа не удалось открыть.',
      );
    }
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentArchive, (_event, raw: unknown) => {
    const parsed = caseDocumentRevisionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.archive', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.caseDocumentRestore, (_event, raw: unknown) => {
    const parsed = caseDocumentRevisionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('caseDocument.restore', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageValidationStart, async (_event, raw: unknown) => {
    const parsed = runPackageValidationStartCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<RunPackageValidationJob>();
    return runPackageValidationStart(parsed.data, selectRunPackageSourceFromDialog, (payload) =>
      runDiagnosticOperation(workerClient, async (client) =>
        client.request('runPackageValidation.start', payload),
      ),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageValidationGet, (_event, raw: unknown) => {
    const parsed = runPackageValidationJobPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<RunPackageValidationJob>();
    return runDiagnosticOperation(workerClient, async (client) =>
      client.request('runPackageValidation.get', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageValidationCancel, (_event, raw: unknown) => {
    const parsed = runPackageValidationJobPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<RunPackageValidationJob>();
    return runDiagnosticOperation(workerClient, async (client) =>
      client.request('runPackageValidation.cancel', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageValidationDiscard, (_event, raw: unknown) => {
    const parsed = runPackageValidationJobPayloadSchema.safeParse(raw);
    if (!parsed.success)
      return validationFailure<{ readonly jobId: string; readonly discarded: true }>();
    return runDiagnosticOperation(workerClient, async (client) =>
      client.request('runPackageValidation.discard', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageImportStart, async (_event, raw: unknown) => {
    const parsed = runPackageImportStartCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<RunPackageImportJob>();
    return runPackageImportStart(parsed.data, selectRunPackageSourceForImport, (payload) =>
      runProjectOperation(workerClient, async (client) =>
        client.request('runPackageImport.start', payload),
      ),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageImportGet, (_event, raw: unknown) => {
    const parsed = runPackageImportJobPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<RunPackageImportJob>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('runPackageImport.get', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageImportCancel, (_event, raw: unknown) => {
    const parsed = runPackageImportJobPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<RunPackageImportJob>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('runPackageImport.cancel', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.runPackageImportDiscard, (_event, raw: unknown) => {
    const parsed = runPackageImportJobPayloadSchema.safeParse(raw);
    if (!parsed.success)
      return validationFailure<{ readonly jobId: string; readonly discarded: true }>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('runPackageImport.discard', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunList, () =>
    runProjectOperation(workerClient, async (client) => client.request('importedRun.list', {})),
  );
  ipcMain.handle(IPC_CHANNELS.importedRunGet, (_event, raw: unknown) => {
    const parsed = importedRunIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<ImportedRunDetail>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('importedRun.get', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunListInspectionPage, (_event, raw: unknown) => {
    const parsed = materialPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runMaterialRead(parsed.data.origin, async (client) =>
      client.request('importedRun.listInspectionPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunGetInspection, (_event, raw: unknown) => {
    const parsed = materialInspectionPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runMaterialRead(parsed.data.origin, async (client) =>
      client.request('importedRun.getInspection', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunListPhotoPage, (_event, raw: unknown) => {
    const parsed = materialPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runMaterialRead(parsed.data.origin, async (client) =>
      client.request('importedRun.listPhotoPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunGetProtocol, (_event, raw: unknown) => {
    const parsed = materialReadPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runMaterialRead(parsed.data.origin, async (client) =>
      client.request('importedRun.getProtocol', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunOpenMaterial, (event, raw: unknown) => {
    const parsed = materialOpenPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    if (mainWindow === null || event.sender !== mainWindow.webContents) return validationFailure();
    if (materialOpener === null)
      return failureResult('worker_unavailable', 'Открытие материалов недоступно.');
    return materialOpener.open(parsed.data, event.sender.id);
  });
  ipcMain.handle(IPC_CHANNELS.importedRunCopyReleaseDecision, (event, raw: unknown) => {
    const parsed = materialCopyReleaseDecisionSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return {
      ok: true,
      result: { accepted: materialCopyConsent.answer(parsed.data, event.sender.id) },
    };
  });
  ipcMain.handle(IPC_CHANNELS.importedRunCancelMaterialOpen, (_event, raw: unknown) => {
    const parsed = materialCancelOpenPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return {
      ok: true,
      result: { cancelled: materialOpener?.cancel(parsed.data.operationId) ?? false },
    };
  });
  ipcMain.handle(IPC_CHANNELS.importedRunVerifySource, (_event, raw: unknown) => {
    const parsed = importedRunIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('importedRun.verifySource', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunGetResolutionState, (_event, raw: unknown) => {
    const parsed = importedRunResolutionStatePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<SpecimenBinding>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('importedRun.getResolutionState', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunBindSpecimen, (_event, raw: unknown) => {
    const parsed = importedRunBindingCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<SpecimenBinding>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('importedRun.bindSpecimen', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.importedRunApplyEnrichmentResolution, (_event, raw: unknown) => {
    const parsed = importedRunEnrichmentResolutionCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure<ImportedRunDetail>();
    return runProjectOperation(workerClient, async (client) =>
      client.request('importedRun.applyEnrichmentResolution', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityExecutionMaterialize, (_event, raw: unknown) => {
    const parsed = importedRunIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityExecution.materialize', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityExecutionListPage, (_event, raw: unknown) => {
    const parsed = reliabilityPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityExecution.listPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityExecutionGetDetail, (_event, raw: unknown) => {
    const parsed = reliabilityExecutionIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityExecution.getDetail', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityObservationListVersions, (_event, raw: unknown) => {
    const parsed = reliabilityExecutionIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityObservation.listVersions', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityObservationGetVersion, (_event, raw: unknown) => {
    const parsed = reliabilityObservationVersionIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityObservation.getVersion', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityObservationCreateVersion, (_event, raw: unknown) => {
    const parsed = reliabilityObservationCreateVersionCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityObservation.createVersion', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityDatasetListPage, (_event, raw: unknown) => {
    const parsed = reliabilityPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityDataset.listPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityDatasetGetVersion, (_event, raw: unknown) => {
    const parsed = reliabilityDatasetVersionIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityDataset.getVersion', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.reliabilityDatasetCreateVersion, (_event, raw: unknown) => {
    const parsed = reliabilityDatasetCreateVersionCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('reliabilityDataset.createVersion', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rbdCalculationGetSourceInputs, (_event, raw: unknown) => {
    const parsed = rbdSourceInputsPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rbdCalculation.getSourceInputs', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rbdCalculationCreate, (_event, raw: unknown) => {
    const parsed = rbdCalculationCreateCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rbdCalculation.create', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rbdCalculationListPage, (_event, raw: unknown) => {
    const parsed = reliabilityPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rbdCalculation.listPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rbdCalculationGetDetail, (_event, raw: unknown) => {
    const parsed = rbdCalculationIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rbdCalculation.getDetail', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rptCalculationGetSourceInputs, (_event, raw: unknown) => {
    const parsed = rptSourceInputsPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rptCalculation.getSourceInputs', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rptCalculationCreate, (_event, raw: unknown) => {
    const parsed = rptCalculationCreateCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rptCalculation.create', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rptCalculationListPage, (_event, raw: unknown) => {
    const parsed = rptCalculationListPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rptCalculation.listPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.rptCalculationGetDetail, (_event, raw: unknown) => {
    const parsed = rptCalculationIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('rptCalculation.getDetail', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.pmnCalculationGetSourceInputs, (_event, raw: unknown) => {
    const parsed = pmnSourceInputsPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('pmnCalculation.getSourceInputs', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.pmnCalculationCreate, (_event, raw: unknown) => {
    const parsed = pmnCalculationCreateCommandSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('pmnCalculation.create', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.pmnCalculationListPage, (_event, raw: unknown) => {
    const parsed = pmnCalculationListPagePayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('pmnCalculation.listPage', parsed.data),
    );
  });
  ipcMain.handle(IPC_CHANNELS.pmnCalculationGetDetail, (_event, raw: unknown) => {
    const parsed = pmnCalculationIdPayloadSchema.safeParse(raw);
    if (!parsed.success) return validationFailure();
    return runProjectOperation(workerClient, async (client) =>
      client.request('pmnCalculation.getDetail', parsed.data),
    );
  });
  return copies;
}

function selectRunPackageSourceFromDialog(): Promise<DesktopResult<string>> {
  return selectRunPackageSource({
    automatedCancelled: automatedRunPackageSelectionCancelled(),
    automatedPath: approvedAutomatedRunPackagePath(),
    showOpenDialog: (options) => {
      if (automatedRunPackageDialogRejected())
        return Promise.reject(new Error('automated_run_package_dialog_rejected'));
      return mainWindow === null
        ? dialog.showOpenDialog(options)
        : dialog.showOpenDialog(mainWindow, options);
    },
  });
}

function selectRunPackageSourceForImport(): Promise<DesktopResult<string>> {
  return selectRunPackageSource({
    automatedCancelled: automatedRunPackageSelectionCancelled(),
    automatedPath: approvedAutomatedRunPackagePath(),
    buttonLabel: 'Импортировать результат',
    showOpenDialog: (options) => {
      if (automatedRunPackageDialogRejected())
        return Promise.reject(new Error('automated_run_package_dialog_rejected'));
      return mainWindow === null
        ? dialog.showOpenDialog(options)
        : dialog.showOpenDialog(mainWindow, options);
    },
  });
}

function selectCaseDocumentSourceFromDialog(): Promise<DesktopResult<string>> {
  return selectCaseDocumentSource({
    automatedCancelled: automatedDocumentSelectionCancelled(),
    automatedPath: approvedAutomatedDocumentPath(),
    showOpenDialog: (options) =>
      mainWindow === null
        ? dialog.showOpenDialog(options)
        : dialog.showOpenDialog(mainWindow, options),
  });
}

function approvedAutomatedDocumentPath(): string | null {
  const isAutomated =
    process.env['NODE_ENV'] === 'test' || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined;
  if (!isAutomated) return null;
  const rawPath = process.env['IMPELLER_AUTOMATED_DOCUMENT_PATH'];
  return rawPath === undefined ? null : resolve(rawPath);
}

function automatedDocumentSelectionCancelled(): boolean {
  const isAutomated =
    process.env['NODE_ENV'] === 'test' || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined;
  return isAutomated && process.env['IMPELLER_AUTOMATED_DOCUMENT_CANCELLED'] === '1';
}

function approvedAutomatedRunPackagePath(): string | null {
  const isAutomated =
    process.env['NODE_ENV'] === 'test' || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined;
  if (!isAutomated) return null;
  const rawPath = process.env['IMPELLER_AUTOMATED_R130RUN_PATH'];
  return rawPath === undefined ? null : resolve(rawPath);
}

function automatedRunPackageSelectionCancelled(): boolean {
  const isAutomated =
    process.env['NODE_ENV'] === 'test' || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined;
  return isAutomated && process.env['IMPELLER_AUTOMATED_R130RUN_CANCELLED'] === '1';
}

function automatedRunPackageDialogRejected(): boolean {
  const isAutomated =
    process.env['NODE_ENV'] === 'test' || process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined;
  return isAutomated && process.env['IMPELLER_AUTOMATED_R130RUN_DIALOG_REJECTED'] === '1';
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
  if (result.ok) {
    activeProjectAuthorization = {
      path: result.result.path,
      projectId: result.result.projectId,
    };
    await touchRecentSafely(recentProjects, result.result, logger);
  }
  return result;
}

async function openProject(
  client: WorkerClient | null,
  recentProjects: RecentProjectsStore,
  logger: JsonlLogger,
  path: string,
  expectedProjectId?: string,
): Promise<DesktopResult<ProjectOverview>> {
  const result = await runProjectOperation(client, async (readyClient) =>
    readyClient.request('project.open', { path, applicationInstanceId }),
  );
  if (
    result.ok &&
    expectedProjectId !== undefined &&
    result.result.projectId !== expectedProjectId
  ) {
    await runProjectOperation(client, async (readyClient) =>
      readyClient.request('project.close', {}),
    );
    return failureResult('corrupt_project', 'По выбранному пути находится другой проект.');
  }
  if (result.ok) {
    activeProjectAuthorization = {
      path: result.result.path,
      projectId: result.result.projectId,
    };
    await touchRecentSafely(recentProjects, result.result, logger);
  }
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

async function runMaterialRead<TResult>(
  origin: MaterialOrigin,
  operation: (client: WorkerClient) => Promise<OperationResponse<TResult>>,
): Promise<DesktopResult<TResult>> {
  const session = materialSession();
  if (session === null || session.projectId !== origin.projectId)
    return failureResult('cancelled', 'Материалы не относятся к активной сессии дела.');
  const result = await runProjectOperation(workerClient, operation);
  const current = materialSession();
  if (
    current === null ||
    current.epoch !== session.epoch ||
    current.projectId !== session.projectId
  )
    return failureResult('cancelled', 'Чтение материалов отменено при смене сессии.');
  return result;
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
    if (
      error instanceof Error &&
      (error.message === 'worker_queue_full' || error.message === 'worker_stopping')
    ) {
      return failureResult(
        'operation_in_progress',
        'Дождитесь завершения текущей операции с проектом.',
      );
    }
    return failureResult(
      'worker_unavailable',
      'Операция с проектом не выполнена: локальный worker недоступен.',
    );
  }
}

async function runDiagnosticOperation<TResult>(
  client: WorkerClient | null,
  operation: (readyClient: WorkerClient) => Promise<OperationResponse<TResult>>,
): Promise<DesktopResult<TResult>> {
  if (client === null) return failureResult('worker_unavailable', 'Расчётное ядро недоступно.');
  try {
    const response = await operation(client);
    return response.ok ? { ok: true, result: response.result } : fromWorkerError(response);
  } catch (error) {
    if (
      error instanceof Error &&
      (error.message === 'worker_queue_full' || error.message === 'worker_stopping')
    ) {
      return failureResult('operation_in_progress', 'Дождитесь завершения текущей операции.');
    }
    return failureResult('worker_unavailable', 'Проверка прервана: локальный worker недоступен.');
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

function validationFailure<TResult>(): DesktopResult<TResult> {
  return failureResult('validation_error', 'Проверьте заполненные значения.');
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

function clearCloseDeliveryTimer(): void {
  if (closeDeliveryTimer === null) return;
  clearTimeout(closeDeliveryTimer);
  closeDeliveryTimer = null;
}

function closeWithoutRendererIfPending(): void {
  if (rendererCloseState !== 'waiting-for-decision') return;
  clearCloseDeliveryTimer();
  rendererCloseState = 'approved';
  app.quit();
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1536,
    height: 864,
    minWidth: 1280,
    minHeight: 720,
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
  mainWindow.webContents.on('did-start-loading', () => {
    invalidateMaterialOpenings();
    rendererReady = false;
    rendererUnavailable = false;
    closeWithoutRendererIfPending();
  });
  mainWindow.webContents.on('render-process-gone', () => {
    invalidateMaterialOpenings();
    rendererUnavailable = true;
    closeWithoutRendererIfPending();
  });
  mainWindow.webContents.on('unresponsive', () => {
    rendererUnavailable = true;
    closeWithoutRendererIfPending();
  });
  mainWindow.webContents.on('responsive', () => {
    rendererUnavailable = false;
  });
  mainWindow.webContents.on('will-navigate', (event, targetUrl) => {
    if (targetUrl !== rendererUrl) event.preventDefault();
  });
  mainWindow.webContents.session.setPermissionRequestHandler(
    (_webContents, _permission, callback) => callback(false),
  );
  mainWindow.on('close', (event) => {
    if (
      quitting ||
      rendererCloseState === 'approved' ||
      process.env['IMPELLER_SMOKE_OUTPUT'] !== undefined
    )
      return;
    if (!rendererReady || rendererUnavailable) return;
    event.preventDefault();
    if (rendererCloseState === 'idle') {
      rendererCloseState = 'waiting-for-decision';
      mainWindow?.webContents.send(IPC_CHANNELS.closeRequested);
      closeDeliveryTimer = setTimeout(closeWithoutRendererIfPending, RENDERER_CLOSE_ACK_TIMEOUT_MS);
    }
  });
  mainWindow.once('ready-to-show', () => {
    if (process.env['IMPELLER_SMOKE_OUTPUT'] === undefined) mainWindow?.show();
  });
  await mainWindow.loadURL(rendererUrl);
}

async function runSmokeIfRequested(copies: MaterialCopies): Promise<void> {
  const smokeOutput = process.env['IMPELLER_SMOKE_OUTPUT'];
  if (smokeOutput === undefined) return;
  const startedAt = performance.now();
  const runtime = await refreshStatus();
  const ping = await workerClient?.request('system.ping', {});
  const automatedProjectPath = approvedAutomatedProjectPath();
  let projectScenarioPassed = false;
  let runPackageValidationPassed = false;
  let runPackageImportPassed = false;
  let rbdCalculationPassed = false;
  let rptCalculationPassed = false;
  let pmnCalculationPassed = false;
  let sourceMaterials: SourceMaterialSmokeEvidence | null = null;
  let sourceMaterialsError: string | null = null;
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
      if (updated.ok && closed.ok && reopened.ok) {
        const customer = await workerClient.request('caseCustomer.upsert', {
          expectedRevision: null,
          customer: {
            fullName: 'Smoke customer',
            legalAddress: '',
            actualAddress: '',
            notes: '',
          },
        });
        const smokeWheelId = randomUUID();
        const smokeSpecimenId = randomUUID();
        const smokeDocumentId = randomUUID();
        const wheel = await workerClient.request('wheelModel.create', {
          wheelModelId: smokeWheelId,
          fullName: 'Smoke wheel',
          designation: 'SM-W',
          nominalDiameterMm: '500',
          nominalSpeedRpm: 1500,
          bladeCount: 12,
          geometryDescription: '',
          compositionDescription: '',
          materialDescription: '',
          notes: '',
        });
        const specimen = wheel.ok
          ? await workerClient.request('specimen.create', {
              specimenId: smokeSpecimenId,
              wheelModelId: wheel.result.wheelModelId,
              identificationNumber: 'SMOKE-SN-1',
              batchNumber: '',
              marking: '',
              manufacturedOn: null,
              receivedOn: null,
              workingDiameterMm: '499.5',
              initialConditionNotes: '',
              notes: '',
            })
          : null;
        const smokeDocumentSource = resolve(dirname(smokeOutput), 'case-document-source.pdf');
        await writeFile(smokeDocumentSource, '%PDF-1.7\nPackaged managed document\n', 'utf8');
        const document =
          wheel.ok && specimen?.ok === true
            ? await workerClient.request('caseDocument.createWithFile', {
                caseDocumentId: smokeDocumentId,
                document: {
                  documentKind: 'technical_specification',
                  title: 'Smoke technical specification',
                  designation: 'SM-TU-1',
                  revisionLabel: 'Revision 1',
                  documentDate: '2026-08-28',
                  issuer: 'Smoke laboratory',
                  notes: '',
                },
                wheelModelIds: [smokeWheelId],
                specimenIds: [smokeSpecimenId],
                sourcePath: smokeDocumentSource,
              })
            : null;
        const dossierClosed = await workerClient.request('project.close', {});
        const dossierReopened = await workerClient.request('project.open', {
          path: automatedProjectPath,
          applicationInstanceId,
        });
        const customerAfter = await workerClient.request('caseCustomer.get', {});
        const wheelsAfter = await workerClient.request('wheelModel.list', {
          includeArchived: false,
        });
        const specimensAfter = await workerClient.request('specimen.list', {
          includeArchived: false,
        });
        const wheelAfter = await workerClient.request('wheelModel.get', {
          wheelModelId: smokeWheelId,
        });
        const specimenAfter = await workerClient.request('specimen.get', {
          specimenId: smokeSpecimenId,
        });
        const documentsAfter = await workerClient.request('caseDocument.list', {
          includeArchived: false,
          documentKind: 'technical_specification',
        });
        const documentAfter = await workerClient.request('caseDocument.verifyFile', {
          caseDocumentId: smokeDocumentId,
        });
        projectScenarioPassed =
          updated.result.recordRevision === 2 &&
          reopened.result.name === 'Packaged smoke project updated' &&
          reopened.result.projectNumber === 'SMOKE-002' &&
          reopened.result.recordRevision === 2 &&
          customer.ok &&
          wheel.ok &&
          specimen?.ok === true &&
          document?.ok === true &&
          dossierClosed.ok &&
          dossierReopened.ok &&
          customerAfter.ok &&
          customerAfter.result.customer?.fullName === 'Smoke customer' &&
          wheelsAfter.ok &&
          wheelsAfter.result.items.length === 1 &&
          specimensAfter.ok &&
          specimensAfter.result.items.length === 1 &&
          wheelAfter.ok &&
          wheelAfter.result.designation === 'SM-W' &&
          wheelAfter.result.nominalDiameterMm === '500' &&
          wheelAfter.result.recordRevision === 1 &&
          specimenAfter.ok &&
          specimenAfter.result.identificationNumber === 'SMOKE-SN-1' &&
          specimenAfter.result.wheelModelId === smokeWheelId &&
          specimenAfter.result.workingDiameterMm === '499.5' &&
          specimenAfter.result.recordRevision === 1 &&
          documentsAfter.ok &&
          documentsAfter.result.items.length === 1 &&
          documentAfter.ok &&
          documentAfter.result.recordRevision === 1 &&
          documentAfter.result.integrityStatus === 'verified' &&
          documentAfter.result.file?.originalFileName === 'case-document-source.pdf' &&
          documentAfter.result.wheelModelIds[0] === smokeWheelId &&
          documentAfter.result.specimenIds[0] === smokeSpecimenId;
        const runPackagePath = approvedAutomatedRunPackagePath();
        if (runPackagePath !== null) {
          const overviewBeforeValidation = await workerClient.request('project.getOverview', {});
          const validationJobId = randomUUID();
          const validationStarted = await workerClient.request('runPackageValidation.start', {
            jobId: validationJobId,
            sourcePath: runPackagePath,
            validationBudgetMs: 180_000,
          });
          let validation = validationStarted;
          const validationDeadline = performance.now() + 180_000;
          while (
            validation.ok &&
            !['completed', 'failed', 'cancelled'].includes(validation.result.state) &&
            performance.now() < validationDeadline
          ) {
            await new Promise<void>((resolvePoll) => setTimeout(resolvePoll, 25));
            validation = await workerClient.request('runPackageValidation.get', {
              jobId: validationJobId,
            });
          }
          const overviewAfterValidation = await workerClient.request('project.getOverview', {});
          const discarded = await workerClient.request('runPackageValidation.discard', {
            jobId: validationJobId,
          });
          runPackageValidationPassed =
            validation.ok &&
            validation.result.state === 'completed' &&
            validation.result.report?.structuralVerdict === 'passed' &&
            validation.result.report.semanticVerdict === 'passed' &&
            validation.result.report.contractSchema === 'r130sh.run-package.v1' &&
            overviewBeforeValidation.ok &&
            overviewAfterValidation.ok &&
            overviewBeforeValidation.result.recordRevision ===
              overviewAfterValidation.result.recordRevision &&
            discarded.ok;
          const importJobId = randomUUID();
          let imported = await workerClient.request('runPackageImport.start', {
            jobId: importJobId,
            sourcePath: runPackagePath,
            allowDiagnosticPartial: false,
          });
          const importDeadline = performance.now() + 180_000;
          while (
            imported.ok &&
            !['completed', 'failed', 'cancelled'].includes(imported.result.state) &&
            performance.now() < importDeadline
          ) {
            await new Promise<void>((resolvePoll) => setTimeout(resolvePoll, 25));
            imported = await workerClient.request('runPackageImport.get', {
              jobId: importJobId,
            });
          }
          if (
            imported.ok &&
            imported.result.state === 'completed' &&
            imported.result.result !== null
          ) {
            const localImportId = imported.result.result.importedRun.localImportId;
            const listed = await workerClient.request('importedRun.list', {});
            const detail = await workerClient.request('importedRun.get', { localImportId });
            const verified = await workerClient.request('importedRun.verifySource', {
              localImportId,
            });
            const importDiscarded = await workerClient.request('runPackageImport.discard', {
              jobId: importJobId,
            });
            const sourceSpecimenId = imported.result.result.importedRun.sourceSpecimenId;
            const binding = await workerClient.request('importedRun.bindSpecimen', {
              sourceSpecimenId,
              localSpecimenId: smokeSpecimenId,
              expectedRevision: imported.result.result.importedRun.bindingRevision,
              actor: 'local_user',
              reason: 'Проверка расчёта по управляемому архиву',
            });
            const materialized = binding.ok
              ? await workerClient.request('reliabilityExecution.materialize', { localImportId })
              : null;
            const globalDocument =
              materialized?.ok === true
                ? await workerClient.request('caseDocument.create', {
                    caseDocumentId: randomUUID(),
                    document: {
                      documentKind: 'measurement_or_attestation_record',
                      title: 'Smoke RBD basis',
                      designation: 'SM-RBD-1',
                      revisionLabel: 'Revision 1',
                      documentDate: '2026-08-28',
                      issuer: 'Smoke laboratory',
                      notes: '',
                    },
                    wheelModelIds: [],
                    specimenIds: [],
                  })
                : null;
            const sourceInputs =
              materialized?.ok === true
                ? await workerClient.request('rbdCalculation.getSourceInputs', {
                    executionId: materialized.result.executionId,
                    planSelection: 'effective',
                  })
                : null;
            const calculationInputId = randomUUID();
            const calculationResultId = randomUUID();
            const calculation =
              sourceInputs?.ok === true && globalDocument?.ok === true
                ? await workerClient.request('rbdCalculation.create', {
                    analysisInputSnapshotId: calculationInputId,
                    calculationSnapshotId: calculationResultId,
                    executionId: sourceInputs.result.executionId,
                    planSelection: 'effective',
                    selections: (
                      [
                        'nominal_rpm',
                        'base_cycles',
                        'reserve_factor',
                        'acceleration_duration_s',
                        'deceleration_duration_s',
                      ] as const
                    ).map((field) => ({
                      field,
                      origin: field === 'base_cycles' ? ('manual' as const) : ('source' as const),
                      manualValue: field === 'base_cycles' ? '1000' : null,
                      basis: field === 'base_cycles' ? 'Documented smoke basis' : '',
                      evidence:
                        field === 'base_cycles'
                          ? {
                              documentId: globalDocument.result.caseDocumentId,
                              documentRecordRevision: globalDocument.result.recordRevision,
                              documentLocator: 'Section 1',
                              observationVersionId: null,
                            }
                          : null,
                    })),
                    failureEvidence: null,
                    actor: 'local_user',
                    reason: 'Проверка точного требования отдельно от округлённой уставки',
                  })
                : null;
            const rptRunPath = process.env['IMPELLER_AUTOMATED_RPT_RUN_PATH'];
            const rptImportJobId = randomUUID();
            let rptImported =
              rptRunPath !== undefined
                ? await workerClient.request('runPackageImport.start', {
                    jobId: rptImportJobId,
                    sourcePath: resolve(rptRunPath),
                    allowDiagnosticPartial: false,
                  })
                : null;
            const rptImportDeadline = performance.now() + 180_000;
            while (
              rptImported?.ok === true &&
              !['completed', 'failed', 'cancelled'].includes(rptImported.result.state) &&
              performance.now() < rptImportDeadline
            ) {
              await new Promise<void>((resolvePoll) => setTimeout(resolvePoll, 25));
              rptImported = await workerClient.request('runPackageImport.get', {
                jobId: rptImportJobId,
              });
            }
            const rptRun =
              rptImported?.ok === true &&
              rptImported.result.state === 'completed' &&
              rptImported.result.result !== null
                ? rptImported.result.result.importedRun
                : null;
            const rptDiscarded =
              rptRun !== null
                ? await workerClient.request('runPackageImport.discard', { jobId: rptImportJobId })
                : null;
            const rptVerified =
              rptRun !== null
                ? await workerClient.request('importedRun.verifySource', {
                    localImportId: rptRun.localImportId,
                  })
                : null;
            const rptMaterialized =
              rptRun !== null
                ? await workerClient.request('reliabilityExecution.materialize', {
                    localImportId: rptRun.localImportId,
                  })
                : null;
            const rptSource =
              rptMaterialized?.ok === true
                ? await workerClient.request('rptCalculation.getSourceInputs', {
                    executionId: rptMaterialized.result.executionId,
                    planSelection: 'original',
                  })
                : null;
            const rptCalculationInputId = randomUUID();
            const rptCalculationResultId = randomUUID();
            const rptCalculation =
              rptSource?.ok === true
                ? await workerClient.request('rptCalculation.create', {
                    analysisInputSnapshotId: rptCalculationInputId,
                    calculationSnapshotId: rptCalculationResultId,
                    executionId: rptSource.result.executionId,
                    planSelection: 'original',
                    selections: (
                      [
                        'nominal_rpm',
                        'design_cycles',
                        'reserve_factor',
                        'acceleration_duration_s',
                        'steady_duration_s',
                        'deceleration_duration_s',
                      ] as const
                    ).map((field) => ({
                      field,
                      origin: 'source' as const,
                      manualValue: null,
                      basis: '',
                      evidence: null,
                    })),
                    failureEvidence: null,
                    actor: 'local_user',
                    reason: 'Проверка РПТ в поставке',
                  })
                : null;
            const rptHistory =
              rptCalculation?.ok === true
                ? await workerClient.request('rptCalculation.listPage', {
                    wheelModelId: smokeWheelId,
                    cursor: null,
                    limit: 25,
                  })
                : null;
            const pmnRunPath = process.env['IMPELLER_AUTOMATED_PMN_RUN_PATH'];
            const pmnImportJobId = randomUUID();
            let pmnImported =
              pmnRunPath !== undefined
                ? await workerClient.request('runPackageImport.start', {
                    jobId: pmnImportJobId,
                    sourcePath: resolve(pmnRunPath),
                    allowDiagnosticPartial: false,
                  })
                : null;
            const pmnImportDeadline = performance.now() + 180_000;
            while (
              pmnImported?.ok === true &&
              !['completed', 'failed', 'cancelled'].includes(pmnImported.result.state) &&
              performance.now() < pmnImportDeadline
            ) {
              await new Promise<void>((resolvePoll) => setTimeout(resolvePoll, 25));
              pmnImported = await workerClient.request('runPackageImport.get', {
                jobId: pmnImportJobId,
              });
            }
            const pmnRun =
              pmnImported?.ok === true &&
              pmnImported.result.state === 'completed' &&
              pmnImported.result.result !== null
                ? pmnImported.result.result.importedRun
                : null;
            const pmnDiscarded =
              pmnRun !== null
                ? await workerClient.request('runPackageImport.discard', { jobId: pmnImportJobId })
                : null;
            const pmnVerified =
              pmnRun !== null
                ? await workerClient.request('importedRun.verifySource', {
                    localImportId: pmnRun.localImportId,
                  })
                : null;
            const pmnMaterialized =
              pmnRun !== null
                ? await workerClient.request('reliabilityExecution.materialize', {
                    localImportId: pmnRun.localImportId,
                  })
                : null;
            const pmnOriginalSource =
              pmnMaterialized?.ok === true
                ? await workerClient.request('pmnCalculation.getSourceInputs', {
                    executionId: pmnMaterialized.result.executionId,
                    planSelection: 'original',
                  })
                : null;
            const pmnEffectiveSource =
              pmnMaterialized?.ok === true
                ? await workerClient.request('pmnCalculation.getSourceInputs', {
                    executionId: pmnMaterialized.result.executionId,
                    planSelection: 'effective',
                  })
                : null;
            const pmnCalculationInputId = randomUUID();
            const pmnCalculationResultId = randomUUID();
            const pmnCalculation =
              pmnEffectiveSource?.ok === true && globalDocument?.ok === true
                ? await workerClient.request('pmnCalculation.create', {
                    analysisInputSnapshotId: pmnCalculationInputId,
                    calculationSnapshotId: pmnCalculationResultId,
                    executionId: pmnEffectiveSource.result.executionId,
                    planSelection: 'effective',
                    selections: (
                      [
                        'nominal_rpm',
                        'speed_factor',
                        'target_cycles',
                        'acceleration_duration_s',
                        'steady_duration_s',
                        'deceleration_duration_s',
                      ] as const
                    ).map((field) => ({
                      field,
                      origin: 'source' as const,
                      manualValue: null,
                      basis: '',
                      evidence: null,
                    })),
                    failureEvidence: {
                      applicability: 'exact_supported',
                      durationToFailureS: '8',
                      basis: 'Packaged PMN failure interval from the documented start',
                      evidence: {
                        documentId: globalDocument.result.caseDocumentId,
                        documentRecordRevision: globalDocument.result.recordRevision,
                        documentLocator: 'Section 3, start and failure',
                      },
                    },
                    actor: 'local_user',
                    reason: 'Проверка ПМН в поставке',
                  })
                : null;
            const pmnHistory =
              pmnCalculation?.ok === true
                ? await workerClient.request('pmnCalculation.listPage', {
                    wheelModelId: smokeWheelId,
                    cursor: null,
                    limit: 25,
                  })
                : null;
            const importClosed = await workerClient.request('project.close', {});
            const importReopened = await workerClient.request('project.open', {
              path: automatedProjectPath,
              applicationInstanceId,
            });
            const listedAfterReopen = await workerClient.request('importedRun.list', {});
            const calculationAfterReopen =
              calculation?.ok === true && importReopened.ok
                ? await workerClient.request('rbdCalculation.getDetail', {
                    calculationSnapshotId: calculationResultId,
                  })
                : null;
            const rptAfterReopen =
              rptCalculation?.ok === true && importReopened.ok
                ? await workerClient.request('rptCalculation.getDetail', {
                    calculationSnapshotId: rptCalculationResultId,
                  })
                : null;
            const pmnAfterReopen =
              pmnCalculation?.ok === true && importReopened.ok
                ? await workerClient.request('pmnCalculation.getDetail', {
                    calculationSnapshotId: pmnCalculationResultId,
                  })
                : null;
            const pmnHistoryAfterReopen =
              pmnCalculation?.ok === true && importReopened.ok
                ? await workerClient.request('pmnCalculation.listPage', {
                    wheelModelId: smokeWheelId,
                    cursor: null,
                    limit: 25,
                  })
                : null;
            pmnCalculationPassed =
              pmnRun !== null &&
              pmnRun.mode === 'pmn' &&
              pmnDiscarded?.ok === true &&
              pmnVerified?.ok === true &&
              pmnVerified.result.sourceIntegrity === 'verified' &&
              pmnMaterialized?.ok === true &&
              pmnMaterialized.result.method === 'pmn' &&
              pmnOriginalSource?.ok === true &&
              pmnOriginalSource.result.planSelection === 'original' &&
              pmnOriginalSource.result.payloadPath === 'plan/original.json' &&
              pmnEffectiveSource?.ok === true &&
              pmnEffectiveSource.result.planSelection === 'effective' &&
              pmnEffectiveSource.result.payloadPath === 'plan/effective.json' &&
              pmnEffectiveSource.result.producerGitCommit ===
                process.env['IMPELLER_AUTOMATED_PMN_PRODUCER_COMMIT'] &&
              pmnEffectiveSource.result.outerPackageSha256 ===
                process.env['IMPELLER_AUTOMATED_PMN_PACKAGE_SHA256'] &&
              pmnEffectiveSource.result.sourceValues.nominalRpm === '1500' &&
              pmnEffectiveSource.result.sourceValues.speedFactor === '1.1' &&
              pmnEffectiveSource.result.sourceValues.targetCycles === '2' &&
              pmnEffectiveSource.result.sourceValues.accelerationDurationS === '2' &&
              pmnEffectiveSource.result.sourceValues.steadyDurationS === '1' &&
              pmnEffectiveSource.result.sourceValues.decelerationDurationS === '2' &&
              pmnEffectiveSource.result.methodicalRequirements.targetMaxRpmExact === '1650' &&
              pmnEffectiveSource.result.executionTargets.targetCycles === '2' &&
              pmnCalculation?.ok === true &&
              pmnCalculation.result.detail.inputSnapshot.analysisInputSnapshotId ===
                pmnCalculationInputId &&
              pmnCalculation.result.detail.calculationSnapshot.calculationSnapshotId ===
                pmnCalculationResultId &&
              pmnCalculation.result.detail.calculationSnapshot.resultSnapshot.maximum_rpm
                .decimal === '1650' &&
              pmnCalculation.result.detail.calculationSnapshot.resultSnapshot.cycle_duration_s_exact
                .decimal === '5' &&
              pmnCalculation.result.detail.calculationSnapshot.resultSnapshot.total_duration_s_exact
                .decimal === '10' &&
              pmnCalculation.result.detail.calculationSnapshot.resultSnapshot.failure_result
                .cycles_to_failure === '2' &&
              pmnHistory?.ok === true &&
              pmnHistory.result.items.some(
                (item) => item.calculationSnapshotId === pmnCalculationResultId,
              ) &&
              pmnHistoryAfterReopen?.ok === true &&
              pmnHistoryAfterReopen.result.items.some(
                (item) => item.calculationSnapshotId === pmnCalculationResultId,
              ) &&
              pmnAfterReopen?.ok === true &&
              pmnAfterReopen.result.inputSnapshot.contentSha256 ===
                pmnCalculation.result.detail.inputSnapshot.contentSha256 &&
              pmnAfterReopen.result.calculationSnapshot.contentSha256 ===
                pmnCalculation.result.detail.calculationSnapshot.contentSha256;
            rptCalculationPassed =
              rptRun !== null &&
              rptRun.mode === 'rpt' &&
              rptDiscarded?.ok === true &&
              rptVerified?.ok === true &&
              rptVerified.result.sourceIntegrity === 'verified' &&
              rptMaterialized?.ok === true &&
              rptMaterialized.result.method === 'rpt' &&
              rptSource?.ok === true &&
              rptSource.result.sourceValues.steadyDurationS === '0' &&
              rptSource.result.executionTargets.lowerPointPolicy === 'full_stop' &&
              rptCalculation?.ok === true &&
              rptCalculation.result.detail.inputSnapshot.analysisInputSnapshotId ===
                rptCalculationInputId &&
              rptCalculation.result.detail.calculationSnapshot.resultSnapshot.required_cycles_exact
                .decimal === '2' &&
              rptCalculation.result.detail.calculationSnapshot.resultSnapshot.cycle_duration_s_exact
                .decimal === '4' &&
              rptCalculation.result.detail.calculationSnapshot.resultSnapshot.total_duration_s_exact
                .decimal === '8' &&
              rptCalculation.result.detail.calculationSnapshot.resultSnapshot.minimum_rpm
                .decimal === '15' &&
              rptCalculation.result.detail.calculationSnapshot.resultSnapshot.lower_point_comparison
                .status === 'differs_from_typical_formula' &&
              rptHistory?.ok === true &&
              rptHistory.result.items.some(
                (item) => item.calculationSnapshotId === rptCalculationResultId,
              ) &&
              rptAfterReopen?.ok === true &&
              rptAfterReopen.result.inputSnapshot.contentSha256 ===
                rptCalculation.result.detail.inputSnapshot.contentSha256 &&
              rptAfterReopen.result.calculationSnapshot.contentSha256 ===
                rptCalculation.result.detail.calculationSnapshot.contentSha256;
            rbdCalculationPassed =
              binding.ok &&
              materialized?.ok === true &&
              globalDocument?.ok === true &&
              sourceInputs?.ok === true &&
              sourceInputs.result.packageId === '8b29c35e-60d0-46c1-bc02-4498145011ce' &&
              sourceInputs.result.runId === 'exact_methodical_rounding' &&
              sourceInputs.result.outerPackageSha256 ===
                '7d36efb7af29dc4da049b7d474034f69d137502f314fe671fa70f9aff0ac4930' &&
              sourceInputs.result.payloadSha256 ===
                'f3f4dd13503faa3d4b4cb769bfec410fb174359a84e2c0e48a59ea4db700eb44' &&
              sourceInputs.result.sourceValues.baseCycles === '1000' &&
              sourceInputs.result.sourceValues.reserveFactor === '1.5003' &&
              sourceInputs.result.executionTargets.targetCycles === '1501' &&
              sourceInputs.result.executionTargets.targetSteadyDurationS === '60.04' &&
              sourceInputs.result.executionTargets.totalDurationS === '70.04' &&
              calculation?.ok === true &&
              calculation.result.detail.inputSnapshot.analysisInputSnapshotId ===
                calculationInputId &&
              calculation.result.detail.inputSnapshot.inputSnapshot.schemaVersion === 2 &&
              calculation.result.detail.inputSnapshot.inputSnapshot.fieldSelections.some(
                (field) =>
                  field.field === 'base_cycles' &&
                  field.origin === 'manual' &&
                  field.sourceReference ===
                    'plan/effective.json#/effective_plan/effective_plan/source_values/base_cycles' &&
                  field.evidence?.document?.documentId === globalDocument.result.caseDocumentId,
              ) &&
              calculation.result.detail.calculationSnapshot.calculationSnapshotId ===
                calculationResultId &&
              calculation.result.detail.calculationSnapshot.resultSnapshot.required_cycles_exact
                .decimal === '1500.3' &&
              calculation.result.detail.calculationSnapshot.resultSnapshot.steady_duration_s_exact
                .decimal === '60.012' &&
              calculation.result.detail.calculationSnapshot.resultSnapshot.total_duration_s_exact
                .decimal === '70.012' &&
              calculationAfterReopen?.ok === true &&
              calculationAfterReopen.result.inputSnapshot.contentSha256 ===
                calculation.result.detail.inputSnapshot.contentSha256 &&
              calculationAfterReopen.result.calculationSnapshot.contentSha256 ===
                calculation.result.detail.calculationSnapshot.contentSha256;
            runPackageImportPassed =
              imported.result.result.disposition === 'created' &&
              listed.ok &&
              listed.result.items.length === 1 &&
              detail.ok &&
              detail.result.summary.localImportId === localImportId &&
              verified.ok &&
              verified.result.sourceIntegrity === 'verified' &&
              importDiscarded.ok &&
              importClosed.ok &&
              importReopened.ok &&
              listedAfterReopen.ok &&
              listedAfterReopen.result.items.length === 3 &&
              listedAfterReopen.result.items.some((item) => item.localImportId === localImportId) &&
              listedAfterReopen.result.items.some(
                (item) => item.localImportId === rptRun?.localImportId,
              ) &&
              listedAfterReopen.result.items.some(
                (item) => item.localImportId === pmnRun?.localImportId,
              );
          }
        }
        await workerClient.request('project.close', {});
      }
    }
  }
  const materialRunPath = process.env['IMPELLER_AUTOMATED_MATERIAL_RUN_PATH'];
  const materialArchiveSha256 = process.env['IMPELLER_AUTOMATED_MATERIAL_PACKAGE_SHA256'];
  if (
    automatedProjectPath !== null &&
    workerClient !== null &&
    runPackageImportPassed &&
    materialRunPath !== undefined &&
    materialArchiveSha256 !== undefined
  ) {
    try {
      sourceMaterials = await runSourceMaterialSmoke(
        workerClient,
        copies,
        automatedProjectPath,
        resolve(materialRunPath),
        materialArchiveSha256,
        applicationInstanceId,
      );
    } catch (error: unknown) {
      sourceMaterialsError =
        error instanceof Error ? error.message : 'material_smoke_unknown_error';
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
          projectScenarioPassed &&
          runPackageValidationPassed &&
          runPackageImportPassed &&
          rbdCalculationPassed &&
          rptCalculationPassed &&
          pmnCalculationPassed &&
          sourceMaterials !== null,
        runtime,
        pingOk: ping?.ok === true,
        projectScenarioPassed,
        runPackageValidationPassed,
        runPackageImportPassed,
        rbdCalculationPassed,
        rptCalculationPassed,
        pmnCalculationPassed,
        sourceMaterialsPassed: sourceMaterials !== null,
        sourceMaterials,
        sourceMaterialsError,
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
  rendererCloseState = 'approved';
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
    const copies = registerIpc(logPath, stateDirectory, logger);
    registerRendererProtocol();
    await logger.write({ severity: 'info', component: 'main', event: 'application_start' });
    await workerClient.start();
    await refreshStatus();
    await createWindow();
    await runSmokeIfRequested(copies);
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
  invalidateMaterialOpenings();
  quitting = true;
  const client = workerClient;
  void Promise.resolve(materialOpener?.drain())
    .then(() => client.shutdown())
    .finally(() => app.exit(0));
});
