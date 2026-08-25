import { Button, Group, Modal, Text } from '@mantine/core';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { ImpellerApi, RuntimeStatus } from '@impeller-reliability/contracts';

import logoUrl from '../assets/logo-lic-vvu.svg';
import { DiagnosticsPanel } from '../features/diagnostics/DiagnosticsPanel';
import {
  ProjectWorkspace,
  type ProjectWorkspaceHandle,
} from '../features/projects/ProjectWorkspace';

export type UiPhase = 'loading' | 'ready' | 'unavailable' | 'reconnecting' | 'error' | 'stopped';
type AppView = 'projects' | 'diagnostics';
type ManagedAction = 'restart' | 'close-application';

const browserPreviewRequiredStatus: RuntimeStatus = {
  applicationVersion: '0.1.0',
  electronVersion: '—',
  workerStatus: 'unavailable',
  workerVersion: null,
  protocolVersion: null,
  sqliteStatus: 'error',
  mode: 'development',
  message: 'Откройте renderer с параметром ?preview=ready или ?preview=unavailable.',
};

const phaseFromRuntime = (runtime: RuntimeStatus): UiPhase => {
  if (runtime.workerStatus === 'ready') return 'ready';
  if (runtime.workerStatus === 'unavailable') return 'unavailable';
  if (runtime.workerStatus === 'stopped') return 'stopped';
  return 'loading';
};

export interface AppProps {
  readonly browserPreview: boolean;
  readonly desktopApi: ImpellerApi | null;
}

