import { Button, Checkbox, Group, Select, Text, Textarea, TextInput, Title } from '@mantine/core';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';

import type {
  CaseDocumentSummary,
  DesktopError,
  ImpellerApi,
  ReliabilityDatasetVersion,
  ReliabilityExecution,
  ReliabilityExecutionSummary,
  ReliabilityObservationVersion,
  WheelModelSummary,
} from '@impeller-reliability/contracts';

interface ReliabilityPreparationProps {
  readonly desktopApi: ImpellerApi;
  readonly disabled: boolean;
  readonly onDirtyChange: (dirty: boolean) => void;
  readonly onPendingChange: (pending: boolean) => void;
}

export interface ReliabilityPreparationHandle {
  discardDraft(): void;
  waitForPendingSave(): Promise<void>;
  verifyAfterReattach(): Promise<boolean>;
}

interface ObservationDraft {
  readonly classification: 'failure' | 'right_censored' | 'withdrawn' | 'invalid';
  readonly endpointKind: 'exact' | 'right_bound' | 'interval' | 'unavailable';
  readonly metricKind: 'rbd_steady_rotation_time' | 'rpt_start_stop_cycles' | null;
  readonly metricUnit: 'hours' | 'count' | null;
  readonly lowerValue: string;
  readonly upperValue: string;
  readonly originBasis: string;
  readonly endpointBasis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
  readonly reason: string;
  readonly failureIds: readonly string[];
}

interface CandidateDecision {
  readonly observationVersionId: string;
  readonly decision: 'pending' | 'included' | 'excluded';
  readonly reason: string;
}

const emptyObservation: ObservationDraft = {
  classification: 'invalid',
  endpointKind: 'unavailable',
  metricKind: null,
  metricUnit: null,
  lowerValue: '',
  upperValue: '',
  originBasis: '',
  endpointBasis: '',
  documentId: null,
  documentLocator: '',
  reason: '',
  failureIds: [],
};

const classificationOptions = [
  { value: 'failure', label: 'Подтверждённый отказ' },
  { value: 'right_censored', label: 'Правое цензурирование' },
  { value: 'withdrawn', label: 'Снято с наблюдения' },
  { value: 'invalid', label: 'Недействительно для назначения' },
];
const endpointOptions = [
  { value: 'exact', label: 'Точное значение' },
  { value: 'right_bound', label: 'Правая граница наблюдения' },
  { value: 'interval', label: 'Интервал возникновения' },
  { value: 'unavailable', label: 'Числовая граница неизвестна' },
];

export const ReliabilityPreparation = forwardRef<
  ReliabilityPreparationHandle,
  ReliabilityPreparationProps
