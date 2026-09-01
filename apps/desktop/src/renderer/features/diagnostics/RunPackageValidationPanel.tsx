import { Badge, Button, Group, Text, Title } from '@mantine/core';
import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  DesktopError,
  ImpellerApi,
  RunPackageValidationJob,
  RunPackageValidationReport,
} from '@impeller-reliability/contracts';

const phaseLabel: Readonly<Record<RunPackageValidationJob['phase'], string>> = {
  source_check: 'Проверка исходного файла',
  outer_hash: 'Внешний SHA-256',
  zip_index: 'Структура ZIP',
  manifest: 'Manifest и inventory',
  payload_integrity: 'Целостность payload',
  semantic_validation: 'Доступная семантика',
  finalizing: 'Формирование результата',
};
const stateLabel: Readonly<Record<RunPackageValidationJob['state'], string>> = {
  queued: 'В очереди',
  running: 'Выполняется',
  cancelling: 'Отменяется',
  completed: 'Завершена',
  failed: 'Не выполнена',
  cancelled: 'Отменена',
};
const semanticLabel: Readonly<Record<string, string>> = {
  covered: 'Проверяется',
  not_available: 'Контракт отсутствует',
  contract_gap: 'Пробел контракта',
};
const coverageAreaLabel: Readonly<Record<string, string>> = {
  manifest: 'Манифест пакета',
  rbd_plan: 'План РБД',
  event_envelope: 'События запуска',
  inspection: 'Осмотры',
  provenance: 'Происхождение данных',
  accepted_projection: 'Зачтённые точки',
  measurement_model: 'Модель измерений',
  measurements_csv: 'Таблица измерений',
  checksums_sha256: 'Индекс контрольных сумм',
  remaining_payloads: 'Остальные данные',
};
const POLL_DELAY_MS = 350;
const MAX_POLL_DELAY_MS = 5_000;

interface RunPackageValidationPanelProps {
  readonly desktopApi: ImpellerApi | null;
  readonly workerReady: boolean;
}

