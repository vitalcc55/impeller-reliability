export const IPC_CHANNELS = {
  getStatus: 'impeller:system:get-status',
  ping: 'impeller:system:ping',
  restart: 'impeller:system:restart',
  openLog: 'impeller:system:open-log',
  statusChanged: 'impeller:system:status-changed',
} as const;
