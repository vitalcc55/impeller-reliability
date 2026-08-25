import { Button, Text } from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';

import type { ImpellerApi, RuntimeStatus } from '@impeller-reliability/contracts';

import logoUrl from '../assets/logo-lic-vvu.svg';
import { DiagnosticsPanel } from '../features/diagnostics/DiagnosticsPanel';
import { ProjectWorkspace } from '../features/projects/ProjectWorkspace';

export type UiPhase = 'loading' | 'ready' | 'unavailable' | 'reconnecting' | 'error' | 'stopped';
type AppView = 'projects' | 'diagnostics';

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
  const [view, setView] = useState<AppView>('projects');
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [phase, setPhase] = useState<UiPhase>('loading');
  const [checking, setChecking] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

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
      applyRuntime(await desktopApi.system.restart());
    } catch {
      showConnectionError();
    } finally {
      setRestarting(false);
    }
  };

  return (
    <div className="desktop-shell">
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
            key={workerReady ? 'worker-ready' : 'worker-unavailable'}
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
            onRestart={() => void restartWorker()}
          />
        </section>
      </main>
    </div>
  );
}
