import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import { z, type ZodType } from 'zod';

import {
  caseDocumentAttachFileCommandSchema,
  caseDocumentCreateCommandSchema,
  caseDocumentIdPayloadSchema,
  caseDocumentListPayloadSchema,
  caseDocumentListResultSchema,
  caseDocumentRevisionPayloadSchema,
  caseDocumentSchema,
  caseDocumentUpdatePayloadSchema,
  createDesktopResultSchema,
  customerGetResultSchema,
  customerProfileSchema,
  customerUpsertPayloadSchema,
  projectBackupResultSchema,
  projectCloseResultSchema,
  projectDraftSchema,
  projectOverviewSchema,
  projectUpdateMetadataPayloadSchema,
  importedRunBindingCommandSchema,
  importedRunDetailSchema,
  importedRunEnrichmentResolutionCommandSchema,
  importedRunIdPayloadSchema,
  importedRunListResultSchema,
  importedRunResolutionStatePayloadSchema,
  importedRunVerifyResultSchema,
  recentProjectsSchema,
  runtimeStatusSchema,
  runPackageValidationDiscardResultSchema,
  runPackageValidationJobPayloadSchema,
  runPackageValidationJobSchema,
  runPackageValidationStartCommandSchema,
  runPackageImportDiscardResultSchema,
  runPackageImportJobPayloadSchema,
  runPackageImportJobSchema,
  runPackageImportStartCommandSchema,
  specimenBindingSchema,
  specimenCreatePayloadSchema,
  specimenListResultSchema,
  specimenRevisionPayloadSchema,
  specimenSchema,
  specimenUpdatePayloadSchema,
  wheelModelCreatePayloadSchema,
  wheelModelListResultSchema,
  wheelModelRevisionPayloadSchema,
  wheelModelSchema,
  wheelModelUpdatePayloadSchema,
  type DesktopResult,
  type ImpellerApi,
} from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from '../main/channels';

async function invokeValidated<TPayload, TResult>(
  channel: string,
  payloadSchema: ZodType<TPayload>,
  payload: unknown,
  resultSchema: ZodType<DesktopResult<TResult>>,
): Promise<DesktopResult<TResult>> {
  const parsed = payloadSchema.safeParse(payload);
  if (!parsed.success) {
    return {
      ok: false,
      error: {
        code: 'validation_error',
        message: 'Проверьте заполненные поля.',
        details: {},
        retryable: false,
      },
    };
  }
  return resultSchema.parse(await ipcRenderer.invoke(channel, parsed.data));
}

