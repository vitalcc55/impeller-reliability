import { Button, Select, Text, Textarea, Title } from '@mantine/core';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';

import type {
  DesktopError,
  DesktopResult,
  CustomerProfile,
  ImpellerApi,
  ImportedRunDetail,
  ImportedRunEnrichmentResolutionCommand,
  ImportedRunSummary,
  ProjectOverview,
  RunPackageImportJob,
  Specimen,
  SpecimenSummary,
  WheelModel,
} from '@impeller-reliability/contracts';

interface R130shResultsProps {
  readonly desktopApi: ImpellerApi;
  readonly project: ProjectOverview;
  readonly disabled: boolean;
  readonly onDirtyChange: (dirty: boolean) => void;
  readonly onPendingChange: (pending: boolean) => void;
  readonly requestTransition: (hasDirty: boolean, action: () => void, discard: () => void) => void;
}

export interface R130shResultsHandle {
  discardDraft(): void;
  waitForPendingSave(): Promise<void>;
  verifyAfterReattach(): Promise<void>;
}

type ResolutionTarget = {
  readonly id: string;
  readonly label: string;
  readonly sourcePayloadPath: string;
  readonly sourceField: string;
  readonly sourceValue: string | null;
  readonly analystValue: string | null;
  readonly targetEntityType: 'customer_profile' | 'wheel_model' | 'specimen';
  readonly targetEntityId: string;
  readonly targetField: string;
  readonly expectedTargetRevision: number | null;
  readonly copyAllowed: boolean;
};

const terminalStates = new Set<RunPackageImportJob['state']>(['completed', 'failed', 'cancelled']);

