import { Button, Group, Select, Text, Textarea, TextInput, Title } from '@mantine/core';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';

import type {
  CaseDocumentSummary,
  DesktopError,
  ImpellerApi,
  RbdCalculationDetail,
  RbdCalculationPage,
  RbdPlanSource,
  ReliabilityExecutionSummary,
  WheelModelSummary,
} from '@impeller-reliability/contracts';

import {
  buildCalculationCommand,
  emptyCalculationDraft,
  RBD_FIELD_LABELS,
  RBD_INPUT_FIELDS,
  sourceValueForField,
  type RbdCalculationDraft,
  type RbdInputField,
} from './rbd-calculation-draft';

interface RbdCalculationProps {
  readonly desktopApi: ImpellerApi;
  readonly disabled: boolean;
  readonly onDirtyChange: (dirty: boolean) => void;
  readonly onPendingChange: (pending: boolean) => void;
}

export interface RbdCalculationHandle {
  discardDraft(): void;
  waitForPendingSave(): Promise<void>;
  verifyAfterReattach(): Promise<boolean>;
}

interface SnapshotIds {
  readonly analysisInputSnapshotId: string;
  readonly calculationSnapshotId: string;
}

const newSnapshotIds = (): SnapshotIds => ({
  analysisInputSnapshotId: crypto.randomUUID(),
  calculationSnapshotId: crypto.randomUUID(),
});

const failureOptions = [
  { value: 'unavailable', label: 'Не рассчитывать: точное время до отказа не задано' },
  { value: 'exact_supported', label: 'Точный документированный отказ' },
  { value: 'interval_endpoint', label: 'Отказ в интервале: точка неизвестна' },
  { value: 'right_censored', label: 'Правое цензурирование: отказ не установлен' },
  { value: 'unsupported_phase', label: 'Отказ в неподдерживаемой фазе' },
  { value: 'ambiguous_pauses', label: 'Паузы или повторные разгоны неоднозначны' },
];