>(function ReliabilityPreparation(
  { desktopApi, disabled, onDirtyChange, onPendingChange },
  ref,
): React.JSX.Element {
  const [wheels, setWheels] = useState<readonly WheelModelSummary[]>([]);
  const [wheelModelId, setWheelModelId] = useState<string | null>(null);
  const [executions, setExecutions] = useState<readonly ReliabilityExecutionSummary[]>([]);
  const [executionCursor, setExecutionCursor] = useState<string | null>(null);
  const [execution, setExecution] = useState<ReliabilityExecution | null>(null);
  const [versions, setVersions] = useState<readonly ReliabilityObservationVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<ReliabilityObservationVersion | null>(
    null,
  );
  const [documents, setDocuments] = useState<readonly CaseDocumentSummary[]>([]);
  const [observationDraft, setObservationDraft] = useState<ObservationDraft>(emptyObservation);
  const [observationId, setObservationId] = useState<string>(() => crypto.randomUUID());
  const [observationVersionId, setObservationVersionId] = useState<string>(() =>
    crypto.randomUUID(),
  );
  const [observationDirty, setObservationDirty] = useState(false);
  const [candidateDecisions, setCandidateDecisions] = useState<
    Readonly<Record<string, CandidateDecision>>
  >({});
  const [datasetTitle, setDatasetTitle] = useState('Выборка наработки');
  const [datasetMethod, setDatasetMethod] = useState<'rbd' | 'rpt' | null>(null);
  const [datasetMetricKind, setDatasetMetricKind] = useState<
    'rbd_steady_rotation_time' | 'rpt_start_stop_cycles' | null
  >(null);
  const [datasetMetricUnit, setDatasetMetricUnit] = useState<'hours' | 'count' | null>(null);
  const [populationBasis, setPopulationBasis] = useState('Рабочие колёса выбранной модели');
  const [methodologyBasis, setMethodologyBasis] = useState('Выбранная инженером методика');
  const [comparabilityBasis, setComparabilityBasis] = useState(
    'Сопоставимые условия подтверждены инженером',
  );
  const [datasetReason, setDatasetReason] = useState(
    'Зафиксирован состав рассмотренных наблюдений',
  );
  const [datasetId, setDatasetId] = useState<string>(() => crypto.randomUUID());
  const [datasetVersionId, setDatasetVersionId] = useState<string>(() => crypto.randomUUID());
  const [datasetDirty, setDatasetDirty] = useState(false);
  const [datasets, setDatasets] = useState<readonly ReliabilityDatasetVersion[]>([]);
  const [datasetCursor, setDatasetCursor] = useState<string | null>(null);
  const [selectedDataset, setSelectedDataset] = useState<ReliabilityDatasetVersion | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<DesktopError | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const pendingRef = useRef<Promise<void> | null>(null);
  const selectionRef = useRef(0);
  const dirty = observationDirty || datasetDirty;

  const loadWheel = useCallback(
    async (selectedWheelId: string): Promise<boolean> => {
      const revision = ++selectionRef.current;
      setExecutions([]);
      setExecution(null);
      setVersions([]);
      setSelectedVersion(null);
      setDatasets([]);
      setExecutionCursor(null);
      setDatasetCursor(null);
      setCandidateDecisions({});
      setSelectedDataset(null);
      setDatasetId(crypto.randomUUID());
      setDatasetVersionId(crypto.randomUUID());
      setDatasetDirty(false);
      setDatasetTitle('Выборка наработки');
      setDatasetMethod(null);
      setDatasetMetricKind(null);
      setDatasetMetricUnit(null);
      setPopulationBasis('Рабочие колёса выбранной модели');
      setMethodologyBasis('Выбранная инженером методика');
      setComparabilityBasis('Сопоставимые условия подтверждены инженером');
      setDatasetReason('Зафиксирован состав рассмотренных наблюдений');
      const executionResult = await desktopApi.reliabilityExecution.listPage(
        selectedWheelId,
        null,
        25,
      );
      if (revision !== selectionRef.current) return false;
      const datasetResult = await desktopApi.reliabilityDataset.listPage(selectedWheelId, null, 25);
      if (revision !== selectionRef.current) return false;
      const documentResult = await desktopApi.caseDocument.list({
        includeArchived: false,
        documentKind: null,
      });
      if (revision !== selectionRef.current) return false;
      if (!executionResult.ok) {
        setError(executionResult.error);
        return false;
      }
      if (!datasetResult.ok) {
        setError(datasetResult.error);
        return false;
      }
      if (!documentResult.ok) {
        setError(documentResult.error);
        return false;
      }
      setDocuments(documentResult.result);
      setExecutions(executionResult.result.items);
      setExecutionCursor(executionResult.result.nextCursor);
      setDatasetCursor(datasetResult.result.nextCursor);
      const loadedDatasets: ReliabilityDatasetVersion[] = [];
      for (const item of datasetResult.result.items) {
        const detail = await desktopApi.reliabilityDataset.getVersion(item.latestVersionId);
        if (revision !== selectionRef.current) return false;
        if (!detail.ok) {
          setError(detail.error);
          return false;
        }
        loadedDatasets.push(detail.result);
      }
      setDatasets(loadedDatasets);
      setCandidateDecisions(
        Object.fromEntries(
          executionResult.result.items
            .filter((item) => item.currentObservationVersionId !== null)
            .map((item) => [
              item.executionId,
              {
                observationVersionId: item.currentObservationVersionId as string,
                decision: 'pending',
                reason: '',
              },
            ]),
        ),
      );
      return true;
    },
    [desktopApi],
  );

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const wheelResult = await desktopApi.wheelModel.list(false);
        if (!active) return;
        if (!wheelResult.ok) setError(wheelResult.error);
        else setWheels(wheelResult.result);
        const documentResult = await desktopApi.caseDocument.list({
          includeArchived: false,
          documentKind: null,
        });
        if (!active) return;
        if (!documentResult.ok) setError(documentResult.error);
        else setDocuments(documentResult.result);
      } catch {
        if (active) setError(unavailableError());
      }
    })();
    return () => {
      active = false;
    };
  }, [desktopApi]);

  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    onPendingChange(busy !== null);
    return () => onPendingChange(false);
  }, [busy, onPendingChange]);

  const runPending = (key: string, action: () => Promise<void>): Promise<void> => {
    setBusy(key);
    setError(null);
    setMessage(null);
    const pending = action()
      .catch(() => setError(unavailableError()))
      .finally(() => {
        pendingRef.current = null;
        setBusy(null);
      });
    pendingRef.current = pending;
    return pending;
  };

  const selectExecution = async (executionId: string): Promise<void> => {
    if (dirty) return;
    const revision = ++selectionRef.current;
    setExecution(null);
    const detail = await desktopApi.reliabilityExecution.getDetail(executionId);
    if (revision !== selectionRef.current) return;
    const versionList = await desktopApi.reliabilityObservation.listVersions(executionId);
    if (revision !== selectionRef.current) return;
    if (!detail.ok) return setError(detail.error);
    if (!versionList.ok) return setError(versionList.error);
    setExecution(detail.result);
    setVersions(versionList.result);
    const latest = versionList.result[0] ?? null;
    setSelectedVersion(latest);
    if (latest === null) {
      setObservationId(crypto.randomUUID());
      setObservationDraft(emptyObservation);
    } else {
      setObservationId(latest.observationId);
      setObservationDraft(versionToDraft(latest));
    }
    setObservationVersionId(crypto.randomUUID());
    setObservationDirty(false);
  };

  const saveObservation = (): Promise<void> =>
    runPending('observation-save', async () => {
      if (execution === null || observationDraft.documentId === null) return;
      const result = await desktopApi.reliabilityObservation.createVersion({
        observationId,
        observationVersionId,
        executionId: execution.executionId,
        expectedPreviousVersionId: versions[0]?.observationVersionId ?? null,
        classification: observationDraft.classification,
        endpointKind: observationDraft.endpointKind,
        metricKind: observationDraft.metricKind,
        metricUnit: observationDraft.metricUnit,
        metricOrigin: observationDraft.metricKind === null ? null : 'analyst_provided',
        lowerValue: observationDraft.lowerValue.trim() === '' ? null : observationDraft.lowerValue,
        upperValue: observationDraft.upperValue.trim() === '' ? null : observationDraft.upperValue,
        originBasis: observationDraft.originBasis,
        endpointBasis: observationDraft.endpointBasis,
        documentId: observationDraft.documentId,
        documentLocator: observationDraft.documentLocator,
        failureIds: [...observationDraft.failureIds],
        actor: 'local_user',
        reason: observationDraft.reason,
      });
      if (!result.ok) return setError(result.error);
      const next = [result.result.version, ...versions];
      setVersions(next);
      setSelectedVersion(result.result.version);
      setObservationVersionId(crypto.randomUUID());
      setObservationDirty(false);
      setExecutions((current) =>
        current.map((item) =>
          item.executionId === execution.executionId
            ? {
                ...item,
                currentObservationVersionId: result.result.version.observationVersionId,
                currentObservationVersionNumber: result.result.version.versionNumber,
                currentClassification: result.result.version.classification,
              }
            : item,
        ),
      );
      setCandidateDecisions((current) => ({
        ...current,
        [execution.executionId]: {
          observationVersionId: result.result.version.observationVersionId,
          decision: 'pending',
          reason: '',
        },
      }));
      setDatasetDirty(true);
      setMessage(
        result.result.disposition === 'existing'
          ? 'Уже сохранённая версия восстановлена после повтора.'
          : `Версия интерпретации ${String(result.result.version.versionNumber)} сохранена.`,
      );
    });

  const saveDataset = (): Promise<void> =>
    runPending('dataset-save', async () => {
      if (
        wheelModelId === null ||
        datasetMethod === null ||
        datasetMetricKind === null ||
        datasetMetricUnit === null
      )
        return;
      const decisions = Object.values(candidateDecisions);
      const resolvedDecisions = decisions.flatMap((item) =>
        item.decision === 'pending'
          ? []
          : [
              {
                observationVersionId: item.observationVersionId,
                decision: item.decision,
                reason: item.reason,
              },
            ],
      );
      if (decisions.length === 0 || resolvedDecisions.length !== decisions.length) return;
      const result = await desktopApi.reliabilityDataset.createVersion({
        datasetId,
        datasetVersionId,
        wheelModelId,
        expectedPreviousVersionId:
          datasets
            .filter((item) => item.datasetId === datasetId)
            .sort((left, right) => right.versionNumber - left.versionNumber)[0]?.datasetVersionId ??
          null,
        title: datasetTitle,
        method: datasetMethod,
        metricKind: datasetMetricKind,
        metricUnit: datasetMetricUnit,
        populationBasis,
        methodologyBasis,
        comparabilityBasis,
        decisions: resolvedDecisions,
        actor: 'local_user',
        reason: datasetReason,
      });
      if (!result.ok) return setError(result.error);
      setSelectedDataset(result.result.version);
      setDatasets((current) => [
        result.result.version,
        ...current.filter(
          (item) => item.datasetVersionId !== result.result.version.datasetVersionId,
        ),
      ]);
      setDatasetVersionId(crypto.randomUUID());
      setDatasetDirty(false);
      setMessage(
        result.result.disposition === 'existing'
          ? 'Уже сохранённая версия выборки восстановлена после повтора.'
          : `Версия выборки ${String(result.result.version.versionNumber)} сохранена.`,
      );
    });

  useImperativeHandle(
    ref,
    () => ({
      discardDraft: () => {
        setObservationDraft(
          selectedVersion === null ? emptyObservation : versionToDraft(selectedVersion),
        );
        setObservationVersionId(crypto.randomUUID());
        setObservationDirty(false);
        setDatasetDirty(false);
      },
      waitForPendingSave: async () => pendingRef.current ?? Promise.resolve(),
      verifyAfterReattach: async () => {
        const observationProbe =
          await desktopApi.reliabilityObservation.getVersion(observationVersionId);
        if (observationProbe.ok) {
          const restored = observationProbe.result;
          setVersions((current) => [
            restored,
            ...current.filter(
              (item) => item.observationVersionId !== restored.observationVersionId,
            ),
          ]);
          setSelectedVersion(restored);
          setObservationDraft(versionToDraft(restored));
          setObservationVersionId(crypto.randomUUID());
          setObservationDirty(false);
          setExecutions((current) =>
            current.map((item) =>
              item.executionId === restored.executionId
                ? {
                    ...item,
                    currentObservationVersionId: restored.observationVersionId,
                    currentObservationVersionNumber: restored.versionNumber,
                    currentClassification: restored.classification,
                  }
                : item,
            ),
          );
          setCandidateDecisions((current) => ({
            ...current,
            [restored.executionId]: {
              observationVersionId: restored.observationVersionId,
              decision: 'pending',
              reason: 'Версия восстановлена после потери ответа; решение нужно подтвердить',
            },
          }));
          setDatasetDirty(true);
        }
        const datasetProbe = await desktopApi.reliabilityDataset.getVersion(datasetVersionId);
        if (datasetProbe.ok) {
          const restored = datasetProbe.result;
          setSelectedDataset(restored);
          setDatasets((current) => [
            restored,
            ...current.filter((item) => item.datasetVersionId !== restored.datasetVersionId),
          ]);
          setDatasetVersionId(crypto.randomUUID());
          setDatasetDirty(false);
        }
        if (observationProbe.ok || datasetProbe.ok || dirty) return true;
        return wheelModelId === null ? true : loadWheel(wheelModelId);
      },
    }),
    [
      datasetVersionId,
      desktopApi,
      dirty,
      loadWheel,
      observationVersionId,
      selectedVersion,
      wheelModelId,
    ],
  );

  const updateObservation = (patch: Partial<ObservationDraft>): void => {
    setObservationDraft((current) => ({ ...current, ...patch }));
    setObservationDirty(true);
  };
  const datasetHistoryCursor =
    selectedDataset === null
      ? null
      : (datasets
          .filter((item) => item.datasetId === selectedDataset.datasetId)
          .sort((left, right) => left.versionNumber - right.versionNumber)[0]?.previousVersionId ??
        null);

  return (
    <section
      className="project-surface reliability-preparation"
      aria-labelledby="reliability-title"
      aria-busy={busy !== null}
    >
      <div className="section-heading">
        <div>
          <Title id="reliability-title" order={2}>
            Данные надёжности
          </Title>
          <Text>
            Исходный результат, решение инженера и зафиксированная выборка без расчёта показателей.
          </Text>
        </div>
        <Select
          label="Модель рабочего колеса"
          placeholder="Выберите модель"
          data={wheels.map((item) => ({ value: item.wheelModelId, label: item.fullName }))}
          value={wheelModelId}
          disabled={disabled || busy !== null || dirty}
          onChange={(value) => {
            setWheelModelId(value);
            if (value !== null)
              void runPending('wheel-load', async () => {
                await loadWheel(value);
              });
          }}
        />
      </div>
      {error === null ? null : (
        <div className="feedback feedback--error" role="alert">
          <strong>{error.message}</strong>
        </div>
      )}
      {message === null ? null : (
        <div className="feedback feedback--success" role="status">
          {message}
        </div>
      )}
      {busy === null ? null : (
        <Text role="status" size="sm">
          {busy.includes('save')
            ? 'Сохраняется новая неизменяемая версия…'
            : 'Загружаются данные проекта…'}
        </Text>
      )}
      <div className="reliability-master-detail">
        <section aria-labelledby="execution-list-title">
          <Title id="execution-list-title" order={3}>
            Исполнения
          </Title>
          {executions.length === 0 && busy !== 'wheel-load' ? (
            <Text size="sm">Для выбранной модели пока нет сохранённых исполнений.</Text>
          ) : null}
          <div className="reliability-record-list">
            {executions.map((item) => (
              <button
                key={item.executionId}
                type="button"
                className="reliability-record"
                aria-pressed={execution?.executionId === item.executionId}
                disabled={disabled || busy !== null || dirty}
                onClick={() =>
                  void runPending('execution-detail', () => selectExecution(item.executionId))
                }
              >
                <strong>{methodLabel(item.method)}</strong>
                <span>
                  {item.sourceRunId} · редакция {String(item.exportRevision)}
                </span>
                <span>{classificationLabel(item.currentClassification)}</span>
              </button>
            ))}
          </div>
          {executionCursor === null || wheelModelId === null ? null : (
            <Button
              variant="subtle"
              disabled={busy !== null || dirty}
              onClick={() =>
                void runPending('more-executions', async () => {
                  const result = await desktopApi.reliabilityExecution.listPage(
                    wheelModelId,
                    executionCursor,
                    25,
                  );
                  if (!result.ok) return setError(result.error);
                  setExecutions((current) => [...current, ...result.result.items]);
                  setCandidateDecisions((current) => ({
                    ...current,
                    ...Object.fromEntries(
                      result.result.items
                        .filter((item) => item.currentObservationVersionId !== null)
                        .map((item) => [
                          item.executionId,
                          current[item.executionId] ?? {
                            observationVersionId: item.currentObservationVersionId as string,
                            decision: 'pending',
                            reason: '',
                          },
                        ]),
                    ),
                  }));
                  setExecutionCursor(result.result.nextCursor);
                })
              }
            >
              Показать ещё
            </Button>
          )}
          {datasetCursor === null || wheelModelId === null ? null : (
            <Button
              variant="subtle"
              disabled={busy !== null || dirty}
              onClick={() =>
                void runPending('more-datasets', async () => {
                  const result = await desktopApi.reliabilityDataset.listPage(
                    wheelModelId,
                    datasetCursor,
                    25,
                  );
                  if (!result.ok) return setError(result.error);
                  const more: ReliabilityDatasetVersion[] = [];
                  for (const item of result.result.items) {
                    const detail = await desktopApi.reliabilityDataset.getVersion(
                      item.latestVersionId,
                    );
                    if (!detail.ok) return setError(detail.error);
                    more.push(detail.result);
                  }
                  setDatasets((current) => [...current, ...more]);
                  setDatasetCursor(result.result.nextCursor);
                })
              }
            >
              Показать ещё сохранённые выборки
            </Button>
          )}
        </section>
        <section aria-labelledby="execution-detail-title">
          <Title id="execution-detail-title" order={3}>
            Интерпретация инженера
          </Title>
          {execution === null ? (
            <Text size="sm">
              Выберите исполнение, чтобы увидеть исходные сведения и сохранить решение.
            </Text>
          ) : (
            <>
              <dl className="reliability-facts">
                <div>
                  <dt>Метод</dt>
                  <dd>{methodLabel(execution.method)}</dd>
                </div>
                <div>
                  <dt>Source run / export revision</dt>
                  <dd>
                    {execution.sourceRunId} · {String(execution.exportRevision)} ·{' '}
                    {execution.packageKind}
                  </dd>
                </div>
                <div>
                  <dt>WheelModel snapshot</dt>
                  <dd>{execution.wheelModelId}</dd>
                </div>
                <div>
                  <dt>Local / source Specimen</dt>
                  <dd>
                    {execution.localSpecimenId} / {execution.sourceSpecimenId}
                  </dd>
                </div>
                <div>
                  <dt>SHA-256 исходного пакета</dt>
                  <dd>{execution.sourceOuterPackageSha256}</dd>
                </div>
                <div>
                  <dt>Техническое завершение</dt>
                  <dd>{sourceValue(execution.resultSummary['technicalStatus'])}</dd>
                </div>
                <div>
                  <dt>Заключение об образце</dt>
                  <dd>{sourceValue(execution.resultSummary['specimenOutcome'])}</dd>
                </div>
                <div>
                  <dt>Пригодность запуска</dt>
                  <dd>{sourceValue(execution.resultSummary['runValidity'])}</dd>
                </div>
                <div>
                  <dt>Причина завершения</dt>
                  <dd>{sourceValue(execution.resultSummary['terminationReason'])}</dd>
                </div>
                <div>
                  <dt>Полнота данных</dt>
                  <dd>{sourceValue(execution.resultSummary['dataCompleteness'])}</dd>
                </div>
                <div>
                  <dt>Свидетельства</dt>
                  <dd>{String(execution.failureObservations.length)}</dd>
                </div>
              </dl>
              <Text size="sm">
                Время завершения запуска и accepted elapsed не используются как точная наработка.
              </Text>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveObservation();
                }}
              >
                <div className="reliability-form-grid">
                  <Select
                    label="Классификация"
                    required
                    data={classificationOptions}
                    value={observationDraft.classification}
                    onChange={(value) =>
                      value === null
                        ? undefined
                        : updateObservation({
                            classification: value as ObservationDraft['classification'],
                          })
                    }
                  />
                  <Select
                    label="Форма границы"
                    required
                    data={endpointOptions}
                    value={observationDraft.endpointKind}
                    onChange={(value) =>
                      value === null
                        ? undefined
                        : updateObservation({
                            endpointKind: value as ObservationDraft['endpointKind'],
                            ...(value === 'unavailable'
                              ? {
                                  metricKind: null,
                                  metricUnit: null,
                                  lowerValue: '',
                                  upperValue: '',
                                }
                              : {}),
                          })
                    }
                  />
                  <Select
                    label="Вид показателя наработки"
                    data={[
                      {
                        value: 'rbd_steady_rotation_time',
                        label: 'Время установившегося вращения РБД',
                      },
                      { value: 'rpt_start_stop_cycles', label: 'Циклы «пуск–торможение» РПТ' },
                    ]}
                    clearable
                    value={observationDraft.metricKind}
                    onChange={(value) =>
                      updateObservation({
                        metricKind: value,
                      })
                    }
                  />
                  <Select
                    label="Единица наработки"
                    data={[
                      { value: 'hours', label: 'часы' },
                      { value: 'count', label: 'циклы, целое число' },
                    ]}
                    clearable
                    value={observationDraft.metricUnit}
                    onChange={(value) =>
                      updateObservation({
                        metricUnit: value,
                      })
                    }
                  />
                  <TextInput
                    label="Значение или нижняя граница"
                    description="Canonical decimal string; смысл задаётся видом показателя"
                    value={observationDraft.lowerValue}
                    maxLength={64}
                    onChange={(event) =>
                      updateObservation({ lowerValue: event.currentTarget.value })
                    }
                  />
                  {observationDraft.endpointKind === 'interval' ? (
                    <TextInput
                      label="Верхняя граница"
                      description="Верхняя граница того же показателя"
                      value={observationDraft.upperValue}
                      maxLength={64}
                      onChange={(event) =>
                        updateObservation({ upperValue: event.currentTarget.value })
                      }
                    />
                  ) : null}
                  <Textarea
                    label="Начало отсчёта"
                    required
                    value={observationDraft.originBasis}
                    maxLength={1000}
                    onChange={(event) =>
                      updateObservation({ originBasis: event.currentTarget.value })
                    }
                  />
                  <Textarea
                    label="Основание границы"
                    required
                    value={observationDraft.endpointBasis}
                    maxLength={1000}
                    onChange={(event) =>
                      updateObservation({ endpointBasis: event.currentTarget.value })
                    }
                  />
                  <Select
                    label="Документ-основание"
                    required
                    description={
                      documents.length === 0
                        ? 'Сначала зарегистрируйте применимый документ дела.'
                        : `Доступно документов: ${String(documents.length)}`
                    }
                    data={documents.map((item) => ({
                      value: item.caseDocumentId,
                      label: `${item.title}${item.designation === '' ? '' : ` · ${item.designation}`}`,
                    }))}
                    value={observationDraft.documentId}
                    onChange={(value) => updateObservation({ documentId: value })}
                  />
                  <TextInput
                    label="Раздел или запись документа"
                    required
                    value={observationDraft.documentLocator}
                    maxLength={1000}
                    onChange={(event) =>
                      updateObservation({ documentLocator: event.currentTarget.value })
                    }
                  />
                </div>
                <Textarea
                  label="Основание решения"
                  required
                  value={observationDraft.reason}
                  maxLength={2000}
                  onChange={(event) => updateObservation({ reason: event.currentTarget.value })}
                />
                <fieldset className="reliability-evidence">
                  <legend>Свидетельства, выбранные инженером</legend>
                  {execution.failureObservations.length === 0 ? (
                    <Text size="sm">В исполнении нет отдельных FailureObservation.</Text>
                  ) : (
                    execution.failureObservations.map((item) => (
                      <div key={item.failureId}>
                        <Checkbox
                          label={`${item.failureType} · ${item.subjectKind} · ${item.sourceFieldReference}`}
                          checked={observationDraft.failureIds.includes(item.failureId)}
                          onChange={(event) =>
                            updateObservation({
                              failureIds: event.currentTarget.checked
                                ? [...observationDraft.failureIds, item.failureId]
                                : observationDraft.failureIds.filter((id) => id !== item.failureId),
                            })
                          }
                        />
                        <Text size="xs" c="dimmed">
                          {item.failureId} · {item.sourceEventReference} · обнаружено:{' '}
                          {item.observedAtUtc ?? 'не установлено'} · source SHA-256:{' '}
                          {item.sourceOuterPackageSha256}
                        </Text>
                      </div>
                    ))
                  )}
                </fieldset>
                <Group className="form-actions">
                  <Button
                    type="submit"
                    loading={busy === 'observation-save'}
                    disabled={
                      disabled ||
                      busy !== null ||
                      !observationDirty ||
                      !observationReady(observationDraft)
                    }
                  >
                    Сохранить новую версию
                  </Button>
                </Group>
              </form>
              {versions.length === 0 ? null : (
                <div className="reliability-version-strip" aria-label="Версии интерпретации">
                  {versions.map((item) => (
                    <button
                      type="button"
                      key={item.observationVersionId}
                      aria-pressed={
                        selectedVersion?.observationVersionId === item.observationVersionId
                      }
                      disabled={dirty || busy !== null}
                      onClick={() => {
                        setSelectedVersion(item);
                        setObservationDraft(versionToDraft(item));
                        setObservationDirty(false);
                      }}
                    >
                      Версия {String(item.versionNumber)} ·{' '}
                      {classificationLabel(item.classification)}
                    </button>
                  ))}
                </div>
              )}
              {selectedVersion === null ? null : (
                <div className="reliability-dataset-readback">
                  <strong>Сохранённая версия {String(selectedVersion.versionNumber)}</strong>
                  <span>
                    Происхождение наработки:{' '}
                    {selectedVersion.metricOrigin ?? 'значение отсутствует'}
                  </span>
                  <span>
                    Документ snapshot: {selectedVersion.documentSnapshot.title} · редакция{' '}
                    {selectedVersion.documentSnapshot.revisionLabel || 'не указана'} · record{' '}
                    {String(selectedVersion.documentSnapshot.recordRevision)} · SHA-256{' '}
                    {selectedVersion.documentSnapshot.managedFileSha256 ?? 'файл не прикреплён'}
                  </span>
                </div>
              )}
            </>
          )}
        </section>
      </div>
      <section className="reliability-dataset" aria-labelledby="dataset-title">
        <Title id="dataset-title" order={3}>
          Версия выборки
        </Title>
        <div className="reliability-form-grid">
          <Select
            label="Метод выборки"
            required
            disabled={disabled || busy !== null}
            data={[
              { value: 'rbd', label: 'РБД' },
              { value: 'rpt', label: 'РПТ' },
            ]}
            value={datasetMethod}
            onChange={(value) => {
              if (value === null) return;
              setDatasetMethod(value);
              setDatasetDirty(true);
            }}
          />
          <Select
            label="Показатель выборки"
            required
            disabled={disabled || busy !== null}
            data={[
              { value: 'rbd_steady_rotation_time', label: 'Время установившегося вращения' },
              { value: 'rpt_start_stop_cycles', label: 'Циклы «пуск–торможение»' },
            ]}
            value={datasetMetricKind}
            onChange={(value) => {
              if (value === null) return;
              setDatasetMetricKind(value);
              setDatasetDirty(true);
            }}
          />
          <Select
            label="Единица выборки"
            required
            disabled={disabled || busy !== null}
            data={[
              { value: 'hours', label: 'часы' },
              { value: 'count', label: 'циклы, целое число' },
            ]}
            value={datasetMetricUnit}
            onChange={(value) => {
              if (value === null) return;
              setDatasetMetricUnit(value);
              setDatasetDirty(true);
            }}
          />
          <TextInput
            label="Название"
            required
            disabled={disabled || busy !== null}
            value={datasetTitle}
            maxLength={200}
            onChange={(event) => {
              setDatasetTitle(event.currentTarget.value);
              setDatasetDirty(true);
            }}
          />
          <Textarea
            label="Граница совокупности"
            required
            disabled={disabled || busy !== null}
            value={populationBasis}
            maxLength={2000}
            onChange={(event) => {
              setPopulationBasis(event.currentTarget.value);
              setDatasetDirty(true);
            }}
          />
          <Textarea
            label="Применимая методика"
            required
            disabled={disabled || busy !== null}
            value={methodologyBasis}
            maxLength={2000}
            onChange={(event) => {
              setMethodologyBasis(event.currentTarget.value);
              setDatasetDirty(true);
            }}
          />
          <Textarea
            label="Почему условия сопоставимы"
            required
            disabled={disabled || busy !== null}
            value={comparabilityBasis}
            maxLength={2000}
            onChange={(event) => {
              setComparabilityBasis(event.currentTarget.value);
              setDatasetDirty(true);
            }}
          />
        </div>
        <div className="reliability-candidates">
          {executions
            .filter((item) => item.currentObservationVersionId !== null)
            .map((item) => {
              const decision = candidateDecisions[item.executionId];
              if (decision === undefined) return null;
              return (
                <div key={item.executionId} className="reliability-candidate">
                  <div>
                    <Select
                      label={`${methodLabel(item.method)} · ${item.sourceRunId}`}
                      data={[
                        { value: 'pending', label: 'Решение не принято' },
                        { value: 'included', label: 'Включить' },
                        { value: 'excluded', label: 'Исключить' },
                      ]}
                      value={decision.decision}
                      required
                      disabled={disabled || busy !== null}
                      onChange={(value) => {
                        if (value === null) return;
                        setCandidateDecisions((current) => ({
                          ...current,
                          [item.executionId]: {
                            ...decision,
                            decision: value,
                            reason: '',
                          },
                        }));
                        setDatasetDirty(true);
                      }}
                    />
                    <Text size="xs" c="dimmed">
                      {item.packageKind === 'diagnostic_partial'
                        ? 'Политика life_metric_exact_v1: diagnostic_partial не включается.'
                        : item.method === 'pmn'
                          ? 'Политика life_metric_exact_v1: ПМН не относится к выборке наработки.'
                          : `Классификация: ${classificationLabel(item.currentClassification)}. Endpoint и metric проверит Python при фиксации.`}
                    </Text>
                  </div>
                  <TextInput
                    label={
                      decision.decision === 'included'
                        ? 'Причина включения'
                        : decision.decision === 'excluded'
                          ? 'Причина исключения'
                          : 'Почему решение пока не принято'
                    }
                    value={decision.reason}
                    required={decision.decision !== 'pending'}
                    disabled={disabled || busy !== null || decision.decision === 'pending'}
                    maxLength={2000}
                    onChange={(event) => {
                      const reason = event.currentTarget.value;
                      setCandidateDecisions((current) => ({
                        ...current,
                        [item.executionId]: { ...decision, reason },
                      }));
                      setDatasetDirty(true);
                    }}
                  />
                </div>
              );
            })}
        </div>
        <Textarea
          label="Основание версии выборки"
          required
          disabled={disabled || busy !== null}
          value={datasetReason}
          maxLength={2000}
          onChange={(event) => {
            setDatasetReason(event.currentTarget.value);
            setDatasetDirty(true);
          }}
        />
        <Group className="form-actions">
          <Text size="sm">
            {Object.values(candidateDecisions).some((item) => item.decision === 'pending')
              ? 'Для каждого загруженного кандидата выберите включение или исключение и укажите причину.'
              : executionCursor !== null
                ? 'Есть следующая страница исполнений; сохранённая версия охватит только явно загруженные и рассмотренные кандидаты.'
                : 'Все загруженные кандидаты имеют явное решение.'}
          </Text>
          <Button
            loading={busy === 'dataset-save'}
            disabled={
              disabled ||
              busy !== null ||
              !datasetDirty ||
              Object.keys(candidateDecisions).length === 0 ||
              datasetMethod === null ||
              datasetMetricKind === null ||
              datasetMetricUnit === null ||
              Object.values(candidateDecisions).some((item) => item.decision === 'pending') ||
              Object.values(candidateDecisions).some((item) => item.reason.trim() === '')
            }
            onClick={() => void saveDataset()}
          >
            Зафиксировать новую версию выборки
          </Button>
        </Group>
        <div className="reliability-version-strip" aria-label="Сохранённые выборки">
          {datasets.map((item) => (
            <button
              key={item.datasetVersionId}
              type="button"
              aria-pressed={selectedDataset?.datasetVersionId === item.datasetVersionId}
              disabled={dirty || busy !== null}
              onClick={() => {
                setSelectedDataset(item);
                setDatasetId(item.datasetId);
                setDatasetVersionId(crypto.randomUUID());
                setDatasetTitle(item.title);
                setDatasetMethod(item.method);
                setDatasetMetricKind(item.metricKind);
                setDatasetMetricUnit(item.metricUnit);
                setPopulationBasis(item.populationBasis);
                setMethodologyBasis(item.methodologyBasis);
                setComparabilityBasis(item.comparabilityBasis);
                setDatasetReason(item.decisionReason);
                const persisted = Object.fromEntries(
                  item.members.map((member) => [
                    member.executionId,
                    {
                      observationVersionId: member.observationVersionId,
                      decision: member.decision,
                      reason: member.inclusionReason,
                    },
                  ]),
                );
                setCandidateDecisions({
                  ...Object.fromEntries(
                    executions.flatMap((entry) =>
                      entry.currentObservationVersionId === null
                        ? []
                        : [
                            [
                              entry.executionId,
                              {
                                observationVersionId: entry.currentObservationVersionId,
                                decision: 'pending' as const,
                                reason: '',
                              },
                            ],
                          ],
                    ),
                  ),
                  ...persisted,
                });
                setDatasetDirty(false);
              }}
            >
              Версия {String(item.versionNumber)} · включено{' '}
              {String(item.members.filter((member) => member.decision === 'included').length)} /
              исключено{' '}
              {String(item.members.filter((member) => member.decision === 'excluded').length)}
            </button>
          ))}
        </div>
        {datasetHistoryCursor === null ? null : (
          <Button
            variant="subtle"
            disabled={dirty || busy !== null}
            onClick={() =>
              void runPending('dataset-history', async () => {
                const older: ReliabilityDatasetVersion[] = [];
                let versionId: string | null = datasetHistoryCursor;
                while (versionId !== null && older.length < 50) {
                  const detail = await desktopApi.reliabilityDataset.getVersion(versionId);
                  if (!detail.ok) return setError(detail.error);
                  older.push(detail.result);
                  versionId = detail.result.previousVersionId;
                }
                setDatasets((current) => [...current, ...older]);
              })
            }
          >
            Показать предыдущие версии выборки
          </Button>
        )}
        {selectedDataset === null ? null : (
          <div className="reliability-dataset-readback">
            <strong>{selectedDataset.title}</strong>
            <span>Политика: {selectedDataset.policyId}</span>
            <span>{selectedDataset.methodologyBasis}</span>
            {selectedDataset.members.map((member) => (
              <span key={member.observationVersionId}>
                {member.decision === 'included' ? 'Включено' : 'Исключено'} · {member.policyReason}{' '}
                · {member.inclusionReason} · observation {member.observationVersionId} · execution{' '}
                {member.executionId} · specimen {member.localSpecimenId} · source run{' '}
                {member.sourceRunId}
              </span>
            ))}
          </div>
        )}
      </section>
    </section>
  );
});