export const R130shResults = forwardRef<R130shResultsHandle, R130shResultsProps>(
  function R130shResults(
    { desktopApi, project, disabled, onDirtyChange, onPendingChange, requestTransition },
    ref,
  ): React.JSX.Element {
    const [items, setItems] = useState<readonly ImportedRunSummary[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [selectionRevision, setSelectionRevision] = useState(0);
    const [detail, setDetail] = useState<ImportedRunDetail | null>(null);
    const [specimens, setSpecimens] = useState<readonly SpecimenSummary[]>([]);
    const [boundSpecimen, setBoundSpecimen] = useState<Specimen | null>(null);
    const [boundWheel, setBoundWheel] = useState<WheelModel | null>(null);
    const [customer, setCustomer] = useState<CustomerProfile | null>(null);
    const [job, setJob] = useState<RunPackageImportJob | null>(null);
    const [startAttemptId, setStartAttemptId] = useState<string | null>(null);
    const [bindingSpecimenId, setBindingSpecimenId] = useState<string | null>(null);
    const [bindingReason, setBindingReason] = useState('');
    const [resolutionTargetId, setResolutionTargetId] = useState<string | null>(null);
    const [resolutionDecision, setResolutionDecision] = useState<
      'use_source' | 'use_analyst' | 'copied_to_analyst'
    >('use_source');
    const [resolutionReason, setResolutionReason] = useState('');
    const [message, setMessage] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [loading, setLoading] = useState(true);
    const [mutationPending, setMutationPending] = useState(false);
    const pendingRef = useRef<Promise<void> | null>(null);
    const detailRequestRef = useRef(0);
    const unresolvedMutationRef = useRef<string | null>(null);
    const jobRef = useRef<RunPackageImportJob | null>(null);
    useEffect(() => {
      jobRef.current = job;
    }, [job]);

    const dirty =
      bindingSpecimenId !== (detail?.summary.localSpecimenId ?? null) ||
      bindingReason.trim() !== '' ||
      resolutionTargetId !== null ||
      resolutionReason.trim() !== '';
    useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
    const importPending = job !== null && !terminalStates.has(job.state);
    const pending = importPending || mutationPending || startAttemptId !== null;
    useEffect(() => onPendingChange(pending), [onPendingChange, pending]);

    const handleError = useCallback((nextError: DesktopError): void => {
      if (nextError.code === 'cancelled') return;
      setError(nextError);
      setMessage(null);
    }, []);

    const selectRun = useCallback((localImportId: string): void => {
      detailRequestRef.current += 1;
      setDetail(null);
      setBoundSpecimen(null);
      setBoundWheel(null);
      setCustomer(null);
      setBindingReason('');
      setResolutionTargetId(null);
      setResolutionReason('');
      setSelectedId(localImportId);
      setSelectionRevision((current) => current + 1);
    }, []);

    const loadItems = useCallback(async (): Promise<boolean> => {
      const [runsResult, specimensResult] = await Promise.all([
        desktopApi.importedRun.list(),
        desktopApi.specimen.list(false),
      ]);
      if (!runsResult.ok) {
        handleError(runsResult.error);
        return false;
      }
      if (!specimensResult.ok) {
        handleError(specimensResult.error);
        return false;
      }
      setItems(runsResult.result);
      setSpecimens(specimensResult.result);
      setSelectedId((current) =>
        current !== null && runsResult.result.some((item) => item.localImportId === current)
          ? current
          : (runsResult.result[0]?.localImportId ?? null),
      );
      return true;
    }, [desktopApi, handleError]);

    useEffect(
      () => () => {
        const current = jobRef.current;
        if (current !== null && terminalStates.has(current.state)) {
          void desktopApi.runPackageImport.discard(current.jobId);
        }
      },
      [desktopApi],
    );

    const loadDetail = useCallback(
      async (localImportId: string): Promise<boolean> => {
        const requestGeneration = detailRequestRef.current + 1;
        detailRequestRef.current = requestGeneration;
        const result = await desktopApi.importedRun.get(localImportId);
        if (detailRequestRef.current !== requestGeneration) return false;
        if (!result.ok) {
          handleError(result.error);
          return false;
        }
        const nextDetail = result.result;
        setDetail(nextDetail);
        setBindingSpecimenId(nextDetail.summary.localSpecimenId);
        setBindingReason('');
        setResolutionTargetId(null);
        setResolutionReason('');
        const [customerResult, specimenResult] = await Promise.all([
          desktopApi.caseCustomer.get(),
          nextDetail.summary.localSpecimenId === null
            ? Promise.resolve(null)
            : desktopApi.specimen.get(nextDetail.summary.localSpecimenId),
        ]);
        if (detailRequestRef.current !== requestGeneration) return false;
        if (!customerResult.ok) {
          handleError(customerResult.error);
          return false;
        }
        setCustomer(customerResult.result);
        if (specimenResult === null) {
          setBoundSpecimen(null);
          setBoundWheel(null);
          if (unresolvedMutationRef.current === localImportId) {
            unresolvedMutationRef.current = null;
            setError(null);
            setMessage('Сохранённое изменение восстановлено после перезапуска worker.');
          }
          return true;
        }
        if (!specimenResult.ok) {
          handleError(specimenResult.error);
          return false;
        }
        setBoundSpecimen(specimenResult.result);
        const wheelResult = await desktopApi.wheelModel.get(specimenResult.result.wheelModelId);
        if (detailRequestRef.current !== requestGeneration) return false;
        if (!wheelResult.ok) {
          handleError(wheelResult.error);
          return false;
        }
        setBoundWheel(wheelResult.result);
        if (unresolvedMutationRef.current === localImportId) {
          unresolvedMutationRef.current = null;
          setError(null);
          setMessage('Сохранённое изменение восстановлено после перезапуска worker.');
        }
        return true;
      },
      [desktopApi, handleError],
    );

    useEffect(() => {
      let active = true;
      const timer = window.setTimeout(() => {
        void loadItems()
          .catch(() => {
            if (active) handleError(unavailableError());
          })
          .finally(() => {
            if (active) setLoading(false);
          });
      }, 0);
      return () => {
        active = false;
        window.clearTimeout(timer);
      };
    }, [handleError, loadItems, project.projectId]);
    useEffect(() => {
      if (selectedId === null) return;
      const timer = window.setTimeout(() => {
        void loadDetail(selectedId).catch(() => handleError(unavailableError()));
      }, 0);
      return () => window.clearTimeout(timer);
    }, [handleError, loadDetail, selectedId, selectionRevision]);

    useEffect(() => {
      const polledJobId =
        job !== null && !terminalStates.has(job.state) ? job.jobId : startAttemptId;
      if (polledJobId === null) return undefined;
      let disposed = false;
      let timer: number | null = null;
      const poll = (): void => {
        void desktopApi.runPackageImport
          .get(polledJobId)
          .then(async (result) => {
            if (disposed) return;
            if (!result.ok) {
              handleError(result.error);
              if (isTransientPollError(result.error)) {
                timer = window.setTimeout(poll, 1_000);
              }
              return;
            }
            if (result.result.state === 'completed' && result.result.result !== null) {
              setError(null);
              await loadItems();
              if (disposed) return;
              setStartAttemptId(null);
              setJob(result.result);
              selectRun(result.result.result.importedRun.localImportId);
              setMessage(
                result.result.result.disposition === 'existing'
                  ? 'Этот пакет уже зарегистрирован. Открыт существующий импорт.'
                  : 'Результат R130SH сохранён в аналитическом деле.',
              );
            } else {
              setStartAttemptId(null);
              setJob(result.result);
            }
            if (!terminalStates.has(result.result.state)) {
              timer = window.setTimeout(poll, pollDelay(result.result.state));
            }
          })
          .catch(() => {
            if (!disposed) {
              handleError(unavailableError());
              timer = window.setTimeout(poll, 1_000);
            }
          });
      };
      timer = window.setTimeout(poll, pollDelay(job?.state ?? 'queued'));
      return () => {
        disposed = true;
        if (timer !== null) window.clearTimeout(timer);
      };
    }, [desktopApi, handleError, job, loadItems, selectRun, startAttemptId]);

    const startImport = (allowDiagnosticPartial: boolean): void => {
      const operation = (async (): Promise<void> => {
        const nextJobId = crypto.randomUUID();
        setStartAttemptId(nextJobId);
        let result: DesktopResult<RunPackageImportJob>;
        try {
          result = await desktopApi.runPackageImport.selectAndStart({
            jobId: nextJobId,
            ...(job !== null && terminalStates.has(job.state) ? { replaceJobId: job.jobId } : {}),
            allowDiagnosticPartial,
          });
        } catch {
          handleError(unavailableError());
          return;
        }
        if (!result.ok) {
          if (!mayHaveStartedImport(result.error)) setStartAttemptId(null);
          handleError(result.error);
          return;
        }
        setStartAttemptId(null);
        setJob(result.result);
        setError(null);
        setMessage(null);
      })();
      setMutationPending(true);
      pendingRef.current = operation;
      void operation.finally(() => {
        if (pendingRef.current === operation) {
          pendingRef.current = null;
          setMutationPending(false);
        }
      });
    };

    const cancelImport = (): void => {
      if (job === null) return;
      void desktopApi.runPackageImport
        .cancel(job.jobId)
        .then((result) => {
          if (result.ok) setJob(result.result);
          else handleError(result.error);
        })
        .catch(() => handleError(unavailableError()));
    };

    const verifySource = (): void => {
      if (detail === null) return;
      const operation = desktopApi.importedRun
        .verifySource(detail.summary.localImportId)
        .then(async (result) => {
          if (!result.ok) return handleError(result.error);
          if (!(await loadDetail(detail.summary.localImportId))) return;
          setError(null);
          setMessage(
            `Проверка managed archive завершена: ${integrityLabel(result.result.sourceIntegrity)}.`,
          );
        })
        .catch(() => handleError(unavailableError()));
      setMutationPending(true);
      pendingRef.current = operation;
      void operation.finally(() => {
        if (pendingRef.current === operation) {
          pendingRef.current = null;
          setMutationPending(false);
        }
      });
    };

    const saveBinding = (): void => {
      if (detail === null || bindingReason.trim() === '') return;
      unresolvedMutationRef.current = detail.summary.localImportId;
      const operation = desktopApi.importedRun
        .bindSpecimen({
          sourceSpecimenId: detail.summary.sourceSpecimenId,
          localSpecimenId: bindingSpecimenId,
          expectedRevision: detail.summary.bindingRevision,
          actor: 'local_user',
          reason: bindingReason,
        })
        .then(async (result) => {
          if (!result.ok) {
            if (!mayHaveCommittedMutation(result.error)) unresolvedMutationRef.current = null;
            handleError(result.error);
            return;
          }
          setBindingReason('');
          if (!(await loadItems())) return;
          if (!(await loadDetail(detail.summary.localImportId))) return;
          unresolvedMutationRef.current = null;
          setError(null);
          setMessage('Привязка source specimen сохранена с audit provenance.');
        })
        .catch(() => handleError(unavailableError()));
      setMutationPending(true);
      pendingRef.current = operation;
      void operation.finally(() => {
        if (pendingRef.current === operation) {
          pendingRef.current = null;
          setMutationPending(false);
        }
      });
    };

    const materializeReliabilityExecution = (): void => {
      if (detail === null) return;
      const operation = desktopApi.reliabilityExecution
        .materialize(detail.summary.localImportId)
        .then((result) => {
          if (!result.ok) return handleError(result.error);
          setError(null);
          setMessage(
            `Аналитическое исполнение ${result.result.method.toUpperCase()} подтверждено для связанного образца.`,
          );
        })
        .catch(() => handleError(unavailableError()));
      setMutationPending(true);
      pendingRef.current = operation;
      void operation.finally(() => {
        if (pendingRef.current === operation) {
          pendingRef.current = null;
          setMutationPending(false);
        }
      });
    };

    const resolutionTargets = buildResolutionTargets(
      project,
      detail,
      customer,
      boundSpecimen,
      boundWheel,
    );
    const selectedResolution = resolutionTargets.find((item) => item.id === resolutionTargetId);
    const saveResolution = (): void => {
      if (detail === null || selectedResolution === undefined) return;
      if (
        selectedResolution.sourceValue !== selectedResolution.analystValue &&
        resolutionDecision !== 'copied_to_analyst' &&
        resolutionReason.trim() === ''
      )
        return;
      const command: ImportedRunEnrichmentResolutionCommand = {
        resolutionId: crypto.randomUUID(),
        localImportId: detail.summary.localImportId,
        sourcePayloadPath: selectedResolution.sourcePayloadPath,
        sourceField: selectedResolution.sourceField,
        targetEntityType: selectedResolution.targetEntityType,
        targetEntityId: selectedResolution.targetEntityId,
        targetField: selectedResolution.targetField,
        decision: resolutionDecision,
        actor: 'local_user',
        reason: resolutionReason,
        expectedTargetRevision: selectedResolution.expectedTargetRevision,
      };
      unresolvedMutationRef.current = detail.summary.localImportId;
      const operation = desktopApi.importedRun
        .applyEnrichmentResolution(command)
        .then(async (result) => {
          if (!result.ok) {
            if (!mayHaveCommittedMutation(result.error)) unresolvedMutationRef.current = null;
            handleError(result.error);
            return;
          }
          setResolutionTargetId(null);
          setResolutionReason('');
          if (!(await loadDetail(detail.summary.localImportId))) return;
          unresolvedMutationRef.current = null;
          setError(null);
          setMessage('Решение по расхождению сохранено; source value не изменялось.');
        })
        .catch(() => handleError(unavailableError()));
      setMutationPending(true);
      pendingRef.current = operation;
      void operation.finally(() => {
        if (pendingRef.current === operation) {
          pendingRef.current = null;
          setMutationPending(false);
        }
      });
    };

    const discardDraft = useCallback((): void => {
      setBindingSpecimenId(detail?.summary.localSpecimenId ?? null);
      setBindingReason('');
      setResolutionTargetId(null);
      setResolutionReason('');
    }, [detail?.summary.localSpecimenId]);
    useImperativeHandle(
      ref,
      () => ({
        discardDraft,
        waitForPendingSave: async () => {
          if (pendingRef.current !== null) await pendingRef.current;
          const activeJobId =
            job !== null && !terminalStates.has(job.state) ? job.jobId : startAttemptId;
          if (activeJobId !== null) {
            const cancelled = await desktopApi.runPackageImport.cancel(activeJobId);
            if (!cancelled.ok) {
              if (cancelled.error.code === 'entity_not_found') {
                const committed = await desktopApi.importedRun.get(activeJobId);
                setStartAttemptId(null);
                if (committed.ok) {
                  setJob(completedRecoveredJob(activeJobId, committed.result.summary));
                  selectRun(committed.result.summary.localImportId);
                  return;
                }
                if (committed.error.code === 'entity_not_found') {
                  if (job !== null) setJob(interruptedJob(job));
                  return;
                }
                throw new Error(committed.error.message);
              }
              throw new Error(cancelled.error.message);
            }
            let settled = cancelled.result;
            for (
              let attempt = 0;
              attempt < 50 && !terminalStates.has(settled.state);
              attempt += 1
            ) {
              await delay(200);
              const current = await desktopApi.runPackageImport.get(activeJobId);
              if (!current.ok) throw new Error(current.error.message);
              settled = current.result;
            }
            if (!terminalStates.has(settled.state))
              throw new Error('Import job не завершился за bounded cancel/drain.');
            setStartAttemptId(null);
            setJob(settled);
          }
        },
        verifyAfterReattach: async () => {
          const activeJobId =
            job !== null && !terminalStates.has(job.state) ? job.jobId : startAttemptId;
          if (activeJobId !== null) {
            const committed = await desktopApi.importedRun.get(activeJobId);
            if (committed.ok) {
              setStartAttemptId(null);
              setJob(completedRecoveredJob(activeJobId, committed.result.summary, job));
              selectRun(committed.result.summary.localImportId);
              setError(null);
              setMessage(
                'Импорт был зафиксирован до перезапуска worker и восстановлен из registry.',
              );
            } else if (committed.error.code === 'entity_not_found') {
              setStartAttemptId(null);
              setJob(job === null ? interruptedAttemptJob(activeJobId) : interruptedJob(job));
            } else {
              handleError(committed.error);
              throw new Error(committed.error.message);
            }
          }
          if (!(await loadItems())) throw new Error('Не удалось перечитать registry импортов.');
          const unresolvedMutationId = unresolvedMutationRef.current;
          if (unresolvedMutationId !== null) {
            selectRun(unresolvedMutationId);
            if (!(await loadDetail(unresolvedMutationId))) {
              throw new Error(
                'Не удалось подтвердить сохранённое изменение после перезапуска worker.',
              );
            }
          } else if (!dirty && selectedId !== null) {
            if (!(await loadDetail(selectedId))) {
              throw new Error('Не удалось перечитать выбранный импорт после перезапуска worker.');
            }
          }
        },
      }),
      [
        desktopApi,
        dirty,
        discardDraft,
        handleError,
        job,
        loadDetail,
        loadItems,
        selectRun,
        selectedId,
        startAttemptId,
      ],
    );

    return (
      <section className="project-surface r130sh-results" aria-labelledby="r130sh-results-title">
        <div className="section-heading r130sh-results__heading">
          <div>
            <Title id="r130sh-results-title" order={2}>
              Результаты R130SH
            </Title>
            <Text>
              Неизменяемые результаты стенда хранятся отдельно от сведений аналитического дела.
            </Text>
          </div>
          <Button
            onClick={() => requestTransition(dirty, () => startImport(false), discardDraft)}
            disabled={disabled || pending}
            loading={job?.state === 'queued'}
          >
            Импортировать результат R130SH
          </Button>
        </div>
        {message === null ? null : (
          <div className="feedback feedback--success" role="status">
            {message}
          </div>
        )}
        {error === null ? null : (
          <div className="feedback feedback--error" role="alert">
            <strong>{error.message}</strong>
            <span>{error.retryable ? 'Повторите операцию после проверки состояния.' : ''}</span>
          </div>
        )}
        {job === null ? null : (
          <ImportProgress job={job} onCancel={cancelImport} disabled={disabled} />
        )}
        {job?.state === 'failed' && job.typedError?.code === 'diagnostic_confirmation_required' ? (
          <div className="feedback feedback--warning" role="alert">
            <strong>Пакет содержит диагностический неполный результат</strong>
            <span>Он будет заметно помечен и по умолчанию исключён из будущих расчётов.</span>
            <Button
              size="compact-sm"
              onClick={() => requestTransition(dirty, () => startImport(true), discardDraft)}
            >
              Импортировать как диагностический
            </Button>
          </div>
        ) : null}
        <div className="r130sh-master-detail" aria-busy={loading}>
          <section className="r130sh-run-list" aria-labelledby="r130sh-run-list-title">
            <Title id="r130sh-run-list-title" order={3}>
              Импортированные запуски
            </Title>
            {items.length === 0 ? (
              <Text className="empty-copy">Импортированных запусков пока нет.</Text>
            ) : (
              <ul>
                {items.map((item) => (
                  <li key={item.localImportId}>
                    <button
                      type="button"
                      className={item.localImportId === selectedId ? 'is-selected' : undefined}
                      aria-current={item.localImportId === selectedId ? 'true' : undefined}
                      disabled={disabled || mutationPending}
                      onClick={() =>
                        requestTransition(dirty, () => selectRun(item.localImportId), discardDraft)
                      }
                    >
                      <span>{modeLabel(item.mode)}</span>
                      <strong>{item.runId}</strong>
                      <small>
                        {item.packageKind} · rev {item.exportRevision} ·{' '}
                        {integrityLabel(item.sourceIntegrity)}
                      </small>
                      <small>Source specimen: {item.sourceSpecimenId}</small>
                      <small>
                        {item.producerVersion} / {item.producerBuildId} ·{' '}
                        {formatDate(item.importedAtUtc)}
                      </small>
                      <small>
                        {item.technicalStatus ?? 'status не указан'} ·{' '}
                        {item.terminationReason ?? 'завершение не указано'} ·{' '}
                        {item.runValidity ?? 'validity не указана'} ·{' '}
                        {item.dataCompleteness ?? 'completeness не указана'}
                      </small>
                      <small>
                        {item.localSpecimenId === null
                          ? 'Привязка не разрешена'
                          : `Local Specimen: ${item.localSpecimenId}`}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="r130sh-run-detail" aria-label="Карточка импортированного запуска">
            {detail === null ? (
              <Text className="empty-copy">Выберите импортированный запуск.</Text>
            ) : (
              <RunDetail
                detail={detail}
                specimens={specimens}
                bindingSpecimenId={bindingSpecimenId}
                bindingReason={bindingReason}
                onBindingSpecimenChange={setBindingSpecimenId}
                onBindingReasonChange={setBindingReason}
                onSaveBinding={saveBinding}
                resolutionTargets={resolutionTargets}
                resolutionTargetId={resolutionTargetId}
                resolutionDecision={resolutionDecision}
                resolutionReason={resolutionReason}
                onResolutionTargetChange={(value) => {
                  setResolutionTargetId(value);
                  const nextTarget = resolutionTargets.find((item) => item.id === value);
                  if (
                    resolutionDecision === 'copied_to_analyst' &&
                    nextTarget?.copyAllowed !== true
                  )
                    setResolutionDecision('use_source');
                }}
                onResolutionDecisionChange={setResolutionDecision}
                onResolutionReasonChange={setResolutionReason}
                onSaveResolution={saveResolution}
                onVerifySource={() => requestTransition(dirty, verifySource, discardDraft)}
                onMaterializeReliabilityExecution={() =>
                  requestTransition(dirty, materializeReliabilityExecution, discardDraft)
                }
                boundSpecimenArchived={
                  boundSpecimen !== null && boundSpecimen.archivedAtUtc !== null
                }
                disabled={disabled || mutationPending || importPending || startAttemptId !== null}
              />
            )}
          </section>
        </div>
      </section>
    );
  },
);

function ImportProgress({
  job,
  onCancel,
  disabled,
}: {
  readonly job: RunPackageImportJob;
  readonly onCancel: () => void;
  readonly disabled: boolean;
}): React.JSX.Element {
  const ratio = job.totalBytes > 0 ? Math.round((job.completedBytes / job.totalBytes) * 100) : 0;
  return (
    <div className="import-progress" role={job.state === 'failed' ? 'alert' : 'status'}>
      <div>
        <strong>{importStateLabel(job.state)}</strong>
        <span>{importPhaseLabel(job.phase)}</span>
      </div>
      {job.totalBytes > 0 ? (
        <progress max={job.totalBytes} value={job.completedBytes} aria-label="Ход импорта" />
      ) : (
        <div role="progressbar" aria-label="Ход импорта" aria-valuetext="Подготовка" />
      )}
      <span>{job.totalBytes > 0 ? `${String(ratio)}%` : 'Подготовка'}</span>
      {job.typedError === null ? null : <span>{job.typedError.message}</span>}
      {terminalStates.has(job.state) || job.state === 'registering' ? null : (
        <Button size="compact-sm" variant="default" disabled={disabled} onClick={onCancel}>
          Отменить
        </Button>
      )}
    </div>
  );
}

interface RunDetailProps {
  readonly detail: ImportedRunDetail;
  readonly specimens: readonly SpecimenSummary[];
  readonly bindingSpecimenId: string | null;
  readonly bindingReason: string;
  readonly onBindingSpecimenChange: (value: string | null) => void;
  readonly onBindingReasonChange: (value: string) => void;
  readonly onSaveBinding: () => void;
  readonly resolutionTargets: readonly ResolutionTarget[];
  readonly resolutionTargetId: string | null;
  readonly resolutionDecision: 'use_source' | 'use_analyst' | 'copied_to_analyst';
  readonly resolutionReason: string;
  readonly onResolutionTargetChange: (value: string | null) => void;
  readonly onResolutionDecisionChange: (
    value: 'use_source' | 'use_analyst' | 'copied_to_analyst',
  ) => void;
  readonly onResolutionReasonChange: (value: string) => void;
  readonly onSaveResolution: () => void;
  readonly onVerifySource: () => void;
  readonly onMaterializeReliabilityExecution: () => void;
  readonly boundSpecimenArchived: boolean;
  readonly disabled: boolean;
}

function RunDetail(props: RunDetailProps): React.JSX.Element {
  const { detail } = props;
  const selectedResolution = props.resolutionTargets.find(
    (item) => item.id === props.resolutionTargetId,
  );
  return (
    <div className="r130sh-detail-sections">
      <header className="r130sh-detail-header">
        <div>
          <Text size="sm">{modeLabel(detail.summary.mode)}</Text>
          <Title order={3}>{detail.summary.runId}</Title>
        </div>
        <div className="r130sh-source-actions">
          <span className={`source-status source-status--${detail.summary.sourceIntegrity}`}>
            {integrityLabel(detail.summary.sourceIntegrity)}
          </span>
          <Button
            size="compact-sm"
            variant="default"
            disabled={props.disabled}
            onClick={props.onVerifySource}
          >
            Проверить источник
          </Button>
          <Button
            size="compact-sm"
            disabled={
              props.disabled ||
              detail.summary.sourceIntegrity !== 'verified' ||
              detail.summary.localSpecimenId === null ||
              props.boundSpecimenArchived
            }
            onClick={props.onMaterializeReliabilityExecution}
          >
            Создать исполнение M04A
          </Button>
        </div>
      </header>
      {detail.summary.packageKind === 'diagnostic_partial' ? (
        <div className="diagnostic-partial-banner" role="status">
          <strong>Диагностический неполный результат.</strong>
          <span>По умолчанию не используется в расчётах.</span>
          <span>
            Причины: {detail.projection.partialReasons.join('; ') || 'производитель не указал'}.
          </span>
          <span>
            Возобновление в R130SH: {detail.projection.resumeAvailable ? 'доступно' : 'недоступно'}.
          </span>
        </div>
      ) : null}
      <DetailSection title="Обзор">
        <DataList
          entries={[
            ['Package kind', detail.summary.packageKind],
            ['Package ID', detail.summary.packageId],
            ['Run ID', detail.summary.runId],
            ['Source specimen ID', detail.summary.sourceSpecimenId],
            ['Export revision', String(detail.summary.exportRevision)],
            ['Outer SHA-256', detail.summary.outerPackageSha256],
            ['Source snapshot SHA-256', detail.summary.sourceSnapshotSha256],
            ['Импортирован', formatDate(detail.summary.importedAtUtc)],
            ['Producer', `${detail.summary.producerVersion} / ${detail.summary.producerBuildId}`],
          ]}
        />
      </DetailSection>
      <DetailSection title="Исходный и фактический план">
        <div className="source-analyst-columns">
          <PlanBlock label="Исходный план" plan={detail.projection.originalPlan} />
          <PlanBlock label="Фактический план" plan={detail.projection.effectivePlan} />
        </div>
      </DetailSection>
      <DetailSection title="Результат и завершение">
        <DataList
          entries={[
            ['Технический статус', detail.summary.technicalStatus],
            ['Причина завершения', detail.summary.terminationReason],
            ['Результат образца', detail.summary.specimenOutcome],
            ['Достоверность', detail.summary.runValidity],
            ['Полнота', detail.summary.dataCompleteness],
            ['Начало', formatDate(detail.projection.startedAtUtc)],
            ['Окончание', formatDate(detail.projection.finishedAtUtc)],
          ]}
        />
      </DetailSection>
      <DetailSection title="Заказчик / колесо / образец">
        <DataList
          entries={[
            ['Заказчик', detail.projection.customerFullName],
            ['Адрес', detail.projection.customerAddress],
            ['Колесо', detail.projection.wheelFullName],
            ['Обозначение', detail.projection.wheelIdentifier],
            ['Рабочий диаметр, мм', detail.projection.workingDiameterMm],
          ]}
        />
      </DetailSection>
      <DetailSection title="Условия среды">
        <DataList
          entries={[
            ['Статус', detail.projection.environment.status],
            ['Температура, °C', detail.projection.environment.temperatureC],
            ['Влажность, %', detail.projection.environment.humidityPct],
            ['Давление, кПа', detail.projection.environment.pressureKpa],
            ['Отклонения', String(detail.projection.environment.deviationCount)],
            ['Подтвердил', detail.projection.environment.confirmationActor],
            ['Причина', detail.projection.environment.confirmationReason],
          ]}
        />
      </DetailSection>
      <DetailSection title="Provenance">
        <DataList
          entries={[
            ['Программа', detail.projection.provenance.producerName],
            ['Версия', detail.projection.provenance.appVersion],
            ['Build', detail.projection.provenance.buildId],
            ['Commit', detail.projection.provenance.gitCommit],
            ['Стенд', detail.projection.provenance.standName],
            ['Серийный номер', detail.projection.provenance.standSerialNumber],
            ['Источник времени', detail.projection.provenance.timeSource],
            ['Validation baseline', detail.summary.validationContractCommit],
          ]}
        />
      </DetailSection>
      <DetailSection title="События и измерения">
        <DataList
          entries={[
            ['Измерения', String(detail.projection.measurementCount)],
            ['Зачтённые измерения', String(detail.projection.acceptedMeasurementCount)],
            ['События', String(detail.projection.eventCount)],
            ['Осмотры', String(detail.projection.inspectionCount)],
            ['Поправки плана', String(detail.projection.amendmentCount)],
            ['Вложения', String(detail.projection.attachmentCount)],
          ]}
        />
        <Text size="sm">Полный physical stream остаётся внутри immutable archive.</Text>
      </DetailSection>
      <DetailSection title="Состав пакета">
        <ul className="package-inventory">
          {detail.inventory.map((item) => (
            <li key={item.path}>
              <strong>{item.path}</strong>
              <span>{formatBytes(item.sizeBytes)}</span>
              <code>{item.sha256}</code>
            </li>
          ))}
        </ul>
      </DetailSection>
      <DetailSection title="Расхождения и привязки">
        <div className="binding-editor">
          <Text>
            Source specimen <code>{detail.summary.sourceSpecimenId}</code> не объединяется по
            маркировке автоматически.
          </Text>
          <Select
            label="Local Specimen"
            clearable
            searchable
            value={props.bindingSpecimenId}
            data={props.specimens.map((item) => ({
              value: item.specimenId,
              label: `${item.identificationNumber} — ${item.wheelModelName}`,
            }))}
            disabled={props.disabled}
            onChange={props.onBindingSpecimenChange}
          />
          <Textarea
            label="Причина привязки"
            required
            maxLength={2_000}
            value={props.bindingReason}
            disabled={props.disabled}
            onChange={(event) => props.onBindingReasonChange(event.currentTarget.value)}
          />
          <Button
            onClick={props.onSaveBinding}
            disabled={props.disabled || props.bindingReason.trim() === ''}
          >
            Сохранить привязку
          </Button>
        </div>
        <div className="resolution-editor">
          <Select
            label="Существенное поле"
            clearable
            value={props.resolutionTargetId}
            data={props.resolutionTargets.map((item) => ({ value: item.id, label: item.label }))}
            disabled={props.disabled}
            onChange={props.onResolutionTargetChange}
          />
          {selectedResolution === undefined ? null : (
            <div className="source-analyst-columns">
              <ValueBlock label="Источник R130SH" value={selectedResolution.sourceValue} />
              <ValueBlock
                label="Сведения аналитического дела"
                value={selectedResolution.analystValue}
              />
            </div>
          )}
          <Select
            label="Решение"
            allowDeselect={false}
            value={props.resolutionDecision}
            data={[
              { value: 'use_source', label: 'Использовать source value в будущем анализе' },
              { value: 'use_analyst', label: 'Сохранить analyst value' },
              {
                value: 'copied_to_analyst',
                label: 'Скопировать только в пустое analyst field',
                disabled: selectedResolution?.copyAllowed !== true,
              },
            ]}
            disabled={props.disabled || selectedResolution === undefined}
            onChange={(value) => {
              if (
                value === 'use_source' ||
                value === 'use_analyst' ||
                value === 'copied_to_analyst'
              )
                props.onResolutionDecisionChange(value);
            }}
          />
          <Textarea
            label="Причина решения"
            maxLength={2_000}
            value={props.resolutionReason}
            disabled={props.disabled || selectedResolution === undefined}
            onChange={(event) => props.onResolutionReasonChange(event.currentTarget.value)}
          />
          <Button
            onClick={props.onSaveResolution}
            disabled={
              props.disabled ||
              selectedResolution === undefined ||
              (props.resolutionDecision === 'copied_to_analyst' &&
                !selectedResolution.copyAllowed) ||
              (props.resolutionDecision !== 'copied_to_analyst' &&
                selectedResolution.sourceValue !== selectedResolution.analystValue &&
                props.resolutionReason.trim() === '')
            }
          >
            Зафиксировать решение
          </Button>
          {detail.enrichmentResolutions.length === 0 ? null : (
            <div className="resolution-history">
              <Text fw={650}>Зафиксированные решения</Text>
              <ul>
                {detail.enrichmentResolutions.map((item) => (
                  <li key={item.resolutionId}>
                    <strong>{item.sourceField}</strong> → {item.targetEntityType}.{item.targetField}
                    : {item.decision}; {item.actor}, {formatDate(item.occurredAtUtc)}
                    {item.reason === '' ? '' : ` — ${item.reason}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </DetailSection>
      <DetailSection title="Findings validation">
        {detail.validationFindings.length === 0 ? (
          <Text>Validation findings отсутствуют.</Text>
        ) : (
          <ul>
            {detail.validationFindings.map((finding) => (
              <li key={`${finding.code}:${finding.location}`}>
                <strong>{finding.code}</strong> — {finding.message}
              </li>
            ))}
          </ul>
        )}
      </DetailSection>
    </div>
  );
}

function DetailSection({
  title,
  children,
}: {
  readonly title: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="r130sh-detail-section">
      <Title order={4}>{title}</Title>
      {children}
    </section>
  );
}

function DataList({
  entries,
}: {
  readonly entries: readonly (readonly [string, string | null])[];
}): React.JSX.Element {
  return (
    <dl className="source-data-list">
      {entries.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value === null || value === '' ? 'Не указано' : value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PlanBlock({
  label,
  plan,
}: {
  readonly label: string;
  readonly plan: ImportedRunDetail['projection']['originalPlan'];
}): React.JSX.Element {
  return (
    <div className="comparison-block">
      <strong>{label}</strong>
      <DataList
        entries={[
          ['Plan ID', plan.planId],
          ['Редакция', String(plan.planRevision)],
          ['Целевые циклы', plan.targetCycles === null ? null : String(plan.targetCycles)],
          ['Номинальная скорость, об/мин', plan.nominalRpm],
          ['Точные циклы', plan.requiredCyclesExact],
          ['Целевая длительность, с', plan.targetSteadyDurationS],
          ['Точная длительность, с', plan.requiredSteadyDurationSExact],
          ['Полная длительность, с', plan.totalDurationS],
          ['Политика нижней точки', plan.lowerPointPolicy],
        ]}
      />
    </div>
  );
}

function ValueBlock({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string | null;
}): React.JSX.Element {
  return (
    <div className="comparison-block">
      <strong>{label}</strong>
      <span>{value === null || value === '' ? 'Не указано' : value}</span>
    </div>
  );
}

function buildResolutionTargets(
  project: ProjectOverview,
  detail: ImportedRunDetail | null,
  customer: CustomerProfile | null,
  specimen: Specimen | null,
  wheel: WheelModel | null,
): readonly ResolutionTarget[] {
  if (detail === null) return [];
  const result: ResolutionTarget[] = [
    {
      id: 'customer-name',
      label: 'Заказчик: полное наименование',
      sourcePayloadPath: 'run-summary.json',
      sourceField: 'run_card.customer_name',
      sourceValue: detail.projection.customerFullName,
      analystValue: customer?.fullName ?? null,
      targetEntityType: 'customer_profile',
      targetEntityId: project.projectId,
      targetField: 'fullName',
      expectedTargetRevision: customer?.recordRevision ?? null,
      copyAllowed: customer === null,
    },
    {
      id: 'customer-legal-address',
      label: 'Заказчик: юридический адрес',
      sourcePayloadPath: 'run-summary.json',
      sourceField: 'run_card.customer_address',
      sourceValue: detail.projection.customerAddress,
      analystValue: customer?.legalAddress ?? null,
      targetEntityType: 'customer_profile',
      targetEntityId: project.projectId,
      targetField: 'legalAddress',
      expectedTargetRevision: customer?.recordRevision ?? null,
      copyAllowed: customer !== null && customer.legalAddress === '',
    },
    {
      id: 'customer-actual-address',
      label: 'Заказчик: фактический адрес',
      sourcePayloadPath: 'run-summary.json',
      sourceField: 'run_card.customer_address',
      sourceValue: detail.projection.customerAddress,
      analystValue: customer?.actualAddress ?? null,
      targetEntityType: 'customer_profile',
      targetEntityId: project.projectId,
      targetField: 'actualAddress',
      expectedTargetRevision: customer?.recordRevision ?? null,
      copyAllowed: customer !== null && customer.actualAddress === '',
    },
  ];
  if (wheel !== null) {
    result.push(
      {
        id: 'wheel-name',
        label: 'Колесо: полное наименование',
        sourcePayloadPath: 'run-summary.json',
        sourceField: 'run_card.wheel_full_name',
        sourceValue: detail.projection.wheelFullName,
        analystValue: wheel.fullName,
        targetEntityType: 'wheel_model',
        targetEntityId: wheel.wheelModelId,
        targetField: 'fullName',
        expectedTargetRevision: wheel.recordRevision,
        copyAllowed: false,
      },
      {
        id: 'wheel-designation',
        label: 'Колесо: обозначение',
        sourcePayloadPath: 'run-summary.json',
        sourceField: 'run_card.wheel_identifier',
        sourceValue: detail.projection.wheelIdentifier,
        analystValue: wheel.designation,
        targetEntityType: 'wheel_model',
        targetEntityId: wheel.wheelModelId,
        targetField: 'designation',
        expectedTargetRevision: wheel.recordRevision,
        copyAllowed: wheel.designation === '',
      },
      {
        id: 'wheel-nominal-speed',
        label: 'Колесо: номинальная скорость',
        sourcePayloadPath: 'plan/original.json',
        sourceField: 'source_values.nominal_rpm',
        sourceValue: detail.projection.originalPlan.nominalRpm,
        analystValue: wheel.nominalSpeedRpm === null ? null : String(wheel.nominalSpeedRpm),
        targetEntityType: 'wheel_model',
        targetEntityId: wheel.wheelModelId,
        targetField: 'nominalSpeedRpm',
        expectedTargetRevision: wheel.recordRevision,
        copyAllowed: wheel.nominalSpeedRpm === null,
      },
    );
  }
  if (specimen !== null) {
    result.push(
      {
        id: 'specimen-marking',
        label: 'Образец: маркировка',
        sourcePayloadPath: 'run-summary.json',
        sourceField: 'sample_label',
        sourceValue: detail.projection.sampleLabel,
        analystValue: specimen.marking,
        targetEntityType: 'specimen',
        targetEntityId: specimen.specimenId,
        targetField: 'marking',
        expectedTargetRevision: specimen.recordRevision,
        copyAllowed: specimen.marking === '',
      },
      {
        id: 'specimen-diameter',
        label: 'Образец: рабочий диаметр',
        sourcePayloadPath: 'run-summary.json',
        sourceField: 'run_card.working_diameter_mm',
        sourceValue: detail.projection.workingDiameterMm,
        analystValue: specimen.workingDiameterMm,
        targetEntityType: 'specimen',
        targetEntityId: specimen.specimenId,
        targetField: 'workingDiameterMm',
        expectedTargetRevision: specimen.recordRevision,
        copyAllowed: specimen.workingDiameterMm === null,
      },
    );
  }
  return result;
}

function modeLabel(value: ImportedRunSummary['mode']): string {
  return { pmn: 'ПМН', rpt: 'РПТ', rbd: 'РБД' }[value];
}

function integrityLabel(value: ImportedRunSummary['sourceIntegrity']): string {
  return {
    verified: 'Источник проверен',
    missing: 'Источник отсутствует',
    modified: 'Источник изменён',
    verification_error: 'Ошибка проверки источника',
  }[value];
}

function importStateLabel(value: RunPackageImportJob['state']): string {
  return {
    queued: 'Импорт поставлен в очередь',
    validating: 'Проверка исходного пакета',
    copying: 'Копирование в аналитическое дело',
    revalidating: 'Повторная проверка managed copy',
    registering: 'Фиксация в проекте — отмена недоступна',
    completed: 'Импорт завершён',
    failed: 'Импорт не выполнен',
    cancelling: 'Отмена импорта',
    cancelled: 'Импорт отменён',
  }[value];
}

function importPhaseLabel(value: RunPackageImportJob['phase']): string {
  return {
    queued: 'Подготовка',
    source_validation: 'Validation source archive',
    streaming_copy: 'Streaming copy + SHA-256',
    staged_validation: 'Validation staged copy',
    database_registration: 'Registry + projection + audit',
    terminal: 'Terminal state',
  }[value];
}

function pollDelay(state: RunPackageImportJob['state']): number {
  return state === 'queued' ? 120 : state === 'registering' ? 80 : 250;
}

function mayHaveStartedImport(error: DesktopError): boolean {
  return error.code === 'worker_unavailable' || error.code === 'timeout';
}

function mayHaveCommittedMutation(error: DesktopError): boolean {
  return error.code === 'worker_unavailable' || error.code === 'timeout';
}

function isTransientPollError(error: DesktopError): boolean {
  return (
    error.retryable ||
    error.code === 'operation_in_progress' ||
    error.code === 'worker_unavailable' ||
    error.code === 'timeout'
  );
}

function completedRecoveredJob(
  jobId: string,
  importedRun: ImportedRunSummary,
  previous?: RunPackageImportJob | null,
): RunPackageImportJob {
  const totalBytes = previous?.totalBytes ?? importedRun.outerSizeBytes;
  const totalEntries = previous?.totalEntries ?? 0;
  return {
    jobId,
    state: 'completed',
    phase: 'terminal',
    completedBytes: totalBytes,
    totalBytes,
    completedEntries: totalEntries,
    totalEntries,
    startedAtUtc: previous?.startedAtUtc ?? null,
    finishedAtUtc: new Date().toISOString(),
    result: { disposition: 'existing', importedRun },
    typedError: null,
  };
}

function interruptedJob(previous: RunPackageImportJob): RunPackageImportJob {
  return {
    ...previous,
    state: 'failed',
    phase: 'terminal',
    finishedAtUtc: new Date().toISOString(),
    result: null,
    typedError: {
      code: 'interrupted',
      message: 'Import job был прерван до фиксации; повторный импорт безопасен.',
      retryable: true,
    },
  };
}

function interruptedAttemptJob(jobId: string): RunPackageImportJob {
  return interruptedJob({
    jobId,
    state: 'queued',
    phase: 'queued',
    completedBytes: 0,
    totalBytes: 0,
    completedEntries: 0,
    totalEntries: 0,
    startedAtUtc: null,
    finishedAtUtc: null,
    result: null,
    typedError: null,
  });
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function formatDate(value: string | null): string | null {
  if (value === null) return null;
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  );
}

function formatBytes(value: number): string {
  return new Intl.NumberFormat('ru-RU', { style: 'unit', unit: 'kilobyte' }).format(value / 1024);
}

function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Не удалось получить состояние импортированных результатов.',
    details: {},
    retryable: true,
  };
}