export const RbdCalculation = forwardRef<RbdCalculationHandle, RbdCalculationProps>(
  function RbdCalculation(
    { desktopApi, disabled, onDirtyChange, onPendingChange },
    ref,
  ): React.JSX.Element {
    const [wheels, setWheels] = useState<readonly WheelModelSummary[]>([]);
    const [wheelModelId, setWheelModelId] = useState<string | null>(null);
    const [executions, setExecutions] = useState<readonly ReliabilityExecutionSummary[]>([]);
    const [executionCursor, setExecutionCursor] = useState<string | null>(null);
    const [executionPagePending, setExecutionPagePending] = useState(false);
    const [executionId, setExecutionId] = useState<string | null>(null);
    const [planSelection, setPlanSelection] = useState<'original' | 'effective'>('original');
    const [source, setSource] = useState<RbdPlanSource | null>(null);
    const [documents, setDocuments] = useState<readonly CaseDocumentSummary[]>([]);
    const [draft, setDraft] = useState<RbdCalculationDraft>(emptyCalculationDraft);
    const [snapshotIds, setSnapshotIds] = useState<SnapshotIds>(newSnapshotIds);
    const [dirty, setDirty] = useState(false);
    const [unresolvedSave, setUnresolvedSave] = useState(false);
    const [detail, setDetail] = useState<RbdCalculationDetail | null>(null);
    const [history, setHistory] = useState<RbdCalculationPage | null>(null);
    const [historyLoadError, setHistoryLoadError] = useState<DesktopError | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const selectionRef = useRef(0);
    const wheelRevisionRef = useRef(0);
    const detailRequestRef = useRef(0);
    const historyRequestRef = useRef(0);
    const executionPagePendingRef = useRef(false);
    const draftRevisionRef = useRef(0);
    const pendingRef = useRef<Promise<void> | null>(null);
    const unresolvedSaveRef = useRef(false);
    const submittedIdsRef = useRef<SnapshotIds | null>(null);
    const activeWheelRef = useRef<string | null>(null);

    useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

    const loadWheel = useCallback(
      async (selectedWheelId: string): Promise<void> => {
        const revision = ++selectionRef.current;
        const wheelRevision = ++wheelRevisionRef.current;
        detailRequestRef.current += 1;
        historyRequestRef.current += 1;
        activeWheelRef.current = selectedWheelId;
        setWheelModelId(selectedWheelId);
        setExecutions([]);
        setExecutionCursor(null);
        setExecutionPagePending(false);
        executionPagePendingRef.current = false;
        setDocuments([]);
        setExecutionId(null);
        setSource(null);
        setDetail(null);
        setHistory(null);
        setHistoryLoadError(null);
        setDraft(emptyCalculationDraft());
        setSnapshotIds(newSnapshotIds());
        setDirty(false);
        setUnresolvedSave(false);
        unresolvedSaveRef.current = false;
        submittedIdsRef.current = null;
        setError(null);
        setMessage(null);
        setBusy('wheel');
        try {
          const executionResult = await desktopApi.reliabilityExecution.listPage(
            selectedWheelId,
            null,
            25,
          );
          if (revision !== selectionRef.current) return;
          if (!executionResult.ok) return setError(executionResult.error);
          setExecutions(executionResult.result.items.filter((item) => item.method === 'rbd'));
          setExecutionCursor(executionResult.result.nextCursor);
          try {
            const historyResult = await desktopApi.rbdCalculation.listPage(
              selectedWheelId,
              null,
              25,
            );
            if (revision !== selectionRef.current) return;
            if (historyResult.ok) {
              setHistory(historyResult.result);
              setHistoryLoadError(null);
            } else setHistoryLoadError(historyResult.error);
          } catch {
            if (revision === selectionRef.current) setHistoryLoadError(unavailableError());
          }
          void desktopApi.caseDocument
            .list({ includeArchived: false, documentKind: null })
            .then((documentResult) => {
              if (wheelRevision !== wheelRevisionRef.current) return;
              if (documentResult.ok) setDocuments(documentResult.result);
              else
                setError({
                  ...documentResult.error,
                  message:
                    'Документы дела недоступны; расчёт по исходным значениям остаётся доступным.',
                });
            })
            .catch(() => {
              if (wheelRevision === wheelRevisionRef.current)
                setError({
                  code: 'worker_unavailable',
                  message:
                    'Документы дела недоступны; расчёт по исходным значениям остаётся доступным.',
                  details: {},
                  retryable: true,
                });
            });
        } catch {
          if (revision === selectionRef.current) setError(unavailableError());
        } finally {
          if (revision === selectionRef.current) setBusy(null);
        }
      },
      [desktopApi],
    );

    useEffect(() => {
      let active = true;
      void desktopApi.wheelModel
        .list(false)
        .then((result) => {
          if (!active) return;
          if (result.ok) setWheels(result.result);
          else setError(result.error);
        })
        .catch(() => {
          if (active) setError(unavailableError());
        });
      return () => {
        active = false;
        selectionRef.current += 1;
        wheelRevisionRef.current += 1;
      };
    }, [desktopApi]);

    const selectSource = async (
      selectedExecutionId: string,
      selectedPlan: 'original' | 'effective',
    ): Promise<void> => {
      const revision = ++selectionRef.current;
      executionPagePendingRef.current = false;
      setExecutionPagePending(false);
      detailRequestRef.current += 1;
      setExecutionId(selectedExecutionId);
      setPlanSelection(selectedPlan);
      setSource(null);
      setDetail(null);
      setDraft(emptyCalculationDraft());
      setSnapshotIds(newSnapshotIds());
      setDirty(false);
      setError(null);
      setMessage(null);
      setBusy('source');
      try {
        const result = await desktopApi.rbdCalculation.getSourceInputs(
          selectedExecutionId,
          selectedPlan,
        );
        if (revision !== selectionRef.current) return;
        if (!result.ok) return setError(result.error);
        if (
          result.result.executionId !== selectedExecutionId ||
          result.result.planSelection !== selectedPlan
        )
          return setError(contractError());
        setSource(result.result);
      } catch {
        if (revision === selectionRef.current) setError(unavailableError());
      } finally {
        if (revision === selectionRef.current) setBusy(null);
      }
    };

    const editDraft = (next: RbdCalculationDraft): void => {
      draftRevisionRef.current += 1;
      setDraft(next);
      setDirty(true);
      setError(null);
      setMessage(null);
      if (!unresolvedSaveRef.current) setSnapshotIds(newSnapshotIds());
    };

    const editField = (
      field: RbdInputField,
      next: RbdCalculationDraft['fields'][RbdInputField],
    ): void => editDraft({ ...draft, fields: { ...draft.fields, [field]: next } });

    const refreshHistory = async (selectedWheelId: string): Promise<boolean> => {
      const request = ++historyRequestRef.current;
      const result = await desktopApi.rbdCalculation.listPage(selectedWheelId, null, 25);
      if (request !== historyRequestRef.current || activeWheelRef.current !== selectedWheelId)
        return false;
      if (!result.ok) {
        setHistoryLoadError(result.error);
        setError({
          ...result.error,
          message: `Историю не удалось обновить: ${result.error.message}`,
        });
        return false;
      }
      setHistory(result.result);
      setHistoryLoadError(null);
      return true;
    };

    const save = (): void => {
      if (source === null || busy !== null || disabled) return;
      const built = buildCalculationCommand(source, draft, documents, snapshotIds);
      if (built.command === null) {
        setError({
          code: 'validation_error',
          message: built.error ?? 'Проверьте входы.',
          details: {},
          retryable: false,
        });
        return;
      }
      const command = built.command;
      const selectedWheelId = wheelModelId;
      const selectedExecutionId = source.executionId;
      const revision = selectionRef.current;
      const draftRevision = draftRevisionRef.current;
      const submittedIds = snapshotIds;
      submittedIdsRef.current = submittedIds;
      setBusy('save');
      onPendingChange(true);
      setError(null);
      const pending = (async (): Promise<void> => {
        try {
          const result = await desktopApi.rbdCalculation.create(command);
          if (revision !== selectionRef.current || executionId !== selectedExecutionId) return;
          if (!result.ok) {
            setError(result.error);
            if (result.error.code === 'timeout' || result.error.code === 'worker_unavailable') {
              unresolvedSaveRef.current = true;
              setUnresolvedSave(true);
            }
            return;
          }
          if (
            result.result.detail.inputSnapshot.analysisInputSnapshotId !==
              submittedIds.analysisInputSnapshotId ||
            result.result.detail.calculationSnapshot.calculationSnapshotId !==
              submittedIds.calculationSnapshotId
          ) {
            setError(contractError());
            return;
          }
          unresolvedSaveRef.current = false;
          setUnresolvedSave(false);
          setDetail(result.result.detail);
          detailRequestRef.current += 1;
          setMessage('Расчёт РБД сохранён. Входы и результат зафиксированы неизменяемой парой.');
          if (draftRevisionRef.current === draftRevision) {
            setDirty(false);
            setSnapshotIds(newSnapshotIds());
          }
          if (selectedWheelId !== null) {
            try {
              await refreshHistory(selectedWheelId);
            } catch {
              const historyError: DesktopError = {
                code: 'worker_unavailable',
                message: 'Расчёт сохранён, но историю пока не удалось обновить.',
                details: {},
                retryable: true,
              };
              setHistoryLoadError(historyError);
              setError(historyError);
            }
          }
        } catch {
          if (revision === selectionRef.current) {
            unresolvedSaveRef.current = true;
            setUnresolvedSave(true);
            setError(unavailableError());
          }
        } finally {
          if (revision === selectionRef.current) setBusy(null);
          onPendingChange(false);
        }
      })();
      pendingRef.current = pending;
      void pending.finally(() => {
        if (pendingRef.current === pending) pendingRef.current = null;
      });
    };

    useImperativeHandle(
      ref,
      () => ({
        discardDraft: () => {
          draftRevisionRef.current += 1;
          setDraft(emptyCalculationDraft());
          setSnapshotIds(newSnapshotIds());
          setDirty(false);
          setUnresolvedSave(false);
          unresolvedSaveRef.current = false;
          submittedIdsRef.current = null;
        },
        waitForPendingSave: async () => {
          await pendingRef.current;
        },
        verifyAfterReattach: async () => {
          const selectedWheelId = activeWheelRef.current;
          if (selectedWheelId === null) return true;
          try {
            const historyResult = await desktopApi.rbdCalculation.listPage(
              selectedWheelId,
              null,
              25,
            );
            if (!historyResult.ok) {
              setError(historyResult.error);
              setHistoryLoadError(historyResult.error);
              return false;
            }
            setHistory(historyResult.result);
            setHistoryLoadError(null);
            const submittedIds = submittedIdsRef.current;
            if (unresolvedSaveRef.current && submittedIds !== null) {
              const reconciled = await desktopApi.rbdCalculation.getDetail(
                submittedIds.calculationSnapshotId,
              );
              if (!reconciled.ok) {
                if (reconciled.error.code === 'entity_not_found') {
                  unresolvedSaveRef.current = false;
                  setUnresolvedSave(false);
                  setError(null);
                  return true;
                }
                setError(reconciled.error);
                return false;
              }
              if (
                reconciled.result.inputSnapshot.analysisInputSnapshotId !==
                  submittedIds.analysisInputSnapshotId ||
                reconciled.result.calculationSnapshot.calculationSnapshotId !==
                  submittedIds.calculationSnapshotId
              ) {
                setError(contractError());
                return false;
              }
              setDetail(reconciled.result);
              setDirty(false);
              setSnapshotIds(newSnapshotIds());
              setUnresolvedSave(false);
              unresolvedSaveRef.current = false;
              setError(null);
              setMessage('Сохранённый расчёт подтверждён после повторного подключения.');
            }
            return true;
          } catch {
            setError(unavailableError());
            return false;
          }
        },
      }),
      [desktopApi],
    );

    const locked = disabled || busy !== null || unresolvedSave;
    const documentOptions = documents.map((item) => ({
      value: item.caseDocumentId,
      label: `${item.title} · редакция записи ${String(item.recordRevision)}`,
    }));

    return (
      <section className="rbd-calculation" aria-labelledby="rbd-calculation-title">
        <div className="section-heading">
          <div>
            <Text className="eyebrow">Производный анализ</Text>
            <Title id="rbd-calculation-title" order={2}>
              Расчёт РБД
            </Title>
            <Text size="sm" c="dimmed">
              Требуемые параметры одного исполнения по ПМИ Р130У. Это не статистическая оценка
              ресурса и не заключение о соответствии образца.
            </Text>
          </div>
        </div>
        {error !== null ? (
          <div className="feedback feedback--error" role="alert">
            {error.message}
          </div>
        ) : null}
        {message !== null ? (
          <div className="feedback feedback--success" role="status">
            {message}
          </div>
        ) : null}
        {unresolvedSave ? (
          <div className="feedback feedback--warning" role="alert">
            Ответ на сохранение не подтверждён. Черновик и идентификаторы операции сохранены. Повтор
            безопасно вернёт уже созданную пару либо создаст её один раз.
            <Button size="compact-sm" disabled={disabled || busy !== null} onClick={save}>
              Повторить сохранение с теми же ID
            </Button>
          </div>
        ) : null}
        <div className="rbd-calculation-layout">
          <section aria-labelledby="rbd-source-heading">
            <Title order={3} id="rbd-source-heading">
              Исполнение и источник
            </Title>
            <Select
              label="Модель рабочего колеса"
              data={wheels.map((item) => ({ value: item.wheelModelId, label: item.fullName }))}
              value={wheelModelId}
              disabled={locked || dirty}
              onChange={(value) => {
                if (value !== null) void loadWheel(value);
              }}
              searchable
            />
            {wheelModelId !== null ? (
              <div className="rbd-execution-list" aria-label="Исполнения РБД">
                {executions.length === 0 ? (
                  <Text size="sm">Подготовленных исполнений РБД нет.</Text>
                ) : null}
                {executions.map((item) => (
                  <button
                    key={item.executionId}
                    type="button"
                    className="reliability-record"
                    aria-pressed={executionId === item.executionId}
                    disabled={locked || dirty}
                    onClick={() => void selectSource(item.executionId, planSelection)}
                  >
                    <strong>РБД · {item.sourceRunId}</strong>
                    <span>
                      Редакция экспорта {item.exportRevision} ·{' '}
                      {item.packageKind === 'diagnostic_partial'
                        ? 'частичный архив'
                        : 'финальный архив'}
                    </span>
                  </button>
                ))}
                {executionCursor !== null ? (
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    disabled={locked || dirty || executionPagePending}
                    onClick={() => {
                      const selectedWheelId = wheelModelId;
                      if (selectedWheelId === null || executionPagePendingRef.current) return;
                      const currentRevision = selectionRef.current;
                      executionPagePendingRef.current = true;
                      setExecutionPagePending(true);
                      void desktopApi.reliabilityExecution
                        .listPage(selectedWheelId, executionCursor, 25)
                        .then((result) => {
                          if (
                            activeWheelRef.current !== selectedWheelId ||
                            currentRevision !== selectionRef.current
                          )
                            return;
                          if (!result.ok) return setError(result.error);
                          setExecutions((previous) => [
                            ...previous,
                            ...result.result.items.filter((item) => item.method === 'rbd'),
                          ]);
                          setExecutionCursor(result.result.nextCursor);
                        })
                        .catch(() => {
                          if (currentRevision === selectionRef.current)
                            setError(unavailableError());
                        })
                        .finally(() => {
                          if (currentRevision === selectionRef.current) {
                            executionPagePendingRef.current = false;
                            setExecutionPagePending(false);
                          }
                        });
                    }}
                  >
                    Показать ещё исполнения
                  </Button>
                ) : null}
              </div>
            ) : null}
            {executionId !== null ? (
              <Select
                label="Редакция плана в выбранном архиве"
                data={[
                  { value: 'original', label: 'Исходный план' },
                  { value: 'effective', label: 'Эффективный план' },
                ]}
                value={planSelection}
                disabled={locked || dirty}
                onChange={(value) => {
                  if (value === 'original' || value === 'effective')
                    void selectSource(executionId, value);
                }}
              />
            ) : null}
            {source !== null ? (
              <dl className="reliability-facts">
                <div>
                  <dt>Архив R130SH</dt>
                  <dd>
                    {source.packageId} · revision {source.exportRevision}
                  </dd>
                </div>
                <div>
                  <dt>План</dt>
                  <dd>
                    {source.planId} · revision {source.planRevision}
                  </dd>
                </div>
                <div>
                  <dt>Поле архива</dt>
                  <dd>
                    <code>{source.payloadPath}</code>
                  </dd>
                </div>
                <div>
                  <dt>SHA-256 плана</dt>
                  <dd>
                    <code>{source.payloadSha256}</code>
                  </dd>
                </div>
              </dl>
            ) : null}
            {source !== null ? (
              <div className="rbd-producer-targets">
                <Text fw={650}>Уставка в источнике R130SH</Text>
                <Text size="sm">
                  {source.executionTargets.targetCycles} циклов ·{' '}
                  {source.executionTargets.targetSteadyDurationS} с установившегося вращения ·{' '}
                  {source.executionTargets.totalDurationS} с всего
                </Text>
                <Text size="xs" c="dimmed">
                  Уставка округлена производителем и не подменяет точное расчётное требование.
                </Text>
              </div>
            ) : null}
          </section>
          <section aria-labelledby="rbd-input-heading">
            <Title order={3} id="rbd-input-heading">
              Явные расчётные входы
            </Title>
            {source === null ? (
              <Text size="sm">Выберите исполнение РБД и редакцию плана.</Text>
            ) : null}
            {source !== null ? (
              <>
                {RBD_INPUT_FIELDS.map((field) => {
                  const choice = draft.fields[field];
                  const sourceValue = sourceValueForField(source, field);
                  return (
                    <fieldset key={field} className="rbd-field">
                      <legend>
                        {RBD_FIELD_LABELS[field].label} · {RBD_FIELD_LABELS[field].unit}
                      </legend>
                      <Text size="sm">
                        Значение в {source.payloadPath}:{' '}
                        <strong>{sourceValue ?? 'отсутствует'}</strong>
                      </Text>
                      <Select
                        label="Происхождение значения"
                        data={[
                          {
                            value: 'source',
                            label: 'Значение выбранного плана R130SH',
                            disabled: sourceValue === null,
                          },
                          { value: 'manual', label: 'Документированное дополнение инженера' },
                        ]}
                        value={choice.origin}
                        disabled={locked}
                        onChange={(value) => {
                          if (value === 'source' || value === 'manual')
                            editField(field, {
                              ...choice,
                              origin: value,
                              manualValue: '',
                              basis: '',
                              documentId: null,
                              documentLocator: '',
                            });
                        }}
                      />
                      {choice.origin === 'manual' ? (
                        <div className="rbd-manual-fields">
                          <TextInput
                            label="Значение дополнения"
                            value={choice.manualValue}
                            disabled={locked}
                            onChange={(event) =>
                              editField(field, {
                                ...choice,
                                manualValue: event.currentTarget.value,
                              })
                            }
                          />
                          <Textarea
                            label="Основание замещения"
                            value={choice.basis}
                            disabled={locked}
                            autosize
                            minRows={2}
                            onChange={(event) =>
                              editField(field, { ...choice, basis: event.currentTarget.value })
                            }
                          />
                          <Select
                            label="Документ дела (если использован)"
                            data={documentOptions}
                            value={choice.documentId}
                            clearable
                            disabled={locked}
                            onChange={(value) => editField(field, { ...choice, documentId: value })}
                          />
                          {choice.documentId !== null ? (
                            <TextInput
                              label="Раздел или поле документа"
                              value={choice.documentLocator}
                              disabled={locked}
                              onChange={(event) =>
                                editField(field, {
                                  ...choice,
                                  documentLocator: event.currentTarget.value,
                                })
                              }
                            />
                          ) : null}
                        </div>
                      ) : null}
                    </fieldset>
                  );
                })}
                <fieldset className="rbd-field">
                  <legend>Документированное фактическое значение до отказа</legend>
                  <Select
                    label="Применимость формулы таблицы 3"
                    data={failureOptions}
                    value={draft.failure.applicability}
                    disabled={locked}
                    onChange={(value) => {
                      if (failureOptions.some((item) => item.value === value))
                        editDraft({
                          ...draft,
                          failure: {
                            applicability:
                              value === 'exact_supported'
                                ? 'exact_supported'
                                : value === 'interval_endpoint'
                                  ? 'interval_endpoint'
                                  : value === 'right_censored'
                                    ? 'right_censored'
                                    : value === 'unsupported_phase'
                                      ? 'unsupported_phase'
                                      : value === 'ambiguous_pauses'
                                        ? 'ambiguous_pauses'
                                        : 'unavailable',
                            durationToFailureS: '',
                            basis: '',
                            documentId: null,
                            documentLocator: '',
                          },
                        });
                    }}
                  />
                  {draft.failure.applicability !== 'unavailable' ? (
                    <div className="rbd-manual-fields">
                      {draft.failure.applicability === 'exact_supported' ? (
                        <TextInput
                          label="T_OTK, с — от начала испытания до отказа"
                          value={draft.failure.durationToFailureS}
                          disabled={locked}
                          onChange={(event) =>
                            editDraft({
                              ...draft,
                              failure: {
                                ...draft.failure,
                                durationToFailureS: event.currentTarget.value,
                              },
                            })
                          }
                        />
                      ) : null}
                      <Textarea
                        label="Основание применимости или неприменимости"
                        value={draft.failure.basis}
                        disabled={locked}
                        autosize
                        minRows={2}
                        onChange={(event) =>
                          editDraft({
                            ...draft,
                            failure: { ...draft.failure, basis: event.currentTarget.value },
                          })
                        }
                      />
                      {draft.failure.applicability === 'exact_supported' ? (
                        <>
                          <Select
                            label="Документ с временем и началом отсчёта"
                            data={documentOptions}
                            value={draft.failure.documentId}
                            disabled={locked}
                            searchable
                            onChange={(value) =>
                              editDraft({
                                ...draft,
                                failure: { ...draft.failure, documentId: value },
                              })
                            }
                          />
                          <TextInput
                            label="Раздел или поле документа"
                            value={draft.failure.documentLocator}
                            disabled={locked}
                            onChange={(event) =>
                              editDraft({
                                ...draft,
                                failure: {
                                  ...draft.failure,
                                  documentLocator: event.currentTarget.value,
                                },
                              })
                            }
                          />
                        </>
                      ) : null}
                    </div>
                  ) : (
                    <Text size="sm" c="dimmed">
                      Обязательная часть расчёта доступна без T_OTK; число циклов до отказа
                      останется неприменимым.
                    </Text>
                  )}
                </fieldset>
                <Textarea
                  label="Основание создания расчётного снимка"
                  placeholder="Почему выбраны именно эта редакция и эти входы"
                  value={draft.reason}
                  disabled={locked}
                  autosize
                  minRows={2}
                  onChange={(event) => editDraft({ ...draft, reason: event.currentTarget.value })}
                />
                <Group className="form-actions">
                  <Button disabled={locked || !dirty} loading={busy === 'save'} onClick={save}>
                    Рассчитать и зафиксировать
                  </Button>
                  <Button
                    variant="subtle"
                    disabled={locked || !dirty}
                    onClick={() => {
                      setDraft(emptyCalculationDraft());
                      setSnapshotIds(newSnapshotIds());
                      setDirty(false);
                    }}
                  >
                    Сбросить черновик
                  </Button>
                </Group>
              </>
            ) : null}
          </section>
        </div>
        <section className="rbd-results" aria-labelledby="rbd-result-heading">
          <Title order={3} id="rbd-result-heading">
            Сохранённый расчёт
          </Title>
          {detail === null ? (
            <Text size="sm">
              После сохранения здесь появятся точные требования, формулы и происхождение.
            </Text>
          ) : (
            <RbdResultDetail detail={detail} />
          )}
        </section>
        {wheelModelId !== null ? (
          <section className="rbd-results" aria-labelledby="rbd-history-heading">
            <Title order={3} id="rbd-history-heading">
              История расчётов модели
            </Title>
            {historyLoadError !== null ? (
              <div className="feedback feedback--warning" role="alert">
                Историю расчётов не удалось прочитать: {historyLoadError.message} Новый расчёт по
                исходным значениям остаётся доступным.
                <Button
                  size="compact-sm"
                  variant="subtle"
                  disabled={locked}
                  onClick={() => {
                    const selectedWheelId = wheelModelId;
                    setBusy('history');
                    void refreshHistory(selectedWheelId)
                      .then((reconciled) => {
                        if (reconciled) setError(null);
                      })
                      .catch(() => setHistoryLoadError(unavailableError()))
                      .finally(() => setBusy(null));
                  }}
                >
                  Повторить чтение истории
                </Button>
              </div>
            ) : null}
            {history?.items.length === 0 ? <Text size="sm">Сохранённых расчётов нет.</Text> : null}
            <div className="rbd-history-list">
              {history?.items.map((item) => (
                <button
                  key={item.calculationSnapshotId}
                  type="button"
                  className="reliability-record"
                  aria-pressed={
                    detail?.calculationSnapshot.calculationSnapshotId === item.calculationSnapshotId
                  }
                  disabled={locked}
                  onClick={() => {
                    const currentRevision = selectionRef.current;
                    const detailRequest = ++detailRequestRef.current;
                    void desktopApi.rbdCalculation
                      .getDetail(item.calculationSnapshotId)
                      .then((result) => {
                        if (
                          currentRevision !== selectionRef.current ||
                          detailRequest !== detailRequestRef.current
                        )
                          return;
                        if (result.ok) setDetail(result.result);
                        else setError(result.error);
                      })
                      .catch(() => {
                        if (
                          currentRevision === selectionRef.current &&
                          detailRequest === detailRequestRef.current
                        )
                          setError(unavailableError());
                      });
                  }}
                >
                  <strong>
                    {item.requiredCycles} циклов ·{' '}
                    {item.planSelection === 'original' ? 'исходный' : 'эффективный'} план
                  </strong>
                  <span>
                    {new Date(item.createdAtUtc).toLocaleString('ru-RU')} · таблица 3:{' '}
                    {item.failureStatus === 'calculated' ? 'рассчитана' : 'не рассчитана'}
                  </span>
                </button>
              ))}
            </div>
            {history?.nextCursor !== null && history !== null ? (
              <Button
                variant="subtle"
                size="compact-sm"
                disabled={locked}
                onClick={() => {
                  const selectedWheelId = wheelModelId;
                  const cursor = history.nextCursor;
                  if (selectedWheelId === null || cursor === null) return;
                  const currentRevision = selectionRef.current;
                  const historyRequest = ++historyRequestRef.current;
                  void desktopApi.rbdCalculation
                    .listPage(selectedWheelId, cursor, 25)
                    .then((result) => {
                      if (
                        activeWheelRef.current !== selectedWheelId ||
                        currentRevision !== selectionRef.current ||
                        historyRequest !== historyRequestRef.current
                      )
                        return;
                      if (result.ok)
                        setHistory({
                          items: [...history.items, ...result.result.items],
                          nextCursor: result.result.nextCursor,
                        });
                      else setError(result.error);
                    })
                    .catch(() => {
                      if (
                        currentRevision === selectionRef.current &&
                        historyRequest === historyRequestRef.current
                      )
                        setError(unavailableError());
                    });
                }}
              >
                Показать предыдущие расчёты
              </Button>
            ) : null}
          </section>
        ) : null}
      </section>
    );
  },
);