export function RunPackageValidationPanel({
  desktopApi,
  workerReady,
}: RunPackageValidationPanelProps): React.JSX.Element {
  const [job, setJob] = useState<RunPackageValidationJob | null>(null);
  const [error, setError] = useState<DesktopError | null>(null);
  const [starting, setStarting] = useState(false);
  const generationRef = useRef(0);
  const transitionPendingRef = useRef(false);
  const primaryActionRef = useRef<HTMLButtonElement>(null);
  const cancelActionRef = useRef<HTMLButtonElement>(null);
  const restorePrimaryFocusRef = useRef(false);
  const wasActiveRef = useRef(false);
  const active = job !== null && ['queued', 'running', 'cancelling'].includes(job.state);
  const activeJobId = active ? job.jobId : null;
  const interruptionError: DesktopError | null =
    !workerReady && active
      ? {
          code: 'worker_unavailable',
          message:
            'Проверка прервана вместе с worker. Проект и выбранный пакет не изменялись; запустите новую проверку.',
          details: {},
          retryable: true,
        }
      : null;
  const displayedError = interruptionError ?? error;

  useEffect(() => {
    if (
      wasActiveRef.current &&
      !active &&
      job !== null &&
      restorePrimaryFocusRef.current &&
      document.activeElement === document.body
    ) {
      primaryActionRef.current?.focus();
    }
    if (!active) restorePrimaryFocusRef.current = false;
    wasActiveRef.current = active;
  }, [active, job]);

  useEffect(() => {
    if (desktopApi === null || !workerReady || activeJobId === null) return undefined;
    const generation = ++generationRef.current;
    const jobId = activeJobId;
    let timer: number | null = null;
    let consecutiveFailures = 0;
    const poll = (delayMs = POLL_DELAY_MS): void => {
      timer = window.setTimeout(() => {
        void desktopApi.runPackageValidation
          .get(jobId)
          .then((result) => {
            if (generation !== generationRef.current) return;
            if (result.ok) {
              consecutiveFailures = 0;
              setError(null);
              setJob(result.result);
              if (['queued', 'running', 'cancelling'].includes(result.result.state)) poll();
            } else if (result.error.code === 'entity_not_found') {
              setJob(null);
              setError({
                code: 'worker_unavailable',
                message: 'Проверка была прервана при перезапуске worker. Запустите новую проверку.',
                details: {},
                retryable: true,
              });
            } else {
              consecutiveFailures += 1;
              setError(result.error);
              poll(Math.min(MAX_POLL_DELAY_MS, POLL_DELAY_MS * 2 ** consecutiveFailures));
            }
          })
          .catch(() => {
            if (generation !== generationRef.current) return;
            consecutiveFailures += 1;
            setError(unavailableError());
            poll(Math.min(MAX_POLL_DELAY_MS, POLL_DELAY_MS * 2 ** consecutiveFailures));
          });
      }, delayMs);
    };
    poll();
    return () => {
      generationRef.current += 1;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [activeJobId, desktopApi, workerReady]);

  const discardTerminal = useCallback(async (): Promise<boolean> => {
    if (desktopApi === null || job === null) return true;
    if (active) return false;
    try {
      const result = await desktopApi.runPackageValidation.discard(job.jobId);
      if (!result.ok && result.error.code !== 'entity_not_found') {
        setError(result.error);
        return false;
      }
      setJob(null);
      return true;
    } catch {
      setError(unavailableError());
      return false;
    }
  }, [active, desktopApi, job]);

  const start = async (): Promise<void> => {
    if (desktopApi === null || !workerReady || starting || active || transitionPendingRef.current)
      return;
    transitionPendingRef.current = true;
    setStarting(true);
    restorePrimaryFocusRef.current = document.activeElement === primaryActionRef.current;
    setError(null);
    try {
      const result = await desktopApi.runPackageValidation.selectAndStart({
        jobId: crypto.randomUUID(),
        ...(job === null ? {} : { replaceJobId: job.jobId }),
      });
      if (!result.ok) {
        if (result.error.code !== 'cancelled') setError(result.error);
        return;
      }
      setJob(result.result);
    } catch {
      setError(unavailableError());
    } finally {
      transitionPendingRef.current = false;
      setStarting(false);
    }
  };

  const cancel = async (): Promise<void> => {
    if (desktopApi === null || job === null || !active || job.state === 'cancelling') return;
    setError(null);
    restorePrimaryFocusRef.current = document.activeElement === cancelActionRef.current;
    try {
      const result = await desktopApi.runPackageValidation.cancel(job.jobId);
      if (result.ok) setJob(result.result);
      else setError(result.error);
    } catch {
      setError(unavailableError());
    }
  };

  const clear = async (): Promise<void> => {
    if (transitionPendingRef.current) return;
    transitionPendingRef.current = true;
    setError(null);
    try {
      if (job === null) return;
      if (await discardTerminal()) {
        setJob(null);
        window.requestAnimationFrame(() => primaryActionRef.current?.focus());
      }
    } finally {
      transitionPendingRef.current = false;
    }
  };

  return (
    <section
      className="diagnostics-surface r130run-validation"
      aria-labelledby="r130run-validation-heading"
      data-validation-state={displayedError === null ? (job?.state ?? 'idle') : 'failed'}
    >
      <div className="r130run-validation__heading">
        <div>
          <Title id="r130run-validation-heading" order={2}>
            Проверка контракта R130SH
          </Title>
          <Text>
            Проверка только для чтения: структура, контрольные суммы и лишь та семантика, которая
            зафиксирована примерами R130SH.
          </Text>
        </div>
        <Badge variant="light" data-validation-badge={job?.state ?? 'idle'}>
          {displayedError !== null
            ? 'Требуется внимание'
            : job === null
              ? 'Не запускалась'
              : stateLabel[job.state]}
        </Badge>
      </div>

      <aside className="r130run-validation__scope" role="note">
        Проверка не импортирует данные в дело и не подтверждает пригодность результата для расчётов.
        Валидатор привязан к production M9a contract; producer compatibility подтверждается отдельно
        M03B acceptance.
      </aside>

      {displayedError !== null ? (
        <div className="feedback feedback--error" role="alert">
          <strong>Проверка не выполнена</strong>
          <span>{displayedError.message}</span>
        </div>
      ) : null}

      <Group className="r130run-validation__actions">
        <Button
          ref={primaryActionRef}
          disabled={desktopApi === null || !workerReady || active}
          loading={starting}
          onClick={() => void start()}
        >
          {job === null ? 'Проверить пакет R130SH' : 'Повторить проверку'}
        </Button>
        {active ? (
          <Button
            ref={cancelActionRef}
            variant="default"
            disabled={!workerReady || job?.state === 'cancelling'}
            onClick={() => void cancel()}
          >
            Отменить проверку
          </Button>
        ) : null}
        {job !== null && !active ? (
          <Button variant="subtle" disabled={starting} onClick={() => void clear()}>
            Очистить результат
          </Button>
        ) : null}
      </Group>

      {job === null ? (
        <div className="r130run-validation__empty">
          Выберите файл-кандидат .r130run. Абсолютный путь не будет показан или сохранён.
        </div>
      ) : (
        <JobStatus job={job} />
      )}
    </section>
  );
}

function JobStatus({ job }: { readonly job: RunPackageValidationJob }): React.JSX.Element {
  const progress = job.progress;
  const percent =
    progress.kind === 'unknown'
      ? null
      : progress.totalBytes > 0
        ? Math.min(100, Math.round((progress.completedBytes / progress.totalBytes) * 100))
        : progress.totalEntries > 0
          ? Math.min(100, Math.round((progress.completedEntries / progress.totalEntries) * 100))
          : 0;
  return (
    <div className="r130run-validation__result">
      <div className="r130run-validation__progress" role="status" aria-live="polite">
        <div>
          <strong>{stateLabel[job.state]}</strong>
          <span>{phaseLabel[job.phase]}</span>
        </div>
        {job.state === 'queued' || job.state === 'running' || job.state === 'cancelling' ? (
          percent === null ? (
            <div
              className="r130run-progress r130run-progress--unknown"
              role="progressbar"
              aria-label="Ход проверки пакета"
              aria-valuetext={phaseLabel[job.phase]}
            />
          ) : (
            <progress
              className="r130run-progress"
              max={100}
              value={percent}
              aria-label={`${phaseLabel[job.phase]}: ${String(percent)}%`}
            />
          )
        ) : null}
        <Text size="sm">
          {formatBytes(progress.completedBytes)} / {formatBytes(progress.totalBytes)} · записей{' '}
          {progress.completedEntries} / {progress.totalEntries}
        </Text>
      </div>
      {job.state === 'completed' ? <ValidationReport report={job.report} /> : null}
      {job.state === 'failed' || job.state === 'cancelled' ? (
        <div
          className="feedback feedback--warning"
          role={job.state === 'failed' ? 'alert' : 'status'}
        >
          <strong>{job.state === 'cancelled' ? 'Проверка отменена' : 'Проверка прервана'}</strong>
          <span>{job.typedError.message}</span>
        </div>
      ) : null}
    </div>
  );
}

function ValidationReport({
  report,
}: {
  readonly report: RunPackageValidationReport;
}): React.JSX.Element {
  return (
    <div className="r130run-report" data-structural-verdict={report.structuralVerdict}>
      <div className="r130run-report__verdicts">
        <div>
          <span>Структурная проверка</span>
          <strong>{report.structuralVerdict === 'passed' ? 'Пройдена' : 'Не пройдена'}</strong>
        </div>
        <div>
          <span>Семантическое покрытие</span>
          <strong>{semanticVerdictLabel(report.semanticVerdict)}</strong>
        </div>
      </div>
      <dl className="r130run-report__identity">
        <ReportValue label="Файл" value={report.sourceFileName} />
        <ReportValue label="Идентификатор пакета" value={report.packageId ?? 'Недоступен'} />
        <ReportValue label="Идентификатор запуска" value={report.runId ?? 'Недоступен'} />
        <ReportValue label="Редакция экспорта" value={report.exportRevision ?? 'Недоступна'} />
        <ReportValue label="Вид пакета" value={report.packageKind ?? 'Недоступен'} />
        <ReportValue label="Схема контракта" value={report.contractSchema} />
        <ReportValue label="Версия валидатора" value={report.validatorVersion} />
        <ReportValue label="SHA-256 файла" value={report.outerPackageSha256} wide />
        <ReportValue label="Размер" value={formatBytes(report.outerSizeBytes)} />
        <ReportValue label="Записей ZIP" value={report.entryCount} />
        <ReportValue label="Commit исходного контракта" value={report.upstreamCommit} wide />
      </dl>
      <div className="r130run-report__coverage">
        <Title order={3}>Покрытие контракта</Title>
        <ul>
          {report.semanticCoverage.map((item) => (
            <li key={item.area}>
              <span>{coverageAreaLabel[item.area] ?? item.area}</span>
              <span>
                <strong>{semanticLabel[item.status] ?? item.status}</strong>
                <small>{item.contractSource}</small>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="r130run-report__findings" aria-label="Результаты проверки пакета">
        <Title order={3}>Замечания проверки</Title>
        <Text size="sm">
          Ошибок: {report.findingCounts.error} · предупреждений: {report.findingCounts.warning} ·
          сведений: {report.findingCounts.info}
          {report.findingCounts.truncated ? ' · подробный список сокращён' : ''}
        </Text>
        {report.findings.length === 0 ? (
          <Text>Ошибок в проверяемой части контракта не найдено.</Text>
        ) : (
          <ul>
            {report.findings.map((finding, index) => (
              <li key={`${finding.code}-${String(index)}`} data-severity={finding.severity}>
                <strong>{finding.code}</strong>
                <span className="r130run-finding__severity">
                  {findingSeverityLabel(finding.severity)}
                </span>
                <span>{finding.message}</span>
                <small>
                  {finding.location} · {finding.contractSource}
                </small>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ReportValue({
  label,
  value,
  wide = false,
}: {
  readonly label: string;
  readonly value: string | number;
  readonly wide?: boolean;
}): React.JSX.Element {
  return (
    <div className={wide ? 'r130run-report__wide' : undefined}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function semanticVerdictLabel(value: RunPackageValidationReport['semanticVerdict']): string {
  if (value === 'passed') return 'Полное в доступном профиле';
  if (value === 'partial') return 'Частичное';
  if (value === 'failed') return 'Не пройдено';
  return 'Недоступно';
}

function findingSeverityLabel(value: 'error' | 'warning' | 'info'): string {
  if (value === 'error') return 'Ошибка';
  if (value === 'warning') return 'Предупреждение';
  return 'Сведение';
}

function formatBytes(value: number): string {
  if (value <= 0) return '0 Б';
  if (value < 1024) return `${String(value)} Б`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КиБ`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} МиБ`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} ГиБ`;
}

function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Локальный worker недоступен. Запустите проверку повторно после восстановления.',
    details: {},
    retryable: true,
  };
}
