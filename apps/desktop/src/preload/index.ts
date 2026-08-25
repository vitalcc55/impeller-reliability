import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

import { runtimeStatusSchema, type ImpellerApi } from '@impeller-reliability/contracts';

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
    subscribeStatus: (listener) => {
      const handleStatus = (_event: IpcRendererEvent, value: unknown): void => {
        listener(runtimeStatusSchema.parse(value));
      };
      ipcRenderer.on(IPC_CHANNELS.statusChanged, handleStatus);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.statusChanged, handleStatus);
    },
  },
};

contextBridge.exposeInMainWorld('impeller', api);