const api: ImpellerApi = {
  system: {
    getStatus: async () =>
      runtimeStatusSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.getStatus)),
    ping: async () => runtimeStatusSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.ping)),
    restart: async () => runtimeStatusSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.restart)),
    openLog: async () => {
      await ipcRenderer.invoke(IPC_CHANNELS.openLog);
    },
    confirmClose: async () => {
      await ipcRenderer.invoke(IPC_CHANNELS.confirmClose);
    },
    cancelClose: async () => {
      await ipcRenderer.invoke(IPC_CHANNELS.cancelClose);
    },
    subscribeStatus: (listener) => {
      const handleStatus = (_event: IpcRendererEvent, value: unknown): void => {
        listener(runtimeStatusSchema.parse(value));
      };
      ipcRenderer.on(IPC_CHANNELS.statusChanged, handleStatus);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.statusChanged, handleStatus);
    },
    subscribeCloseRequested: (listener) => {
      const handleCloseRequested = (): void => {
        void ipcRenderer.invoke(IPC_CHANNELS.closeAcknowledged).then(() => listener());
      };
      ipcRenderer.on(IPC_CHANNELS.closeRequested, handleCloseRequested);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.closeRequested, handleCloseRequested);
    },
  },
  project: {
    create: async (draft) =>
      invokeValidated(
        IPC_CHANNELS.projectCreate,
        projectDraftSchema,
        draft,
        createDesktopResultSchema(projectOverviewSchema),
      ),
    open: async () =>
      createDesktopResultSchema(projectOverviewSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectOpen),
      ),
    openRecent: async (path) =>
      createDesktopResultSchema(projectOverviewSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectOpenRecent, path),
      ),
    close: async () =>
      createDesktopResultSchema(projectCloseResultSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectClose),
      ),
    releaseLocalWorkspace: async () => {
      await ipcRenderer.invoke(IPC_CHANNELS.projectReleaseLocalWorkspace);
    },
    getOverview: async () =>
      createDesktopResultSchema(projectOverviewSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectGetOverview),
      ),
    updateMetadata: async (command) =>
      invokeValidated(
        IPC_CHANNELS.projectUpdateMetadata,
        projectUpdateMetadataPayloadSchema,
        command,
        createDesktopResultSchema(projectOverviewSchema),
      ),
    createBackup: async () =>
      createDesktopResultSchema(projectBackupResultSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectCreateBackup),
      ),
    listRecent: async () =>
      createDesktopResultSchema(recentProjectsSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectListRecent),
      ),
  },
  caseCustomer: {
    get: async () => {
      const result = createDesktopResultSchema(customerGetResultSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.customerGet),
      );
      return result.ok ? { ok: true, result: result.result.customer } : result;
    },
    upsert: async (command) =>
      invokeValidated(
        IPC_CHANNELS.customerUpsert,
        customerUpsertPayloadSchema,
        command,
        createDesktopResultSchema(customerProfileSchema),
      ),
  },
  wheelModel: {
    create: async (command) =>
      invokeValidated(
        IPC_CHANNELS.wheelModelCreate,
        wheelModelCreatePayloadSchema,
        command,
        createDesktopResultSchema(wheelModelSchema),
      ),
    list: async (includeArchived) => {
      const result = createDesktopResultSchema(wheelModelListResultSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.wheelModelList, { includeArchived }),
      );
      return result.ok ? { ok: true, result: result.result.items } : result;
    },
    get: async (wheelModelId) =>
      createDesktopResultSchema(wheelModelSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.wheelModelGet, { wheelModelId }),
      ),
    update: async (command) =>
      invokeValidated(
        IPC_CHANNELS.wheelModelUpdate,
        wheelModelUpdatePayloadSchema,
        command,
        createDesktopResultSchema(wheelModelSchema),
      ),
    archive: async (command) =>
      invokeValidated(
        IPC_CHANNELS.wheelModelArchive,
        wheelModelRevisionPayloadSchema,
        command,
        createDesktopResultSchema(wheelModelSchema),
      ),
    restore: async (command) =>
      invokeValidated(
        IPC_CHANNELS.wheelModelRestore,
        wheelModelRevisionPayloadSchema,
        command,
        createDesktopResultSchema(wheelModelSchema),
      ),
  },
  specimen: {
    create: async (command) =>
      invokeValidated(
        IPC_CHANNELS.specimenCreate,
        specimenCreatePayloadSchema,
        command,
        createDesktopResultSchema(specimenSchema),
      ),
    list: async (includeArchived) => {
      const result = createDesktopResultSchema(specimenListResultSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.specimenList, { includeArchived }),
      );
      return result.ok ? { ok: true, result: result.result.items } : result;
    },
    get: async (specimenId) =>
      createDesktopResultSchema(specimenSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.specimenGet, { specimenId }),
      ),
    update: async (command) =>
      invokeValidated(
        IPC_CHANNELS.specimenUpdate,
        specimenUpdatePayloadSchema,
        command,
        createDesktopResultSchema(specimenSchema),
      ),
    archive: async (command) =>
      invokeValidated(
        IPC_CHANNELS.specimenArchive,
        specimenRevisionPayloadSchema,
        command,
        createDesktopResultSchema(specimenSchema),
      ),
    restore: async (command) =>
      invokeValidated(
        IPC_CHANNELS.specimenRestore,
        specimenRevisionPayloadSchema,
        command,
        createDesktopResultSchema(specimenSchema),
      ),
  },
  caseDocument: {
    create: async (command) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentCreate,
        caseDocumentCreateCommandSchema,
        command,
        createDesktopResultSchema(caseDocumentSchema),
      ),
    createWithFile: async (command) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentCreateWithFile,
        caseDocumentCreateCommandSchema,
        command,
        createDesktopResultSchema(caseDocumentSchema),
      ),
    list: async (query) => {
      const result = await invokeValidated(
        IPC_CHANNELS.caseDocumentList,
        caseDocumentListPayloadSchema,
        query,
        createDesktopResultSchema(caseDocumentListResultSchema),
      );
      return result.ok ? { ok: true, result: result.result.items } : result;
    },
    get: async (caseDocumentId) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentGet,
        caseDocumentIdPayloadSchema,
        { caseDocumentId },
        createDesktopResultSchema(caseDocumentSchema),
      ),
    update: async (command) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentUpdate,
        caseDocumentUpdatePayloadSchema,
        command,
        createDesktopResultSchema(caseDocumentSchema),
      ),
    attachFile: async (command) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentAttachFile,
        caseDocumentAttachFileCommandSchema,
        command,
        createDesktopResultSchema(caseDocumentSchema),
      ),
    verifyFile: async (caseDocumentId) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentVerifyFile,
        caseDocumentIdPayloadSchema,
        { caseDocumentId },
        createDesktopResultSchema(caseDocumentSchema),
      ),
    openFile: async (caseDocumentId) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentOpenFile,
        caseDocumentIdPayloadSchema,
        { caseDocumentId },
        createDesktopResultSchema(z.object({ opened: z.boolean() }).strict()),
      ),
    archive: async (command) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentArchive,
        caseDocumentRevisionPayloadSchema,
        command,
        createDesktopResultSchema(caseDocumentSchema),
      ),
    restore: async (command) =>
      invokeValidated(
        IPC_CHANNELS.caseDocumentRestore,
        caseDocumentRevisionPayloadSchema,
        command,
        createDesktopResultSchema(caseDocumentSchema),
      ),
  },
  runPackageValidation: {
    selectAndStart: async (command) =>
      invokeValidated(
        IPC_CHANNELS.runPackageValidationStart,
        runPackageValidationStartCommandSchema,
        command,
        createDesktopResultSchema(runPackageValidationJobSchema),
      ),
    get: async (jobId) =>
      invokeValidated(
        IPC_CHANNELS.runPackageValidationGet,
        runPackageValidationJobPayloadSchema,
        { jobId },
        createDesktopResultSchema(runPackageValidationJobSchema),
      ),
    cancel: async (jobId) =>
      invokeValidated(
        IPC_CHANNELS.runPackageValidationCancel,
        runPackageValidationJobPayloadSchema,
        { jobId },
        createDesktopResultSchema(runPackageValidationJobSchema),
      ),
    discard: async (jobId) =>
      invokeValidated(
        IPC_CHANNELS.runPackageValidationDiscard,
        runPackageValidationJobPayloadSchema,
        { jobId },
        createDesktopResultSchema(runPackageValidationDiscardResultSchema),
      ),
  },
  runPackageImport: {
    selectAndStart: async (command) =>
      invokeValidated(
        IPC_CHANNELS.runPackageImportStart,
        runPackageImportStartCommandSchema,
        command,
        createDesktopResultSchema(runPackageImportJobSchema),
      ),
    get: async (jobId) =>
      invokeValidated(
        IPC_CHANNELS.runPackageImportGet,
        runPackageImportJobPayloadSchema,
        { jobId },
        createDesktopResultSchema(runPackageImportJobSchema),
      ),
    cancel: async (jobId) =>
      invokeValidated(
        IPC_CHANNELS.runPackageImportCancel,
        runPackageImportJobPayloadSchema,
        { jobId },
        createDesktopResultSchema(runPackageImportJobSchema),
      ),
    discard: async (jobId) =>
      invokeValidated(
        IPC_CHANNELS.runPackageImportDiscard,
        runPackageImportJobPayloadSchema,
        { jobId },
        createDesktopResultSchema(runPackageImportDiscardResultSchema),
      ),
  },
  importedRun: {
    list: async () => {
      const result = await invokeValidated(
        IPC_CHANNELS.importedRunList,
        z.object({}).strict(),
        {},
        createDesktopResultSchema(importedRunListResultSchema),
      );
      return result.ok ? { ok: true, result: result.result.items } : result;
    },
    get: async (localImportId) =>
      invokeValidated(
        IPC_CHANNELS.importedRunGet,
        importedRunIdPayloadSchema,
        { localImportId },
        createDesktopResultSchema(importedRunDetailSchema),
      ),
    verifySource: async (localImportId) =>
      invokeValidated(
        IPC_CHANNELS.importedRunVerifySource,
        importedRunIdPayloadSchema,
        { localImportId },
        createDesktopResultSchema(importedRunVerifyResultSchema),
      ),
    getResolutionState: async (sourceSpecimenId) =>
      invokeValidated(
        IPC_CHANNELS.importedRunGetResolutionState,
        importedRunResolutionStatePayloadSchema,
        { sourceSpecimenId },
        createDesktopResultSchema(specimenBindingSchema),
      ),
    bindSpecimen: async (command) =>
      invokeValidated(
        IPC_CHANNELS.importedRunBindSpecimen,
        importedRunBindingCommandSchema,
        command,
        createDesktopResultSchema(specimenBindingSchema),
      ),
    applyEnrichmentResolution: async (command) =>
      invokeValidated(
        IPC_CHANNELS.importedRunApplyEnrichmentResolution,
        importedRunEnrichmentResolutionCommandSchema,
        command,
        createDesktopResultSchema(importedRunDetailSchema),
      ),
  },
};

contextBridge.exposeInMainWorld('impeller', api);
