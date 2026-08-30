import { Badge, Button, Group, Stack, Text, Title } from '@mantine/core';

import type { ImpellerApi, RuntimeStatus } from '@impeller-reliability/contracts';
import type { UiPhase } from '../../app/App';
import { RunPackageValidationPanel } from './RunPackageValidationPanel';

const phaseLabel: Readonly<Record<UiPhase, string>> = {
  loading: 'Запуск',
  ready: 'Готов',
  unavailable: 'Недоступен',
  reconnecting: 'Перезапуск',
  error: 'Ошибка',
  stopped: 'Остановлен',
};

interface DiagnosticsPanelProps {
  readonly browserPreview: boolean;
  readonly desktopApi: ImpellerApi | null;
  readonly runtime: RuntimeStatus | null;
  readonly phase: UiPhase;
  readonly message: string;
  readonly checking: boolean;
  readonly restarting: boolean;
  readonly onCheck: () => void;
  readonly onRestart: () => void;
}

export function DiagnosticsPanel(props: DiagnosticsPanelProps): React.JSX.Element {
  const {
    browserPreview,
    desktopApi,
    runtime,
    phase,
    message,
    checking,
    restarting,
    onCheck,
    onRestart,
  } = props;
  return (
    <div className="diagnostics-view">
      <div className="page-heading">
        <Title order={1}>Диагностика приложения</Title>
        <Text>Техническое состояние локального контура вынесено из основного рабочего потока.</Text>
      </div>
      <section className="diagnostics-surface" aria-labelledby="runtime-heading">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={4} className="diagnostics-copy">
            <Title id="runtime-heading" order={2}>
              Локальный расчётный контур
            </Title>
            <Text className="status-message" aria-live="polite">
              {message}
            </Text>
          </Stack>
          <Badge className={`status-badge status-badge--${phase}`} size="lg" variant="light">
            {phaseLabel[phase]}
          </Badge>
        </Group>
        <Group className="status-actions">
          <Button disabled={desktopApi === null || restarting} loading={checking} onClick={onCheck}>
            Проверить связь
          </Button>
          <Button
            disabled={desktopApi === null || checking}
            loading={restarting}
            variant="default"
            onClick={onRestart}
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
        <dl className="status-grid">
          <div>
            <dt>Версия приложения</dt>
            <dd>{runtime?.applicationVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>Electron</dt>
            <dd>{runtime?.electronVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>Версия Python worker</dt>
            <dd>{runtime?.workerVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>Версия протокола IPC</dt>
            <dd>{runtime?.protocolVersion ?? '—'}</dd>
          </div>
          <div>
            <dt>Состояние SQLite</dt>
            <dd>{runtime?.sqliteStatus ?? 'pending'}</dd>
          </div>
          <div>
            <dt>Режим</dt>
            <dd>
              {runtime === null
                ? '—'
                : runtime.mode === 'development'
                  ? 'Разработка'
                  : 'Пакетная сборка'}
            </dd>
          </div>
        </dl>
      </section>
      <RunPackageValidationPanel desktopApi={desktopApi} workerReady={phase === 'ready'} />
    </div>
  );
}
