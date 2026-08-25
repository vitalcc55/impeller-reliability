import { Badge, Button, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';

import type { ImpellerApi, RuntimeStatus } from '@impeller-reliability/contracts';

type UiPhase = 'loading' | 'ready' | 'unavailable' | 'reconnecting' | 'error' | 'stopped';

const phaseLabel: Readonly<Record<UiPhase, string>> = {
  loading: 'Запуск',
  ready: 'Готов',
  unavailable: 'Недоступен',
  reconnecting: 'Перезапуск',
  error: 'Ошибка',
  stopped: 'Остановлен',
};

const statusClassName = (phase: UiPhase): string => {
  if (phase === 'ready') return 'status-badge status-badge--success';
  if (phase === 'loading' || phase === 'reconnecting') {
    return 'status-badge status-badge--warning';
  }
  if (phase === 'unavailable' || phase === 'error') {
    return 'status-badge status-badge--danger';
  }
  return 'status-badge';
};

const phaseFromRuntime = (runtime: RuntimeStatus): UiPhase => {
  if (runtime.workerStatus === 'ready') return 'ready';
  if (runtime.workerStatus === 'unavailable') return 'unavailable';
  if (runtime.workerStatus === 'stopped') return 'stopped';
  return 'loading';
};

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

export interface AppProps {
  readonly browserPreview: boolean;
  readonly desktopApi: ImpellerApi | null;
}

export function App({ browserPreview, desktopApi }: AppProps): React.JSX.Element {
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
    <main className="app-shell">
      <section className="hero" aria-labelledby="application-title">
        <Text className="product-name">Impeller Reliability</Text>
        <Title id="application-title" order={1}>
          Калькулятор показателей надёжности рабочих колёс вентиляторов
        </Title>
        <Text c="dimmed" maw={720}>
          Инфраструктурный экран M01. Предметные расчёты ещё не реализованы.
        </Text>
      </section>

      <Paper className="status-panel" radius="lg" withBorder>
        <Group justify="space-between" align="flex-start">
          <Stack className="status-copy" gap={4}>
            <Title order={2}>Состояние локального контура</Title>
            <Text className="status-message" c="dimmed" aria-live="polite">
              {displayedMessage}
            </Text>
          </Stack>
          <Badge className={statusClassName(displayedPhase)} size="lg" variant="light">
            {phaseLabel[displayedPhase]}
          </Badge>
        </Group>

        <dl className="status-grid">
          <div>
            <dt>Версия приложения</dt>
            <dd>{displayedRuntime?.applicationVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>Electron</dt>
            <dd>{displayedRuntime?.electronVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>Python worker</dt>
            <dd>{displayedRuntime?.workerVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>IPC protocol</dt>
            <dd>{displayedRuntime?.protocolVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>SQLite</dt>
            <dd>{displayedRuntime?.sqliteStatus ?? 'pending'}</dd>
          </div>
          <div>
            <dt>Режим</dt>
            <dd>{displayedRuntime?.mode ?? '—'}</dd>
          </div>
        </dl>

        <Group className="status-actions" mt="xl">
          <Button
            disabled={desktopApi === null || restarting}
            loading={checking}
            onClick={() => void checkConnection()}
          >
            Проверить связь
          </Button>
          <Button
            disabled={desktopApi === null || checking}
            loading={restarting}
            variant="default"
            onClick={() => void restartWorker()}
          >
            Перезапустить ядро
          </Button>
          <Button
            disabled={desktopApi === null || browserPreview || checking || restarting}
            variant="subtle"
            onClick={() => void desktopApi?.system.openLog()}
          >
            Открыть журнал
          </Button>
        </Group>
      </Paper>
    </main>
  );
}