function versionToDraft(version: ReliabilityObservationVersion): ObservationDraft {
  return {
    classification: version.classification,
    endpointKind: version.endpointKind,
    metricKind: version.metricKind,
    metricUnit: version.metricUnit,
    lowerValue: version.lowerValue ?? '',
    upperValue: version.upperValue ?? '',
    originBasis: version.originBasis,
    endpointBasis: version.endpointBasis,
    documentId: version.documentSnapshot.documentId,
    documentLocator: version.documentLocator,
    reason: version.decisionReason,
    failureIds: version.failureIds,
  };
}

function observationReady(draft: ObservationDraft): boolean {
  if (
    draft.originBasis.trim() === '' ||
    draft.endpointBasis.trim() === '' ||
    draft.documentId === null ||
    draft.documentLocator.trim() === '' ||
    draft.reason.trim() === ''
  )
    return false;
  return true;
}

function methodLabel(method: 'rbd' | 'rpt' | 'pmn'): string {
  return method === 'rbd' ? 'РБД' : method === 'rpt' ? 'РПТ' : 'ПМН';
}

function classificationLabel(value: ReliabilityExecutionSummary['currentClassification']): string {
  if (value === null) return 'Интерпретация не сохранена';
  return value === 'failure'
    ? 'Подтверждённый отказ'
    : value === 'right_censored'
      ? 'Правое цензурирование'
      : value === 'withdrawn'
        ? 'Снято с наблюдения'
        : 'Недействительно для назначения';
}

function sourceValue(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : 'не указано';
}

function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Ядро недоступно. Черновик сохранён в форме.',
    details: {},
    retryable: true,
  };
}
