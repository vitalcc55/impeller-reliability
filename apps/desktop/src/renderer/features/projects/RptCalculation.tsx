import { Button, Group, Select, Text, Textarea, TextInput, Title } from '@mantine/core';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';

import type {
  CaseDocumentSummary,
  DesktopError,
  ImpellerApi,
  ReliabilityExecutionSummary,
  RptCalculationDetail,
  RptCalculationPage,
  RptPlanSource,
  WheelModelSummary,
} from '@impeller-reliability/contracts';

import {
  buildRptCalculationCommand,
  emptyRptCalculationDraft,
  RPT_FIELD_LABELS,
  RPT_INPUT_FIELDS,
  rptSourceValueForField,
  type RptCalculationDraft,
  type RptFailureApplicability,
  type RptInputField,
} from './rpt-calculation-draft';

interface RptCalculationProps {
  readonly desktopApi: ImpellerApi;
  readonly disabled: boolean;
  readonly onDirtyChange: (dirty: boolean) => void;
  readonly onPendingChange: (pending: boolean) => void;
}

export interface RptCalculationHandle {
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

const failureOptions: readonly { value: RptFailureApplicability; label: string }[] = [
  { value: 'unavailable', label: 'T_ОТК не представлено' },
  { value: 'exact_supported', label: 'Точное документированное время до отказа' },
  { value: 'interval_endpoint', label: 'Отказ известен только в интервале' },
  { value: 'right_censored', label: 'Отказ не установлен' },
  { value: 'ambiguous_pauses', label: 'Паузы неоднозначны' },
  { value: 'variable_cycle', label: 'Длительность цикла менялась' },
  { value: 'unknown_start', label: 'Начало отсчёта неизвестно' },
  { value: 'repeated_attempts', label: 'Повторные пуски неоднозначны' },
];

export const RptCalculation = forwardRef<RptCalculationHandle, RptCalculationProps>(
  function RptCalculation(
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
    const [source, setSource] = useState<RptPlanSource | null>(null);
    const [documents, setDocuments] = useState<readonly CaseDocumentSummary[]>([]);
    const [draft, setDraft] = useState<RptCalculationDraft>(emptyRptCalculationDraft);
    const [snapshotIds, setSnapshotIds] = useState<SnapshotIds>(newSnapshotIds);
    const [dirty, setDirty] = useState(false);
    const [unresolvedSave, setUnresolvedSave] = useState(false);
    const [detail, setDetail] = useState<RptCalculationDetail | null>(null);
    const [history, setHistory] = useState<RptCalculationPage | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const selectionRef = useRef(0);
    const detailRequestRef = useRef(0);
    const draftRevisionRef = useRef(0);
    const pendingRef = useRef<Promise<void> | null>(null);
    const unresolvedSaveRef = useRef(false);
    const submittedIdsRef = useRef<SnapshotIds | null>(null);
    const wheelRef = useRef<string | null>(null);
    const executionPagePendingRef = useRef(false);

    useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

    const loadWheel = useCallback(
      async (selectedWheelId: string): Promise<void> => {
        const revision = ++selectionRef.current;
        wheelRef.current = selectedWheelId;
        detailRequestRef.current += 1;
        setWheelModelId(selectedWheelId);
        setExecutions([]);
        setExecutionCursor(null);
        setExecutionPagePending(false);
        executionPagePendingRef.current = false;
        setExecutionId(null);
        setSource(null);
        setDocuments([]);
        setDetail(null);
        setHistory(null);
        setDraft(emptyRptCalculationDraft());
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
          if (executionResult.ok) {
            setExecutions(executionResult.result.items.filter((item) => item.method === 'rpt'));
            setExecutionCursor(executionResult.result.nextCursor);
          } else setError(executionResult.error);
          const historyResult = await desktopApi.rptCalculation.listPage(selectedWheelId, null, 25);
          if (revision !== selectionRef.current) return;
          if (historyResult.ok) setHistory(historyResult.result);
          else setError(historyResult.error);
          const documentResult = await desktopApi.caseDocument.list({
            includeArchived: false,
            documentKind: null,
          });
          if (revision !== selectionRef.current) return;
          if (documentResult.ok) setDocuments(documentResult.result);
          else setError(documentResult.error);
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
      };
    }, [desktopApi]);

    const selectSource = async (
      selectedExecutionId: string,
      selectedPlan: 'original' | 'effective',
    ): Promise<void> => {
      const revision = ++selectionRef.current;
      detailRequestRef.current += 1;
      setExecutionId(selectedExecutionId);
      setPlanSelection(selectedPlan);
      setSource(null);
      setDetail(null);
      setDraft(emptyRptCalculationDraft());
      setSnapshotIds(newSnapshotIds());
      setDirty(false);
      setError(null);
      setMessage(null);
      setBusy('source');
      try {
        const result = await desktopApi.rptCalculation.getSourceInputs(
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

    const editDraft = (next: RptCalculationDraft): void => {
      draftRevisionRef.current += 1;
      setDraft(next);
      setDirty(true);
      setError(null);
      setMessage(null);
      if (!unresolvedSaveRef.current) setSnapshotIds(newSnapshotIds());
    };

    const editField = (
      field: RptInputField,
      next: RptCalculationDraft['fields'][RptInputField],
    ): void => editDraft({ ...draft, fields: { ...draft.fields, [field]: next } });

    const refreshHistory = async (selectedWheelId: string): Promise<void> => {
      const result = await desktopApi.rptCalculation.listPage(selectedWheelId, null, 25);
      if (wheelRef.current !== selectedWheelId) return;
      if (result.ok) setHistory(result.result);
      else
        setError({
          ...result.error,
          message: `Расчёт сохранён, но история не обновилась: ${result.error.message}`,
        });
    };

    const save = (): void => {
      if (source === null || busy !== null || disabled) return;
      const built = buildRptCalculationCommand(source, draft, documents, snapshotIds);
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
      const revision = selectionRef.current;
      const draftRevision = draftRevisionRef.current;
      const submittedIds = snapshotIds;
      submittedIdsRef.current = submittedIds;
      setBusy('save');
      setError(null);
      onPendingChange(true);
      const pending = (async (): Promise<void> => {
        try {
          const result = await desktopApi.rptCalculation.create(command);
          if (revision !== selectionRef.current) return;
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
          )
            return setError(contractError());
          unresolvedSaveRef.current = false;
          setUnresolvedSave(false);
          setDetail(result.result.detail);
          detailRequestRef.current += 1;
          setMessage('Расчёт РПТ сохранён: входы и результат зафиксированы неизменяемой парой.');
          if (draftRevisionRef.current === draftRevision) {
            setDirty(false);
            setSnapshotIds(newSnapshotIds());
          }
          if (wheelModelId !== null) {
            try {
              await refreshHistory(wheelModelId);
            } catch {
              setError({
                code: 'worker_unavailable',
                message: 'Расчёт сохранён, но историю пока не удалось обновить.',
                details: {},
                retryable: true,
              });
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
          setDraft(emptyRptCalculationDraft());
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
          const selectedWheelId = wheelRef.current;
          if (selectedWheelId === null) return true;
          try {
            const page = await desktopApi.rptCalculation.listPage(selectedWheelId, null, 25);
            if (!page.ok) {
              setError(page.error);
              return false;
            }
            setHistory(page.result);
            const submitted = submittedIdsRef.current;
            if (unresolvedSaveRef.current && submitted !== null) {
              const saved = await desktopApi.rptCalculation.getDetail(
                submitted.calculationSnapshotId,
              );
              if (!saved.ok) {
                if (saved.error.code === 'entity_not_found') {
                  unresolvedSaveRef.current = false;
                  setUnresolvedSave(false);
                  setError(null);
                  return true;
                }
                setError(saved.error);
                return false;
              }
              if (
                saved.result.inputSnapshot.analysisInputSnapshotId !==
                  submitted.analysisInputSnapshotId ||
                saved.result.calculationSnapshot.calculationSnapshotId !==
                  submitted.calculationSnapshotId
              ) {
                setError(contractError());
                return false;
              }
              setDetail(saved.result);
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
      <section className="rbd-calculation" aria-labelledby="rpt-calculation-title">
        <div className="section-heading">
          <div>
            <Title id="rpt-calculation-title" order={2}>
              Расчёт РПТ
            </Title>
            <Text size="sm" c="dimmed">
              Требуемые параметры циклов пуск–торможение по ПМИ Р130У. Это не фактически достигнутый
              ресурс образца.
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
            Ответ на сохранение не подтверждён. Черновик и идентификаторы операции сохранены.
            <Button size="compact-sm" disabled={disabled || busy !== null} onClick={save}>
              Повторить сохранение с теми же ID
            </Button>
          </div>
        ) : null}
        <div className="rbd-calculation-layout">
          <section aria-labelledby="rpt-source-heading">
            <Title order={3} id="rpt-source-heading">
              Исполнение и источник
            </Title>
            <Select
              label="Модель рабочего колеса"
              data={wheels.map((item) => ({ value: item.wheelModelId, label: item.fullName }))}
              value={wheelModelId}
              disabled={locked || dirty}
              searchable
              onChange={(value) => {
                if (value !== null) void loadWheel(value);
              }}
            />
            {wheelModelId !== null ? (
              <div className="rbd-execution-list" aria-label="Исполнения РПТ">
                {executions.length === 0 ? (
                  <Text size="sm">Подготовленных исполнений РПТ нет.</Text>
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
                    <strong>РПТ · {item.sourceRunId}</strong>
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
                      const revision = selectionRef.current;
                      executionPagePendingRef.current = true;
                      setExecutionPagePending(true);
                      void desktopApi.reliabilityExecution
                        .listPage(selectedWheelId, executionCursor, 25)
                        .then((result) => {
                          if (
                            revision !== selectionRef.current ||
                            wheelRef.current !== selectedWheelId
                          )
                            return;
                          if (result.ok) {
                            setExecutions((previous) => [
                              ...previous,
                              ...result.result.items.filter((item) => item.method === 'rpt'),
                            ]);
                            setExecutionCursor(result.result.nextCursor);
                          } else setError(result.error);
                        })
                        .catch(() => {
                          if (revision === selectionRef.current) setError(unavailableError());
                        })
                        .finally(() => {
                          if (revision === selectionRef.current) {
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
              <>
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
                <div className="rbd-producer-targets">
                  <Text fw={650}>Методические требования в источнике R130SH</Text>
                  <Text size="sm">
                    {source.methodicalRequirements.requiredCyclesExact} циклов ·{' '}
                    {source.methodicalRequirements.cycleDurationSExact} с/цикл ·{' '}
                    {source.methodicalRequirements.requiredTotalDurationSExact} с всего
                  </Text>
                  <Text size="xs" c="dimmed">
                    Это данные сохранённого плана, отдельные от выбранных входов и результата
                    текущего расчёта.
                  </Text>
                </div>
                <div className="rbd-producer-targets">
                  <Text fw={650}>Уставка в источнике R130SH</Text>
                  <Text size="sm">
                    {source.executionTargets.targetCycles} циклов ·{' '}
                    {source.executionTargets.lowerRpm}–{source.executionTargets.upperRpm} об/мин ·{' '}
                    {source.executionTargets.cycleDurationS} с/цикл ·{' '}
                    {source.executionTargets.totalDurationS} с всего
                  </Text>
                  <Text size="xs" c="dimmed">
                    Нижняя точка уставки:{' '}
                    {lowerPointPolicyLabel(source.executionTargets.lowerPointPolicy)}. Нижняя точка
                    плана: {lowerPointPolicyLabel(source.sourceValues.lowerPointPolicy)}
                    {source.sourceValues.explicitLowerRpm !== null
                      ? `, ${source.sourceValues.explicitLowerRpm} об/мин`
                      : ''}
                    . Уставка и точное расчётное требование показаны отдельно.
                  </Text>
                </div>
              </>
            ) : null}
          </section>
          <section aria-labelledby="rpt-input-heading">
            <Title order={3} id="rpt-input-heading">
              Явные расчётные входы
            </Title>
            {source === null ? (
              <Text size="sm">Выберите исполнение РПТ и редакцию плана.</Text>
            ) : null}
            {source !== null ? (
              <>
                <Text size="xs" c="dimmed">
                  Доступно документов дела: {documents.length}
                </Text>
                {RPT_INPUT_FIELDS.map((field) => {
                  const choice = draft.fields[field];
                  const sourceValue = rptSourceValueForField(source, field);
                  return (
                    <fieldset key={field} className="rbd-field">
                      <legend>
                        {RPT_FIELD_LABELS[field].label} · {RPT_FIELD_LABELS[field].unit}
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
                  <legend>
                    Фактическое время до отказа — только при точном документированном отсчёте
                  </legend>
                  <Select
                    label="Применимость формулы таблицы 4"
                    data={failureOptions}
                    value={draft.failure.applicability}
                    disabled={locked}
                    onChange={(value) => {
                      const selected = failureOptions.find((item) => item.value === value);
                      if (selected !== undefined)
                        editDraft({
                          ...draft,
                          failure: {
                            applicability: selected.value,
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
                          label="T_ОТК, с — от начала испытания до отказа"
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
                        label="Основание применимости"
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
                            label="Документ с моментом отказа и началом отсчёта"
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
                  ) : null}
                </fieldset>
                <Textarea
                  label="Основание создания расчётного снимка"
                  value={draft.reason}
                  disabled={locked}
                  autosize
                  minRows={2}
                  onChange={(event) => editDraft({ ...draft, reason: event.currentTarget.value })}
                />
                <Group className="form-actions">
                  <Button disabled={locked} loading={busy === 'save'} onClick={save}>
                    Рассчитать и сохранить РПТ
                  </Button>
                </Group>
                <Text size="xs" c="dimmed">
                  Значения результата рассчитывает Python worker. Сохранение создаёт неизменяемую
                  пару входов и результата.
                </Text>
              </>
            ) : null}
          </section>
        </div>
        {detail !== null ? <RptResultDetail detail={detail} /> : null}
        {history !== null ? (
          <section aria-labelledby="rpt-history-heading">
            <Title order={3} id="rpt-history-heading">
              Сохранённые расчёты РПТ
            </Title>
            {history.items.length === 0 ? (
              <Text size="sm">Пока нет сохранённых расчётов для этой модели.</Text>
            ) : null}
            <div className="rbd-execution-list">
              {history.items.map((item) => (
                <button
                  key={item.calculationSnapshotId}
                  type="button"
                  className="reliability-record"
                  disabled={locked}
                  onClick={() => {
                    const request = ++detailRequestRef.current;
                    void desktopApi.rptCalculation
                      .getDetail(item.calculationSnapshotId)
                      .then((result) => {
                        if (request !== detailRequestRef.current) return;
                        if (result.ok) setDetail(result.result);
                        else setError(result.error);
                      })
                      .catch(() => {
                        if (request === detailRequestRef.current) setError(unavailableError());
                      });
                  }}
                >
                  <strong>
                    {item.requiredCycles} циклов · таблица 4:{' '}
                    {item.failureStatus === 'calculated' ? 'рассчитана' : 'не рассчитана'}
                  </strong>
                  <span>{new Date(item.createdAtUtc).toLocaleString('ru-RU')}</span>
                </button>
              ))}
            </div>
            {history.nextCursor !== null ? (
              <Button
                variant="subtle"
                size="compact-sm"
                disabled={locked}
                onClick={() => {
                  const selectedWheelId = wheelModelId;
                  const cursor = history.nextCursor;
                  if (selectedWheelId === null || cursor === null) return;
                  void desktopApi.rptCalculation
                    .listPage(selectedWheelId, cursor, 25)
                    .then((result) => {
                      if (wheelRef.current !== selectedWheelId) return;
                      if (result.ok)
                        setHistory({
                          items: [...history.items, ...result.result.items],
                          nextCursor: result.result.nextCursor,
                        });
                      else setError(result.error);
                    })
                    .catch(() => setError(unavailableError()));
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

function RptResultDetail({ detail }: { readonly detail: RptCalculationDetail }): React.JSX.Element {
  const input = detail.inputSnapshot.inputSnapshot;
  const result = detail.calculationSnapshot.resultSnapshot;
  const exact = (value: {
    readonly numerator: string;
    readonly denominator: string;
    readonly decimal: string | null;
    readonly decimal_preview: string;
  }): string =>
    value.decimal ?? `${value.numerator}/${value.denominator} (≈ ${value.decimal_preview})`;
  return (
    <section aria-labelledby="rpt-result-heading" className="rbd-result-detail">
      <Title order={3} id="rpt-result-heading">
        Зафиксированный результат
      </Title>
      <Text size="sm">
        Исполнение {input.source.runId} · export revision {input.source.exportRevision} ·{' '}
        {input.source.planSelection === 'original' ? 'исходный' : 'эффективный'} план, revision{' '}
        {input.source.planRevision}
      </Text>
      <Text fw={650}>Контекст сохранённого плана</Text>
      <dl className="reliability-facts">
        <div>
          <dt>Методическое требование источника</dt>
          <dd>
            {input.source.methodicalRequirements.required_cycles_exact} циклов ·{' '}
            {input.source.methodicalRequirements.cycle_duration_s_exact} с/цикл ·{' '}
            {input.source.methodicalRequirements.required_total_duration_s_exact} с всего
          </dd>
        </div>
        <div>
          <dt>Уставка источника</dt>
          <dd>
            {input.source.executionTargets.target_cycles} циклов ·{' '}
            {input.source.executionTargets.lower_rpm}–{input.source.executionTargets.upper_rpm}{' '}
            об/мин · {input.source.executionTargets.cycle_duration_s} с/цикл ·{' '}
            {input.source.executionTargets.total_duration_s} с всего
          </dd>
        </div>
      </dl>
      <dl className="reliability-facts">
        <div>
          <dt>Требование NЦ × k2</dt>
          <dd>{exact(result.required_cycles_exact)} циклов</dd>
        </div>
        <div>
          <dt>Полный цикл TЦ</dt>
          <dd>{exact(result.cycle_duration_s_exact)} с</dd>
        </div>
        <div>
          <dt>Полное расчётное время</dt>
          <dd>
            {exact(result.total_duration_s_exact)} с · {exact(result.total_duration_h_exact)} ч
          </dd>
        </div>
        <div>
          <dt>Частоты nmax / nmin</dt>
          <dd>
            {exact(result.maximum_rpm)} / {exact(result.minimum_rpm)} об/мин
          </dd>
        </div>
      </dl>
      <Text size="sm">
        Нижняя точка в плане: {lowerPointPolicyLabel(result.lower_point_comparison.source_policy)}
        {result.lower_point_comparison.source_explicit_lower_rpm !== null
          ? `, ${result.lower_point_comparison.source_explicit_lower_rpm} об/мин`
          : ''}
        ; уставка: {lowerPointPolicyLabel(result.lower_point_comparison.target_policy)},{' '}
        {result.lower_point_comparison.target_lower_rpm} об/мин.{' '}
        {lowerPointComparisonLabel(result.lower_point_comparison.status)}
      </Text>
      <Text fw={650}>Использованные значения и происхождение</Text>
      <dl className="rbd-provenance">
        {input.fieldSelections.map((item) => (
          <div key={item.field}>
            <dt>{RPT_FIELD_LABELS[item.field].label}</dt>
            <dd>
              Принято: {item.value} {RPT_FIELD_LABELS[item.field].unit} ·{' '}
              {item.origin === 'source' ? 'выбранный источник R130SH' : 'дополнение инженера'} ·
              поле источника: {item.sourceReference}
              {item.origin === 'manual'
                ? ` · исходное значение плана: ${item.rawSourceValue === null ? 'отсутствует' : `${item.rawSourceValue} ${RPT_FIELD_LABELS[item.field].unit}`}`
                : ''}
              {item.origin === 'manual' ? ` · основание: ${item.basis}` : ''}
              {item.document !== null
                ? ` · документ: ${item.document.title}, редакция ${item.document.recordRevision}, ${item.document.locator}`
                : ''}
            </dd>
          </div>
        ))}
      </dl>
      <Text size="sm">
        Таблица 4:{' '}
        {result.failure_result.status === 'calculated'
          ? `${result.failure_result.cycles_to_failure} циклов до отказа`
          : `не рассчитана — ${failureReason(result.failure_result.reason_code)}`}
        .
      </Text>
      {input.failureEvidence !== null ? (
        <Text size="sm">
          T_ОТК:{' '}
          {input.failureEvidence.durationToFailureS === null
            ? 'не представлено'
            : `${input.failureEvidence.durationToFailureS} с`}
          {' · '}Применимость:{' '}
          {
            failureOptions.find((option) => option.value === input.failureEvidence?.applicability)
              ?.label
          }
          {' · '}Основание: {input.failureEvidence.basis}
          {input.failureEvidence.document !== null
            ? ` · ${input.failureEvidence.document.title}, редакция записи ${input.failureEvidence.document.recordRevision} (${input.failureEvidence.document.revisionLabel}), ${input.failureEvidence.document.locator}`
            : ''}
        </Text>
      ) : (
        <Text size="sm">T_ОТК: не представлено · свидетельство отказа не выбрано.</Text>
      )}
      <Text size="xs" c="dimmed">
        Формулы: {result.formula_references.join('; ')}. Арифметика:{' '}
        {detail.calculationSnapshot.numericPolicy}; алгоритм{' '}
        {detail.calculationSnapshot.algorithmVersion}.
      </Text>
      <div
        className="rbd-profile"
        role="img"
        aria-label="Схема двух расчётных циклов РПТ; не фактические измерения"
      >
        <svg viewBox="0 0 2000 110" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <polyline
            points={result.diagram_points
              .map((point) => `${String(point.x)},${String(point.y)}`)
              .join(' ')}
          />
        </svg>
        <div className="rpt-profile-labels" aria-hidden="true">
          <span>Первый цикл</span>
          <span>Повтор цикла</span>
        </div>
      </div>
      <Text size="xs" c="dimmed">
        Схема не в масштабе времени. Точные границы фаз приведены ниже; это не фактические
        измерения.
      </Text>
      <div className="rbd-phase-table-wrap">
        <table className="rbd-phase-table">
          <caption>Фазы расчётного цикла РПТ — точные границы</caption>
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
      <Text size="xs" c="dimmed">
        Снимок входов {detail.inputSnapshot.analysisInputSnapshotId}; результат{' '}
        {detail.calculationSnapshot.calculationSnapshotId}; SHA-256 плана{' '}
        {input.source.payloadSha256}.
      </Text>
    </section>
  );
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

function lowerPointPolicyLabel(policy: 'one_percent' | 'full_stop' | 'explicit_rpm'): string {
  switch (policy) {
    case 'one_percent':
      return '1 % от nP';
    case 'full_stop':
      return 'полная остановка';
    case 'explicit_rpm':
      return 'явная частота';
  }
}

function lowerPointComparisonLabel(
  status: RptCalculationDetail['calculationSnapshot']['resultSnapshot']['lower_point_comparison']['status'],
): string {
  switch (status) {
    case 'matches_typical_formula':
      return 'Нижняя точка уставки совпадает с типовой формулой.';
    case 'differs_from_typical_formula':
      return 'Нижняя точка уставки отличается от типовой формулы.';
    case 'source_target_policy_conflict':
      return 'Политика плана и уставки различается.';
    case 'target_unavailable':
      return 'Нижняя точка уставки недоступна для сопоставления.';
  }
}

function failureReason(code: string | null): string {
  switch (code) {
    case null:
      return 'применимость не подтверждена';
    case 'failure_duration_unavailable':
      return 'точное T_ОТК не представлено';
    case 'failure_endpoint_interval':
      return 'отказ установлен только в интервале';
    case 'failure_not_observed':
      return 'отказ не установлен';
    case 'failure_structure_ambiguous':
      return 'паузы неоднозначны';
    case 'failure_cycle_variable':
      return 'длительность цикла менялась';
    case 'failure_start_unknown':
      return 'начало отсчёта неизвестно';
    case 'failure_attempts_ambiguous':
      return 'повторные пуски неоднозначны';
    default:
      return code;
  }
}
