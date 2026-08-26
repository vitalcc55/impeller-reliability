import type {
  DesktopResult,
  ImpellerApi,
  ProjectOverview,
  RecentProject,
  RuntimeStatus,
} from '@impeller-reliability/contracts';

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
  let activeProject: ProjectOverview | null = null;
  const recentProject: RecentProject = {
    path: 'C:\\Проекты\\Надёжность рабочего колеса.irproj',
    name: 'Надёжность рабочего колеса',
    projectNumber: 'ИР-2026-001',
    lastOpenedAtUtc: '2026-08-25T15:00:00.000Z',
  };
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
      confirmClose: () => Promise.resolve(),
      subscribeStatus: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      subscribeCloseRequested: () => () => undefined,
    },
    project: {
      create: (draft) => {
        activeProject = projectOverview(draft, 'C:\\Проекты\\Новый проект.irproj');
        return Promise.resolve(success(activeProject));
      },
      open: () => {
        activeProject = projectOverview(
          {
            name: recentProject.name,
            projectNumber: recentProject.projectNumber,
            description: 'Синтетический проект Browser preview без записи на диск.',
            status: 'active',
          },
          recentProject.path,
        );
        return Promise.resolve(success(activeProject));
      },
      openRecent: () => {
        activeProject = projectOverview(
          {
            name: recentProject.name,
            projectNumber: recentProject.projectNumber,
            description: 'Синтетический проект Browser preview без записи на диск.',
            status: 'active',
          },
          recentProject.path,
        );
        return Promise.resolve(success(activeProject));
      },
      close: () => {
        activeProject = null;
        return Promise.resolve(success({ closed: true }));
      },
      getOverview: () =>
        Promise.resolve(activeProject === null ? noProject() : success(activeProject)),
      updateMetadata: ({ expectedRevision, metadata }) => {
        if (activeProject === null) return Promise.resolve(noProject());
        if (activeProject.recordRevision !== expectedRevision) {
          return Promise.resolve({
            ok: false,
            error: {
              code: 'revision_conflict',
              message: 'Синтетический конфликт редакции.',
              details: {},
              retryable: false,
            },
          });
        }
        activeProject = {
          ...activeProject,
          ...metadata,
          recordRevision: expectedRevision + 1,
          updatedAtUtc: new Date().toISOString(),
        };
        return Promise.resolve(success(activeProject));
      },
      createBackup: () =>
        Promise.resolve(
          success({
            fileName: 'project-v1-preview.sqlite',
            sha256: '0'.repeat(64),
            createdAtUtc: new Date().toISOString(),
          }),
        ),
      listRecent: () => Promise.resolve(success([recentProject])),
    },
  };
}

function projectOverview(
  draft: {
    readonly name: string;
    readonly projectNumber: string;
    readonly description: string;
    readonly status: ProjectOverview['status'];
  },
  path: string,
): ProjectOverview {
  return {
    projectId: '019d2ca4-b4e6-7e18-8f5e-36ce99ab87da',
    path,
    ...draft,
    recordRevision: 1,
    createdAtUtc: '2026-08-25T15:00:00.000Z',
    updatedAtUtc: '2026-08-25T15:00:00.000Z',
    createdWithApplicationVersion: '0.1.0',
    schemaVersion: 1,
  };
}

function success<TResult>(result: TResult): DesktopResult<TResult> {
  return { ok: true, result };
}

function noProject<TResult>(): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'storage_error',
      message: 'Проект не открыт.',
      details: {},
      retryable: false,
    },
  };
}
