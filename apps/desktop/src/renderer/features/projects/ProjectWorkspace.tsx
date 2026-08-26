import { Button, Group, Select, Text, Textarea, TextInput, Title } from '@mantine/core';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useState } from 'react';

import type {
  DesktopError,
  ImpellerApi,
  ProjectDraft,
  ProjectOverview,
  RecentProject,
} from '@impeller-reliability/contracts';

const newProjectDraft: ProjectDraft = {
  name: 'Новый проект',
  projectNumber: '',
  description: '',
  status: 'draft',
};
const statusOptions = [
  { value: 'draft', label: 'Черновик' },
  { value: 'active', label: 'В работе' },
  { value: 'completed', label: 'Завершён' },
  { value: 'archived', label: 'Архивный' },
];

interface ProjectWorkspaceProps {
  readonly desktopApi: ImpellerApi | null;
  readonly workerReady: boolean;
}

export interface ProjectWorkspaceHandle {
  hasDirtyDraft(): boolean;
  reattachAfterWorkerRestart(): Promise<boolean>;
}

export const ProjectWorkspace = forwardRef<ProjectWorkspaceHandle, ProjectWorkspaceProps>(
  function ProjectWorkspace({ desktopApi, workerReady }, ref): React.JSX.Element {
    const [project, setProject] = useState<ProjectOverview | null>(null);
    const [draft, setDraft] = useState<ProjectDraft>(newProjectDraft);
    const [recent, setRecent] = useState<readonly RecentProject[]>([]);
    const [busy, setBusy] = useState<string | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [confirmClose, setConfirmClose] = useState(false);
    const [reattachBlocked, setReattachBlocked] = useState(false);
    const [confirmReload, setConfirmReload] = useState(false);
    const [confirmDiscardLocal, setConfirmDiscardLocal] = useState(false);
    const dirty =
      project !== null &&
      (draft.name !== project.name ||
        draft.projectNumber !== project.projectNumber ||
        draft.description !== project.description ||
        draft.status !== project.status);
    const detached = project !== null && (!workerReady || reattachBlocked);

    const refreshRecent = useCallback(async (): Promise<void> => {
      if (desktopApi === null) return;
      try {
        const result = await desktopApi.project.listRecent();
        if (result.ok) setRecent(result.result);
        else setError(result.error);
      } catch {
        setError(unavailableError());
      }
    }, [desktopApi]);
    useEffect(() => {
      let active = true;
      if (desktopApi !== null) {
        void desktopApi.project
          .listRecent()
          .then((result) => {
            if (!active) return;
            if (result.ok) setRecent(result.result);
            else setError(result.error);
          })
          .catch(() => {
            if (active) setError(unavailableError());
          });
      }
      return () => {
        active = false;
      };
    }, [desktopApi]);

    const acceptProject = (overview: ProjectOverview, notice: string): void => {
      setProject(overview);
      setDraft({
        name: overview.name,
        projectNumber: overview.projectNumber,
        description: overview.description,
        status: overview.status,
      });
      setError(null);
      setConfirmClose(false);
      setReattachBlocked(false);
      setConfirmReload(false);
      setConfirmDiscardLocal(false);
      setMessage(notice);
      void refreshRecent();
    };
    const handleFailure = (nextError: DesktopError): void => {
      if (nextError.code === 'cancelled') return;
      setError(nextError);
      setMessage(null);
    };
    const run = async (key: string, action: () => Promise<void>): Promise<void> => {
      setBusy(key);
      setError(null);
      try {
        await action();
      } catch {
        setError(unavailableError());
        setMessage(null);
      } finally {
        setBusy(null);
      }
    };

    const createProject = (): Promise<void> =>
      run('create', async () => {
        if (desktopApi === null) return;
        const result = await desktopApi.project.create(newProjectDraft);
        if (result.ok) acceptProject(result.result, 'Проект создан и открыт.');
        else handleFailure(result.error);
      });
    const openProject = (): Promise<void> =>
      run('open', async () => {
        if (desktopApi === null) return;
        const result = await desktopApi.project.open();
        if (result.ok) acceptProject(result.result, 'Проект открыт.');
        else handleFailure(result.error);
      });
    const openRecent = (path: string): Promise<void> =>
      run(path, async () => {
        if (desktopApi === null) return;
        const result = await desktopApi.project.openRecent(path);
        if (result.ok) acceptProject(result.result, 'Недавний проект открыт.');
        else handleFailure(result.error);
      });
    const saveProject = (): Promise<void> =>
      run('save', async () => {
        if (desktopApi === null || project === null) return;
        const result = await desktopApi.project.updateMetadata({
          expectedRevision: project.recordRevision,
          metadata: draft,
        });
        if (result.ok)
          acceptProject(
            result.result,
            `Изменения сохранены. Редакция ${String(result.result.recordRevision)}.`,
          );
        else handleFailure(result.error);
      });
    const closeProject = (): Promise<void> =>
      run('close', async () => {
        if (desktopApi === null) return;
        if (dirty && !confirmClose) {
          setConfirmClose(true);
          setMessage(null);
          return;
        }
        const result = await desktopApi.project.close();
        if (result.ok) {
          setProject(null);
          setDraft(newProjectDraft);
          setMessage('Проект закрыт. Данные сохранены в его контейнере.');
          void refreshRecent();
        } else handleFailure(result.error);
      });
    const createBackup = (): Promise<void> =>
      run('backup', async () => {
        if (desktopApi === null || project === null) return;
        const result = await desktopApi.project.createBackup();
        if (result.ok) setMessage(`Резервная копия создана: ${result.result.fileName}`);
        else handleFailure(result.error);
      });
    const changeDraft = (nextDraft: ProjectDraft): void => {
      setDraft(nextDraft);
      setConfirmClose(false);
      setConfirmDiscardLocal(false);
    };

    const reattachAfterWorkerRestart = useCallback(async (): Promise<boolean> => {
      if (project === null) return true;
      if (desktopApi === null) return false;
      try {
        const result = await desktopApi.project.openRecent(project.path);
        if (!result.ok) {
          setReattachBlocked(true);
          setError(result.error);
          setMessage(null);
          return false;
        }
        if (
          result.result.projectId !== project.projectId ||
          result.result.recordRevision !== project.recordRevision
        ) {
          await desktopApi.project.close();
          setReattachBlocked(true);
          setError({
            code: 'revision_conflict',
            message:
              'Проект изменился после потери worker. Черновик сохранён локально; перечитайте проект и перенесите изменения явно.',
            details: {
              expectedRevision: project.recordRevision,
              actualRevision: result.result.recordRevision,
            },
            retryable: false,
          });
          return false;
        }
        setProject(result.result);
        setReattachBlocked(false);
        setConfirmReload(false);
        setConfirmDiscardLocal(false);
        setError(null);
        setMessage(
          dirty
            ? 'Ядро перезапущено. Несохранённый черновик сохранён.'
            : 'Проект снова подключён к worker.',
        );
        void refreshRecent();
        return true;
      } catch {
        setReattachBlocked(true);
        setError(unavailableError());
        return false;
      }
    }, [desktopApi, dirty, project, refreshRecent]);

    useImperativeHandle(
      ref,
      () => ({
        hasDirtyDraft: () => dirty,
        reattachAfterWorkerRestart,
      }),
      [dirty, reattachAfterWorkerRestart],
    );

    const reloadAfterConflict = (): Promise<void> =>
      run('reload', async () => {
        if (desktopApi === null || project === null) return;
        if (!confirmReload) {
          setConfirmReload(true);
          return;
        }
        const result = await desktopApi.project.openRecent(project.path);
        if (result.ok)
          acceptProject(
            result.result,
            'Проект перечитан. Локальный черновик удалён по подтверждению.',
          );
        else handleFailure(result.error);
      });

    const discardLocalWorkspace = async (): Promise<void> => {
      if (!confirmDiscardLocal) {
        setConfirmDiscardLocal(true);
        setMessage(null);
        return;
      }
      if (desktopApi !== null) {
        try {
          await desktopApi.project.releaseLocalWorkspace();
        } catch {
          setError({
            code: 'storage_error',
            message: 'Не удалось освободить локальное состояние проекта.',
            details: {},
            retryable: true,
          });
          return;
        }
      }
      setProject(null);
      setDraft(newProjectDraft);
      setError(null);
      setConfirmClose(false);
      setReattachBlocked(false);
      setConfirmReload(false);
      setConfirmDiscardLocal(false);
      setMessage('Локальный черновик удалён. Файлы проекта не изменялись.');
      void refreshRecent();
    };

    if (project === null)
      return (
        <div className="start-view">
          <section className="start-intro" aria-labelledby="start-title">
            <div>
              <Title id="start-title" order={1}>
                Проект объединяет испытания, анализ и доказательства
              </Title>
              <Text>
                Начните с отдельного контейнера проекта. Сейчас приложение сохраняет его
                идентичность, метаданные и неизменяемую историю действий.
              </Text>
            </div>
            <Group className="start-actions">
              <Button
                loading={busy === 'create'}
                disabled={!workerReady || busy !== null}
                onClick={() => void createProject()}
              >
                Создать проект
              </Button>
              <Button
                loading={busy === 'open'}
                disabled={!workerReady || busy !== null}
                variant="white"
                onClick={() => void openProject()}
              >
                Открыть проект
              </Button>
            </Group>
          </section>
          <Feedback message={message} error={error} />
          <section className="recent-projects" aria-labelledby="recent-title">
            <div className="section-heading">
              <Title id="recent-title" order={2}>
                Недавние проекты
              </Title>
              <Text>
                {recent.length === 0
                  ? 'Список появится после первого создания или открытия.'
                  : 'Пути ранее разрешены через системный диалог.'}
              </Text>
            </div>
            {recent.length === 0 ? (
              <div className="empty-projects">
                <Text fw={650}>Пока нет недавних проектов</Text>
                <Text>Создайте новый контейнер `.irproj` или откройте существующий.</Text>
              </div>
            ) : (
              <div className="recent-list">
                {recent.map((item) => (
                  <button
                    key={item.path}
                    type="button"
                    className="recent-row"
                    disabled={!workerReady || busy !== null}
                    onClick={() => void openRecent(item.path)}
                  >
                    <span>
                      <strong>{item.name}</strong>
                      <small>{item.projectNumber || 'Без номера'}</small>
                    </span>
                    <span className="recent-path">{item.path}</span>
                    <span>{busy === item.path ? 'Открытие…' : 'Открыть'}</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>
      );

    return (
      <div className="project-view">
        <header className="project-heading">
          <div>
            <Title order={1}>{project.name}</Title>
            <Text>
              {project.projectNumber || 'Номер проекта не задан'} · редакция{' '}
              {project.recordRevision}
            </Text>
          </div>
          <Group>
            <Button
              variant="default"
              loading={busy === 'backup'}
              disabled={detached || busy !== null}
              onClick={() => void createBackup()}
            >
              Создать резервную копию
            </Button>
            <Button
              variant="subtle"
              loading={busy === 'close'}
              disabled={detached || busy !== null}
              onClick={() => void closeProject()}
            >
              {confirmClose ? 'Закрыть без сохранения' : 'Закрыть проект'}
            </Button>
          </Group>
        </header>
        {detached ? (
          <div className="feedback feedback--warning" role="alert">
            <strong>Проект отсоединён от worker</strong>
            <span>
              {reattachBlocked
                ? 'Локальный черновик сохранён. Worker session не подключена: редакция не совпала или повторное подключение не завершилось. Черновик не будет записан автоматически.'
                : 'Введённые значения сохранены в форме. Перезапустите ядро; запись станет доступна только после сверки проекта и редакции.'}
            </span>
            {workerReady && reattachBlocked ? (
              <Group>
                <Button
                  size="compact-sm"
                  variant={confirmReload ? 'filled' : 'subtle'}
                  color={confirmReload ? 'red' : 'navy'}
                  loading={busy === 'reload'}
                  disabled={busy !== null}
                  onClick={() => void reloadAfterConflict()}
                >
                  {confirmReload ? 'Перечитать и удалить черновик' : 'Перечитать проект'}
                </Button>
                {confirmReload ? (
                  <Button
                    size="compact-sm"
                    variant="subtle"
                    onClick={() => setConfirmReload(false)}
                  >
                    Оставить черновик
                  </Button>
                ) : null}
              </Group>
            ) : null}
            {reattachBlocked ? (
              <div className="detached-discard">
                {confirmDiscardLocal ? (
                  <Text size="sm">
                    Будет очищена только локальная форма. Контейнер `.irproj` и список недавних
                    проектов не изменятся.
                  </Text>
                ) : null}
                <Group>
                  <Button
                    size="compact-sm"
                    variant={confirmDiscardLocal ? 'filled' : 'subtle'}
                    color="red"
                    onClick={() => void discardLocalWorkspace()}
                  >
                    {confirmDiscardLocal
                      ? 'Удалить только локальный черновик'
                      : 'Отказаться от локального черновика'}
                  </Button>
                  {confirmDiscardLocal ? (
                    <Button
                      size="compact-sm"
                      variant="subtle"
                      onClick={() => setConfirmDiscardLocal(false)}
                    >
                      Оставить черновик
                    </Button>
                  ) : null}
                </Group>
              </div>
            ) : null}
          </div>
        ) : null}
        {confirmClose ? (
          <div className="feedback feedback--warning" role="alert">
            <strong>Есть несохранённые изменения</strong>
            <span>Повторно нажмите «Закрыть без сохранения» или продолжите редактирование.</span>
            <Button size="compact-sm" variant="subtle" onClick={() => setConfirmClose(false)}>
              Продолжить редактирование
            </Button>
          </div>
        ) : null}
        <Feedback message={message} error={error} />
        <section className="project-surface" aria-labelledby="metadata-title">
          <div className="section-heading">
            <Title id="metadata-title" order={2}>
              Основные сведения
            </Title>
            <Text>Изменение создаёт новую редакцию и запись в истории в одной транзакции.</Text>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void saveProject();
            }}
          >
            <div className="metadata-form">
              <TextInput
                label="Название проекта"
                required
                maxLength={200}
                value={draft.name}
                disabled={detached}
                onChange={(event) => changeDraft({ ...draft, name: event.currentTarget.value })}
              />
              <TextInput
                label="Номер проекта"
                maxLength={100}
                value={draft.projectNumber}
                disabled={detached}
                onChange={(event) =>
                  changeDraft({ ...draft, projectNumber: event.currentTarget.value })
                }
              />
              <Select
                label="Статус"
                data={statusOptions}
                allowDeselect={false}
                value={draft.status}
                disabled={detached}
                onChange={(value) => {
                  if (
                    value === 'draft' ||
                    value === 'active' ||
                    value === 'completed' ||
                    value === 'archived'
                  )
                    changeDraft({ ...draft, status: value });
                }}
              />
              <Textarea
                className="description-field"
                label="Описание"
                minRows={5}
                maxLength={4000}
                value={draft.description}
                disabled={detached}
                onChange={(event) =>
                  changeDraft({ ...draft, description: event.currentTarget.value })
                }
              />
            </div>
            <div className="container-details">
              <div>
                <span>Контейнер</span>
                <strong>{project.path}</strong>
              </div>
              <div>
                <span>Идентификатор проекта</span>
                <strong>{project.projectId}</strong>
              </div>
              <div>
                <span>Схема БД</span>
                <strong>v{project.schemaVersion}</strong>
              </div>
              <div>
                <span>Обновлён</span>
                <strong>{formatDate(project.updatedAtUtc)}</strong>
              </div>
            </div>
            <Group className="form-actions">
              <Button
                type="submit"
                loading={busy === 'save'}
                disabled={detached || busy !== null || draft.name.trim() === ''}
              >
                Сохранить изменения
              </Button>
              <Text size="sm">Ожидаемая редакция: {project.recordRevision}</Text>
            </Group>
          </form>
        </section>
      </div>
    );
  },
);

function Feedback({
  message,
  error,
}: {
  readonly message: string | null;
  readonly error: DesktopError | null;
}): React.JSX.Element | null {
  if (error !== null)
    return (
      <div className="feedback feedback--error" role="alert">
        <strong>Операция не выполнена</strong>
        <span>{error.message}</span>
      </div>
    );
  if (message !== null)
    return (
      <div className="feedback" role="status">
        <span>{message}</span>
      </div>
    );
  return null;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  );
}

function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Локальный worker недоступен. Перезапустите ядро и повторите операцию.',
    details: {},
    retryable: true,
  };
}
