import type { ImpellerApi, RuntimeStatus } from '@impeller-reliability/contracts';

export type PreviewMode = 'ready' | 'unavailable';

const previewStatuses: Readonly<Record<PreviewMode, RuntimeStatus>> = {
  ready: {
    applicationVersion: '0.1.0',
    electronVersion: '43.4.1',
    workerStatus: 'ready',
    workerVersion: '0.1.0',
    protocolVersion: 1,
    sqliteStatus: 'ok',
    mode: 'development',
    message: 'Browser preview (DEV, без сохранения): синтетический локальный контур готов.',
  },
  unavailable: {
    applicationVersion: '0.1.0',
    electronVersion: '43.4.1',
    workerStatus: 'unavailable',
    workerVersion: null,
    protocolVersion: 1,
    sqliteStatus: 'error',
    mode: 'development',
    message: 'Browser preview (DEV, без сохранения): смоделирован недоступный Python worker.',
  },
};

export function createPreviewApi(mode: PreviewMode): ImpellerApi {
  let status = previewStatuses[mode];
  const listeners = new Set<(nextStatus: RuntimeStatus) => void>();
  return {
    system: {
      getStatus: () => Promise.resolve(status),
      ping: () =>
        status.workerStatus === 'ready'
          ? Promise.resolve(status)
          : Promise.reject(new Error('preview_worker_unavailable')),
      restart: () => {
        status = previewStatuses.ready;
        for (const listener of listeners) listener(status);
        return Promise.resolve(status);
      },
      openLog: () => Promise.resolve(),
      subscribeStatus: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
  };
}
