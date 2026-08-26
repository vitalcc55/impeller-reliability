import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

import {
  createDesktopResultSchema,
  projectBackupResultSchema,
  projectCloseResultSchema,
  projectDraftSchema,
  projectOverviewSchema,
  projectUpdateMetadataPayloadSchema,
  recentProjectsSchema,
  runtimeStatusSchema,
  type ImpellerApi,
} from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from '../main/channels';

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
      createDesktopResultSchema(projectOverviewSchema).parse(
        await ipcRenderer.invoke(IPC_CHANNELS.projectCreate, projectDraftSchema.parse(draft)),
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
      createDesktopResultSchema(projectOverviewSchema).parse(
        await ipcRenderer.invoke(
          IPC_CHANNELS.projectUpdateMetadata,
          projectUpdateMetadataPayloadSchema.parse(command),
        ),
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
};

contextBridge.exposeInMainWorld('impeller', api);
