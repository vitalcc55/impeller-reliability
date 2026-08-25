import { Badge, Button, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { useEffect, useState } from 'react';

import type { ImpellerApi, RuntimeStatus } from '@impeller-reliability/contracts';

const statusClassName = (status: RuntimeStatus['workerStatus'] | null): string => {
  if (status === 'ready') return 'status-badge status-badge--success';
  if (status === 'starting') return 'status-badge status-badge--warning';
  if (status === null || status === 'stopped') return 'status-badge';
  return 'status-badge status-badge--danger';
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
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let active = true;
    if (desktopApi === null) return undefined;
    void desktopApi.system.getStatus().then((nextRuntime) => {
      if (active) setRuntime(nextRuntime);
    });
    return () => {
      active = false;
    };
  }, [desktopApi]);

  const displayedRuntime = runtime ?? (desktopApi === null ? browserPreviewRequiredStatus : null);

  const checkConnection = async (): Promise<void> => {
    setChecking(true);
    try {
      if (desktopApi !== null) setRuntime(await desktopApi.system.ping());
    } finally {
      setChecking(false);
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
          <Stack gap={4}>
            <Title order={2}>Состояние локального контура</Title>
            <Text c="dimmed">{displayedRuntime?.message ?? 'Получение состояния…'}</Text>
          </Stack>
          <Badge
            className={statusClassName(displayedRuntime?.workerStatus ?? null)}
            size="lg"
            variant="light"
          >
            {displayedRuntime?.workerStatus ?? 'starting'}
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

        <Group mt="xl">
          <Button
            disabled={desktopApi === null}
            loading={checking}
            onClick={() => void checkConnection()}
          >
            Проверить связь
          </Button>
          <Button
            disabled={desktopApi === null || browserPreview}
            variant="default"
            onClick={() => void desktopApi?.system.openLog()}
          >
            Открыть журнал
          </Button>
        </Group>
      </Paper>
    </main>
  );
}
