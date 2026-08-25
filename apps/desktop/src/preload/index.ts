import { contextBridge, ipcRenderer } from 'electron';

import { runtimeStatusSchema, type ImpellerApi } from '@impeller-reliability/contracts';

import { IPC_CHANNELS } from '../main/channels';

const api: ImpellerApi = {
  system: {
    getStatus: async () =>
      runtimeStatusSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.getStatus)),
    ping: async () => runtimeStatusSchema.parse(await ipcRenderer.invoke(IPC_CHANNELS.ping)),
    openLog: async () => {
      await ipcRenderer.invoke(IPC_CHANNELS.openLog);
    },
  },
};

contextBridge.exposeInMainWorld('impeller', api);