function RbdResultDetail({ detail }: { readonly detail: RbdCalculationDetail }): React.JSX.Element {
  const result = detail.calculationSnapshot.resultSnapshot;
  const exact = (value: {
    readonly decimal: string | null;
    readonly decimal_preview: string;
  }): string => value.decimal ?? `${value.decimal_preview}…`;
  return (
    <div className="rbd-result-detail">
      <Text size="sm">
        Исполнение {detail.inputSnapshot.sourceRunId} · export revision{' '}
        {detail.inputSnapshot.exportRevision} ·{' '}
        {detail.inputSnapshot.planSelection === 'original' ? 'исходный' : 'эффективный'} план,
        revision {detail.inputSnapshot.planRevision}
      </Text>
      <dl className="reliability-facts">
        <div>
          <dt>Расчётное требование · N0 × k1</dt>
          <dd>{exact(result.required_cycles_exact)} циклов</dd>
        </div>
        <div>
          <dt>Частота nmax1 = nP</dt>
          <dd>{exact(result.maximum_rpm)} об/мин</dd>
        </div>
        <div>
          <dt>Установившееся вращение tUST1</dt>
          <dd>{exact(result.steady_duration_s_exact)} с</dd>
        </div>
        <div>
          <dt>Полный цикл TC1 = T01</dt>
          <dd>{exact(result.total_duration_s_exact)} с</dd>
        </div>
      </dl>
      <div>
        <Text fw={650}>Использованные значения и происхождение</Text>
        <dl className="rbd-provenance">
          {detail.inputSnapshot.inputSnapshot.fieldSelections.map((selection) => (
            <div key={selection.field}>
              <dt>{RBD_FIELD_LABELS[selection.field].label}</dt>
              <dd>
                {selection.value} {RBD_FIELD_LABELS[selection.field].unit} ·{' '}
                {selection.origin === 'source'
                  ? 'выбранный источник R130SH'
                  : 'дополнение инженера'}
                {' · '}
                {selection.sourceReference}
                {selection.origin === 'manual' ? ` · основание: ${selection.basis}` : ''}
                {selection.evidence?.document !== null && selection.evidence?.document !== undefined
                  ? ` · документ: ${selection.evidence.document.title}, редакция записи ${String(selection.evidence.document.recordRevision)}, ${selection.evidence.document.locator}`
                  : ''}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <Text size="sm">
        Формулы: {result.formula_references.join('; ')}. Арифметика:{' '}
        {detail.calculationSnapshot.numericPolicy}, алгоритм{' '}
        {detail.calculationSnapshot.algorithmVersion}.
      </Text>
      <Text size="sm">
        Расчёт по таблице 3:{' '}
        {result.failure_result.status === 'calculated'
          ? `${result.failure_result.cycles_to_failure} циклов до отказа`
          : `Не рассчитано: ${failureReason(result.failure_result.reason_code)}`}
      </Text>
      <div>
        <Text fw={650}>Основание обработки отказа</Text>
        {detail.inputSnapshot.inputSnapshot.failureEvidence === null ? (
          <Text size="sm">
            T_OTK не представлено; обязательные параметры испытания рассчитаны независимо.
          </Text>
        ) : (
          <dl className="rbd-provenance">
            <div>
              <dt>Применимость таблицы 3</dt>
              <dd>
                {failureOptions.find(
                  (item) =>
                    item.value ===
                    detail.inputSnapshot.inputSnapshot.failureEvidence?.applicability,
                )?.label ?? 'Неизвестная применимость'}
              </dd>
            </div>
            {detail.inputSnapshot.inputSnapshot.failureEvidence.durationToFailureS !== null ? (
              <div>
                <dt>Документированное T_OTK</dt>
                <dd>{detail.inputSnapshot.inputSnapshot.failureEvidence.durationToFailureS} с</dd>
              </div>
            ) : null}
            <div>
              <dt>Основание</dt>
              <dd>{detail.inputSnapshot.inputSnapshot.failureEvidence.basis}</dd>
            </div>
            {detail.inputSnapshot.inputSnapshot.failureEvidence.evidence?.document !== null &&
            detail.inputSnapshot.inputSnapshot.failureEvidence.evidence?.document !== undefined ? (
              <div>
                <dt>Точный документ</dt>
                <dd>
                  {detail.inputSnapshot.inputSnapshot.failureEvidence.evidence.document.title},
                  редакция записи{' '}
                  {
                    detail.inputSnapshot.inputSnapshot.failureEvidence.evidence.document
                      .recordRevision
                  }
                  ,{detail.inputSnapshot.inputSnapshot.failureEvidence.evidence.document.locator}
                </dd>
              </div>
            ) : null}
          </dl>
        )}
      </div>
      <Text size="xs" c="dimmed">
        Источник: {detail.inputSnapshot.planPayloadPath}, SHA-256{' '}
        {detail.inputSnapshot.planPayloadSha256}. Входной снимок{' '}
        {detail.inputSnapshot.analysisInputSnapshotId}; результат{' '}
        {detail.calculationSnapshot.calculationSnapshotId}.
      </Text>
      <div
        className="rbd-profile"
        role="img"
        aria-label="Схема выбранного расчётного профиля РБД; не фактические измерения"
      >
        <svg viewBox="0 0 1000 110" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <polyline
            points={result.diagram_points
              .map((point) => `${String(point.x)},${String(point.y)}`)
              .join(' ')}
          />
        </svg>
        <div className="rbd-profile-labels" aria-hidden="true">
          <span>Разгон</span>
          <span>Установившееся вращение</span>
          <span>Торможение</span>
        </div>
      </div>
      <Text size="xs" c="dimmed">
        Схема выбранного расчётного профиля не в масштабе времени; точные границы приведены ниже.
        Это не фактические измерения.
      </Text>
      <div className="rbd-phase-table-wrap">
        <table className="rbd-phase-table">
          <caption>Фазы расчётного профиля — точные границы</caption>
          <thead>
            <tr>
              <th scope="col">Фаза</th>
              <th scope="col">Начало, с</th>
              <th scope="col">Конец, с</th>
              <th scope="col">Начальная частота, об/мин</th>
              <th scope="col">Конечная частота, об/мин</th>
            </tr>
          </thead>
          <tbody>
            {result.phases.map((phase) => (
              <tr key={phase.phase}>
                <th scope="row">
                  {phase.phase === 'acceleration'
                    ? 'Разгон'
                    : phase.phase === 'steady_rotation'
                      ? 'Установившееся вращение'
                      : 'Торможение'}
                </th>
                <td>{exact(phase.start_s)}</td>
                <td>{exact(phase.end_s)}</td>
                <td>{exact(phase.start_rpm)}</td>
                <td>{exact(phase.end_rpm)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Text size="sm" c="dimmed">
        Требование не является фактически достигнутой наработкой образца или статистическим ресурсом
        модели.
      </Text>
    </div>
  );
}

function failureReason(code: string | null): string {
  switch (code) {
    case null:
      return 'необходимые входы отсутствуют';
    case 'failure_duration_unavailable':
      return 'точное T_OTK не представлено';
    case 'failure_endpoint_interval':
      return 'отказ локализован только интервалом';
    case 'failure_not_observed':
      return 'отказ не установлен';
    case 'failure_phase_unsupported':
      return 'фаза отказа не поддерживается первым сценарием';
    case 'failure_structure_ambiguous':
      return 'паузы или повторные разгоны неоднозначны';
    default:
      return code;
  }
}

function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Ядро недоступно. Черновик сохранён локально.',
    details: {},
    retryable: true,
  };
}

function contractError(): DesktopError {
  return {
    code: 'validation_error',
    message: 'Ответ расчёта не соответствует выбранному исполнению или идентификаторам.',
    details: {},
    retryable: false,
  };
}