export function App({ browserPreview, desktopApi }: AppProps): React.JSX.Element {
  const projectWorkspace = useRef<ProjectWorkspaceHandle>(null);
  const [view, setView] = useState<AppView>('projects');
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [phase, setPhase] = useState<UiPhase>('loading');
  const [checking, setChecking] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [managedAction, setManagedAction] = useState<ManagedAction | null>(null);

  const applyRuntime = useCallback((nextRuntime: RuntimeStatus): void => {
    setRuntime(nextRuntime);
    setPhase(phaseFromRuntime(nextRuntime));
    setErrorMessage(null);
  }, []);
  const showConnectionError = useCallback((): void => {
    setPhase('error');
    setErrorMessage(
      'Не удалось получить ответ локального расчётного ядра. Перезапустите ядро и повторите проверку.',
    );
  }, []);

  useEffect(() => {
    let active = true;
    if (desktopApi === null) return undefined;
    const unsubscribe = desktopApi.system.subscribeStatus((nextRuntime) => {
      if (active) applyRuntime(nextRuntime);
    });
    const unsubscribeCloseRequested = desktopApi.system.subscribeCloseRequested(() => {
      if (projectWorkspace.current?.hasDirtyDraft() === true) {
        setManagedAction('close-application');
        return;
      }
      void desktopApi.system.confirmClose();
    });
    void desktopApi.system
      .getStatus()
      .then((nextRuntime) => {
        if (active) applyRuntime(nextRuntime);
      })
      .catch(() => {
        if (active) showConnectionError();
      });
    return () => {
      active = false;
      unsubscribe();
      unsubscribeCloseRequested();
    };
  }, [applyRuntime, desktopApi, showConnectionError]);

  const displayedRuntime = runtime ?? (desktopApi === null ? browserPreviewRequiredStatus : null);
  const displayedPhase: UiPhase = restarting ? 'reconnecting' : phase;
  const displayedMessage =
    errorMessage ?? displayedRuntime?.message ?? 'Получение состояния локального контура…';
  const workerReady = displayedPhase === 'ready';

  const checkConnection = async (): Promise<void> => {
    if (desktopApi === null) return;
    setChecking(true);
    setErrorMessage(null);
    try {
      applyRuntime(await desktopApi.system.ping());
    } catch {
      showConnectionError();
    } finally {
      setChecking(false);
    }
  };
  const restartWorker = async (): Promise<void> => {
    if (desktopApi === null) return;
    setRestarting(true);
    setPhase('reconnecting');
    setErrorMessage(null);
    try {
      const nextRuntime = await desktopApi.system.restart();
      applyRuntime(nextRuntime);
      if (nextRuntime.workerStatus === 'ready') {
        await projectWorkspace.current?.reattachAfterWorkerRestart();
      }
    } catch {
      showConnectionError();
    } finally {
      setRestarting(false);
    }
  };
  const requestRestart = (): void => {
    if (projectWorkspace.current?.hasDirtyDraft() === true) {
      setManagedAction('restart');
      return;
    }
    void restartWorker();
  };
  const confirmManagedAction = async (): Promise<void> => {
    const action = managedAction;
    setManagedAction(null);
    if (desktopApi === null || action === null) return;
    if (action === 'close-application') {
      await desktopApi.system.confirmClose();
      return;
    }
    await restartWorker();
  };

  return (
    <div className="desktop-shell">
      <Modal
        opened={managedAction !== null}
        onClose={() => setManagedAction(null)}
        title="Есть несохранённые изменения"
        centered
      >
        <Text>
          {managedAction === 'restart'
            ? 'Ядро будет перезапущено. Черновик останется в форме, а проект подключится повторно только после сверки идентификатора и редакции.'
            : 'Закрыть приложение без сохранения изменений в проекте? Это действие удалит только локальный черновик формы.'}
        </Text>
        <Group mt="lg" justify="flex-end">
          <Button variant="default" onClick={() => setManagedAction(null)}>
            Продолжить редактирование
          </Button>
          <Button
            color={managedAction === 'close-application' ? 'red' : 'navy'}
            onClick={() => void confirmManagedAction()}
          >
            {managedAction === 'restart'
              ? 'Перезапустить и сохранить черновик'
              : 'Закрыть без сохранения'}
          </Button>
        </Group>
      </Modal>
      <header className="shell-header">
        <div className="brand-lockup">
          <span className="brand-logo-frame">
            <img src={logoUrl} alt="ЛИЦ ВВУ" className="brand-logo" />
          </span>
          <div className="product-lockup">
            <Text fw={700}>Impeller Reliability</Text>
            <Text size="sm">Надёжность рабочих колёс</Text>
          </div>
        </div>
        <nav className="shell-navigation" aria-label="Разделы приложения">
          <Button
            aria-current={view === 'projects' ? 'page' : undefined}
            className={
              view === 'projects'
                ? 'navigation-button navigation-button--active'
                : 'navigation-button'
            }
            variant="subtle"
            onClick={() => setView('projects')}
          >
            Проекты
          </Button>
          <Button
            aria-current={view === 'diagnostics' ? 'page' : undefined}
            className={
              view === 'diagnostics'
                ? 'navigation-button navigation-button--active'
                : 'navigation-button'
            }
            variant="subtle"
            onClick={() => setView('diagnostics')}
          >
            Диагностика
          </Button>
        </nav>
        <div
          className={`runtime-indicator runtime-indicator--${displayedPhase}`}
          aria-label={displayedMessage}
        >
          <span aria-hidden="true" />
          {workerReady ? 'Контур готов' : 'Требуется внимание'}
        </div>
      </header>

      <main className="shell-content">
        <section hidden={view !== 'projects'} aria-label="Работа с проектами">
          <ProjectWorkspace
            ref={projectWorkspace}
            desktopApi={desktopApi}
            workerReady={workerReady}
          />
        </section>
        <section hidden={view !== 'diagnostics'} aria-label="Диагностика приложения">
          <DiagnosticsPanel
            browserPreview={browserPreview}
            desktopApi={desktopApi}
            runtime={displayedRuntime}
            phase={displayedPhase}
            message={displayedMessage}
            checking={checking}
            restarting={restarting}
            onCheck={() => void checkConnection()}
            onRestart={requestRestart}
          />
        </section>
      </main>
    </div>
  );
}
