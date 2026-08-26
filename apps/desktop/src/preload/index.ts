import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import type { ZodType } from 'zod';

import {
  createDesktopResultSchema,
  customerGetResultSchema,
  customerProfileSchema,
  customerUpsertPayloadSchema,
  projectBackupResultSchema,
  projectCloseResultSchema,
  projectDraftSchema,
  projectOverviewSchema,
  projectUpdateMetadataPayloadSchema,
  recentProjectsSchema,
  runtimeStatusSchema,
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
};

contextBridge.exposeInMainWorld('impeller', api);
