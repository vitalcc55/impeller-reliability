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

import {
  MAX_DATASET_CANDIDATES,
  addDatasetCandidate,
  hasUniqueDatasetMembers,
  observationResponseMatches,
  replaceDatasetCandidateVersion,
  type DatasetCandidateDecision,
} from './reliability-dataset-draft';

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
    Readonly<Record<string, DatasetCandidateDecision>>
  >({});
  const [candidateVersions, setCandidateVersions] = useState<
    Readonly<Record<string, ReliabilityObservationVersion>>
  >({});
  const [candidateLatestVersions, setCandidateLatestVersions] = useState<
    Readonly<
      Record<
        string,
        {
          readonly observationVersionId: string;
          readonly versionNumber: number;
          readonly classification: ReliabilityObservationVersion['classification'];
        }
      >
    >
  >({});
  const [datasetTitle, setDatasetTitle] = useState('Выборка наработки');
  const [datasetMethod, setDatasetMethod] = useState<'rbd' | 'rpt' | null>(null);
  const [datasetMetricKind, setDatasetMetricKind] = useState<
    'rbd_steady_rotation_time' | 'rpt_start_stop_cycles' | null
  >(null);
  const [datasetMetricUnit, setDatasetMetricUnit] = useState<'hours' | 'count' | null>(null);
  const [populationBasis, setPopulationBasis] = useState('');
  const [methodologyBasis, setMethodologyBasis] = useState('');
  const [comparabilityBasis, setComparabilityBasis] = useState('');
  const [datasetReason, setDatasetReason] = useState('');
  const [datasetId, setDatasetId] = useState<string>(() => crypto.randomUUID());
  const [datasetVersionId, setDatasetVersionId] = useState<string>(() => crypto.randomUUID());
  const [datasetDirty, setDatasetDirty] = useState(false);
  const [datasets, setDatasets] = useState<readonly ReliabilityDatasetVersion[]>([]);
  const [datasetCursor, setDatasetCursor] = useState<string | null>(null);
  const [selectedDataset, setSelectedDataset] = useState<ReliabilityDatasetVersion | null>(null);
  const [committedDatasetVersionId, setCommittedDatasetVersionId] = useState<string | null>(null);
  const [observationWriteUnresolved, setObservationWriteUnresolved] = useState(false);
  const [datasetWriteUnresolved, setDatasetWriteUnresolved] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<DesktopError | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const pendingRef = useRef<Promise<void> | null>(null);
  const busyRef = useRef<string | null>(null);
  const operationRef = useRef(0);
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
      setCandidateVersions({});
      setCandidateLatestVersions({});
      setSelectedDataset(null);
      setCommittedDatasetVersionId(null);
      setObservationWriteUnresolved(false);
      setDatasetWriteUnresolved(false);
      setDatasetId(crypto.randomUUID());
      setDatasetVersionId(crypto.randomUUID());
      setDatasetDirty(false);
      setDatasetTitle('Выборка наработки');
      setDatasetMethod(null);
      setDatasetMetricKind(null);
      setDatasetMetricUnit(null);
      setPopulationBasis('');
      setMethodologyBasis('');
      setComparabilityBasis('');
      setDatasetReason('');
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
        if (detail.result.datasetVersionId !== item.latestVersionId) {
          setError(contractError());
          return false;
        }
        loadedDatasets.push(detail.result);
      }
      setDatasets(loadedDatasets);
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
    if (busyRef.current !== null) return pendingRef.current ?? Promise.resolve();
    const operation = ++operationRef.current;
    busyRef.current = key;
    setBusy(key);
    setError(null);
    setMessage(null);
    const pending = action()
      .catch(() => setError(unavailableError()))
      .finally(() => {
        if (operation === operationRef.current) {
          pendingRef.current = null;
          busyRef.current = null;
          setBusy(null);
        }
      });
    pendingRef.current = pending;
    return pending;
  };

  const selectExecution = async (executionId: string): Promise<void> => {
    if (observationDirty) return;
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

  const addCurrentObservationCandidate = async (
    item: ReliabilityExecutionSummary,
  ): Promise<void> => {
    if (item.currentObservationVersionId === null) return;
    const proposal = addDatasetCandidate(
      candidateDecisions,
      item.executionId,
      item.currentObservationVersionId,
    );
    if (proposal.limitReached) {
      setError({
        code: 'validation_error',
        message: `В одной версии выборки допускается не более ${String(MAX_DATASET_CANDIDATES)} рассмотренных наблюдений. Удалите ненужный кандидат перед добавлением нового.`,
        details: {},
        retryable: false,
      });
      return;
    }
    if (proposal.decisions === candidateDecisions) return;
    const selectionRevision = selectionRef.current;
    const detail = await desktopApi.reliabilityObservation.getVersion(
      item.currentObservationVersionId,
    );
    if (selectionRevision !== selectionRef.current) return;
    if (!detail.ok) return setError(detail.error);
    if (
      !observationResponseMatches(detail.result, item.currentObservationVersionId, item.executionId)
    )
      return setError(contractError());
    setCandidateVersions((current) => ({
      ...current,
      [detail.result.observationVersionId]: detail.result,
    }));
    setCandidateLatestVersions((current) => ({
      ...current,
      [item.executionId]: {
        observationVersionId: detail.result.observationVersionId,
        versionNumber: detail.result.versionNumber,
        classification: detail.result.classification,
      },
    }));
    setCandidateDecisions(proposal.decisions);
    setDatasetDirty(true);
  };

  const replaceCandidateWithLatest = async (
    executionId: string,
    latestObservationVersionId: string,
  ): Promise<void> => {
    const selectionRevision = selectionRef.current;
    const detail = await desktopApi.reliabilityObservation.getVersion(latestObservationVersionId);
    if (selectionRevision !== selectionRef.current) return;
    if (!detail.ok) return setError(detail.error);
    if (!observationResponseMatches(detail.result, latestObservationVersionId, executionId))
      return setError(contractError());
    setCandidateVersions((current) => ({
      ...current,
      [detail.result.observationVersionId]: detail.result,
    }));
    setCandidateDecisions((current) =>
      replaceDatasetCandidateVersion(current, executionId, latestObservationVersionId),
    );
    setDatasetDirty(true);
  };

  const selectDatasetVersion = useCallback(
    async (item: ReliabilityDatasetVersion): Promise<boolean> => {
      const selectionRevision = ++selectionRef.current;
      if (!hasUniqueDatasetMembers(item.members)) {
        setError(contractError());
        return false;
      }
      const exactVersions: Record<string, ReliabilityObservationVersion> = {};
      const latestVersions: Record<
        string,
        {
          observationVersionId: string;
          versionNumber: number;
          classification: ReliabilityObservationVersion['classification'];
        }
      > = {};
      for (const member of item.members) {
        const detail = await desktopApi.reliabilityObservation.getVersion(
          member.observationVersionId,
        );
        if (selectionRevision !== selectionRef.current) return false;
        if (!detail.ok) {
          setError(detail.error);
          return false;
        }
        if (
          !observationResponseMatches(
            detail.result,
            member.observationVersionId,
            member.executionId,
          )
        ) {
          setError(contractError());
          return false;
        }
        exactVersions[detail.result.observationVersionId] = detail.result;
        const versionList = await desktopApi.reliabilityObservation.listVersions(
          member.executionId,
        );
        if (selectionRevision !== selectionRef.current) return false;
        if (!versionList.ok) {
          setError(versionList.error);
          return false;
        }
        const latest = versionList.result[0];
        if (latest === undefined || latest.executionId !== member.executionId) {
          setError(contractError());
          return false;
        }
        latestVersions[member.executionId] = {
          observationVersionId: latest.observationVersionId,
          versionNumber: latest.versionNumber,
          classification: latest.classification,
        };
      }
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
      setCandidateVersions(exactVersions);
      setCandidateLatestVersions(latestVersions);
      setCandidateDecisions(
        Object.fromEntries(
          item.members.map((member) => [
            member.executionId,
            {
              observationVersionId: member.observationVersionId,
              decision: member.decision,
              reason: member.inclusionReason,
            },
          ]),
        ),
      );
      setCommittedDatasetVersionId(item.datasetVersionId);
      setDatasetDirty(false);
      return true;
    },
    [desktopApi],
  );

  const saveObservation = (): Promise<void> =>
    runPending('observation-save', async () => {
      if (execution === null || observationDraft.documentId === null) return;
      setObservationWriteUnresolved(true);
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
      if (!result.ok) {
        if (!result.error.retryable) setObservationWriteUnresolved(false);
        return setError(result.error);
      }
      if (
        !observationResponseMatches(
          result.result.version,
          observationVersionId,
          execution.executionId,
        )
      )
        return setError(contractError());
      setObservationWriteUnresolved(false);
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
      setCandidateLatestVersions((current) => ({
        ...current,
        [execution.executionId]: {
          observationVersionId: result.result.version.observationVersionId,
          versionNumber: result.result.version.versionNumber,
          classification: result.result.version.classification,
        },
      }));
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
      setDatasetWriteUnresolved(true);
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
      if (!result.ok) {
        if (!result.error.retryable) setDatasetWriteUnresolved(false);
        return setError(result.error);
      }
      if (
        result.result.version.datasetVersionId !== datasetVersionId ||
        result.result.version.wheelModelId !== wheelModelId ||
        !hasUniqueDatasetMembers(result.result.version.members)
      )
        return setError(contractError());
      setDatasetWriteUnresolved(false);
      setSelectedDataset(result.result.version);
      setCommittedDatasetVersionId(result.result.version.datasetVersionId);
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
        if (selectedDataset !== null && !hasUniqueDatasetMembers(selectedDataset.members)) {
          setError(contractError());
          return;
        }
        setObservationDraft(
          selectedVersion === null ? emptyObservation : versionToDraft(selectedVersion),
        );
        setObservationVersionId(crypto.randomUUID());
        setObservationDirty(false);
        if (selectedDataset === null) {
          setDatasetTitle('Выборка наработки');
          setDatasetMethod(null);
          setDatasetMetricKind(null);
          setDatasetMetricUnit(null);
          setPopulationBasis('');
          setMethodologyBasis('');
          setComparabilityBasis('');
          setDatasetReason('');
          setCandidateDecisions({});
          setCandidateVersions({});
          setCandidateLatestVersions({});
        } else {
          setDatasetTitle(selectedDataset.title);
          setDatasetMethod(selectedDataset.method);
          setDatasetMetricKind(selectedDataset.metricKind);
          setDatasetMetricUnit(selectedDataset.metricUnit);
          setPopulationBasis(selectedDataset.populationBasis);
          setMethodologyBasis(selectedDataset.methodologyBasis);
          setComparabilityBasis(selectedDataset.comparabilityBasis);
          setDatasetReason(selectedDataset.decisionReason);
          setCandidateDecisions(
            Object.fromEntries(
              selectedDataset.members.map((member) => [
                member.executionId,
                {
                  observationVersionId: member.observationVersionId,
                  decision: member.decision,
                  reason: member.inclusionReason,
                },
              ]),
            ),
          );
        }
        setDatasetVersionId(crypto.randomUUID());
        setDatasetDirty(false);
      },
      waitForPendingSave: async () => pendingRef.current ?? Promise.resolve(),
      verifyAfterReattach: async () => {
        let reconciled = false;
        if (observationWriteUnresolved) {
          const observationProbe =
            await desktopApi.reliabilityObservation.getVersion(observationVersionId);
          if (!observationProbe.ok) {
            setError(
              observationProbe.error.code === 'entity_not_found'
                ? unresolvedWriteError('интерпретации')
                : observationProbe.error,
            );
            return false;
          }
          const restored = observationProbe.result;
          if (
            execution === null ||
            !observationResponseMatches(restored, observationVersionId, execution.executionId)
          ) {
            setError(contractError());
            return false;
          }
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
          setObservationWriteUnresolved(false);
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
          setCandidateLatestVersions((current) => ({
            ...current,
            [restored.executionId]: {
              observationVersionId: restored.observationVersionId,
              versionNumber: restored.versionNumber,
              classification: restored.classification,
            },
          }));
          reconciled = true;
        }
        const datasetProbeId = datasetWriteUnresolved
          ? datasetVersionId
          : datasetDirty
            ? null
            : committedDatasetVersionId;
        if (datasetProbeId !== null) {
          const datasetProbe = await desktopApi.reliabilityDataset.getVersion(datasetProbeId);
          if (!datasetProbe.ok) {
            setError(
              datasetProbe.error.code === 'entity_not_found'
                ? unresolvedWriteError('выборки')
                : datasetProbe.error,
            );
            return false;
          }
          const restored = datasetProbe.result;
          if (
            restored.datasetVersionId !== datasetProbeId ||
            restored.wheelModelId !== wheelModelId ||
            !hasUniqueDatasetMembers(restored.members)
          ) {
            setError(contractError());
            return false;
          }
          if (!(await selectDatasetVersion(restored))) return false;
          setDatasets((current) => [
            restored,
            ...current.filter((item) => item.datasetVersionId !== restored.datasetVersionId),
          ]);
          setDatasetWriteUnresolved(false);
          reconciled = true;
        }
        if (reconciled || dirty) return true;
        return wheelModelId === null ? true : loadWheel(wheelModelId);
      },
    }),
    [
      committedDatasetVersionId,
      datasetVersionId,
      datasetDirty,
      datasetWriteUnresolved,
      desktopApi,
      dirty,
      execution,
      loadWheel,
      observationVersionId,
      observationWriteUnresolved,
      selectDatasetVersion,
      selectedDataset,
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
  const observationHistoryCursor = versions.at(-1)?.previousVersionId ?? null;
  const datasetDraftLocked = disabled || busy !== null || datasetWriteUnresolved;

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
      {observationWriteUnresolved || datasetWriteUnresolved ? (
        <div className="feedback feedback--warning" role="status">
          Ответ на сохранение не подтверждён. Поля отправленного черновика заблокированы; повторите
          сохранение с тем же идентификатором или перезапустите ядро для точной сверки.
        </div>
      ) : null}
      <div className="reliability-master-detail">
        <section aria-labelledby="execution-list-title">
          <Title id="execution-list-title" order={3}>
            Исполнения
          </Title>
          {executions.length === 0 && busy !== 'wheel-load' ? (
            <Text size="sm">Для выбранной модели пока нет сохранённых исполнений.</Text>
          ) : null}
          <div className="reliability-record-list">
            {executions.map((item) => {
              const alreadySelected = candidateDecisions[item.executionId] !== undefined;
              return (
                <div key={item.executionId} className="reliability-record-entry">
                  <button
                    type="button"
                    className="reliability-record"
                    aria-pressed={execution?.executionId === item.executionId}
                    disabled={disabled || busy !== null || observationDirty}
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
                  {item.currentObservationVersionId === null ? null : (
                    <Button
                      size="compact-sm"
                      variant="subtle"
                      aria-label={`${alreadySelected ? 'Добавлено в черновик' : 'Добавить в выборку'}: ${item.sourceRunId}, версия ${String(item.currentObservationVersionNumber)}`}
                      disabled={datasetDraftLocked || alreadySelected}
                      onClick={() =>
                        void runPending('candidate-add', () => addCurrentObservationCandidate(item))
                      }
                    >
                      {alreadySelected ? 'Добавлено в черновик' : 'Добавить в выборку'}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
          {executionCursor === null || wheelModelId === null ? null : (
            <Button
              variant="subtle"
              disabled={busy !== null || observationDirty}
              onClick={() =>
                void runPending('more-executions', async () => {
                  const result = await desktopApi.reliabilityExecution.listPage(
                    wheelModelId,
                    executionCursor,
                    25,
                  );
                  if (!result.ok) return setError(result.error);
                  setExecutions((current) => [...current, ...result.result.items]);
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
                    if (detail.result.datasetVersionId !== item.latestVersionId)
                      return setError(contractError());
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
                <fieldset
                  className="reliability-observation-fields"
                  disabled={disabled || busy !== null || observationWriteUnresolved}
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
                                  : observationDraft.failureIds.filter(
                                      (id) => id !== item.failureId,
                                    ),
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
                    {observationWriteUnresolved
                      ? 'Повторить сохранение версии'
                      : 'Сохранить новую версию'}
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
              {observationHistoryCursor === null ? null : (
                <Button
                  variant="subtle"
                  disabled={dirty || busy !== null}
                  onClick={() =>
                    void runPending('observation-history', async () => {
                      const older: ReliabilityObservationVersion[] = [];
                      let versionId: string | null = observationHistoryCursor;
                      while (versionId !== null && older.length < 50) {
                        const detail =
                          await desktopApi.reliabilityObservation.getVersion(versionId);
                        if (!detail.ok) return setError(detail.error);
                        if (
                          !observationResponseMatches(
                            detail.result,
                            versionId,
                            execution.executionId,
                          )
                        )
                          return setError(contractError());
                        older.push(detail.result);
                        versionId = detail.result.previousVersionId;
                      }
                      setVersions((current) => [
                        ...current,
                        ...older.filter(
                          (candidate) =>
                            !current.some(
                              (item) =>
                                item.observationVersionId === candidate.observationVersionId,
                            ),
                        ),
                      ]);
                    })
                  }
                >
                  Показать предыдущие версии интерпретации
                </Button>
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
            disabled={datasetDraftLocked}
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
            disabled={datasetDraftLocked}
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
            disabled={datasetDraftLocked}
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
            disabled={datasetDraftLocked}
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
            placeholder="Опишите, какие рабочие колёса и условия относятся к этой выборке"
            disabled={datasetDraftLocked}
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
            placeholder="Укажите документ, раздел и почему методика применима"
            disabled={datasetDraftLocked}
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
            placeholder="Зафиксируйте проверенные общие условия и существенные различия"
            disabled={datasetDraftLocked}
            value={comparabilityBasis}
            maxLength={2000}
            onChange={(event) => {
              setComparabilityBasis(event.currentTarget.value);
              setDatasetDirty(true);
            }}
          />
        </div>
        <div className="reliability-candidates">
          {Object.entries(candidateDecisions).map(([executionId, decision]) => {
            const item = executions.find((entry) => entry.executionId === executionId);
            const persistedMember = selectedDataset?.members.find(
              (member) => member.executionId === executionId,
            );
            const exactVersion = candidateVersions[decision.observationVersionId];
            const sourceLabel = item?.sourceRunId ?? persistedMember?.sourceRunId ?? executionId;
            const latestFromPage =
              item?.currentObservationVersionId === null ||
              item?.currentObservationVersionId === undefined ||
              item.currentObservationVersionNumber === null ||
              item.currentClassification === null
                ? undefined
                : {
                    observationVersionId: item.currentObservationVersionId,
                    versionNumber: item.currentObservationVersionNumber,
                    classification: item.currentClassification,
                  };
            const latestVersion = candidateLatestVersions[executionId] ?? latestFromPage;
            const latestVersionId = latestVersion?.observationVersionId ?? null;
            const hasNewerVersion =
              latestVersionId !== null && latestVersionId !== decision.observationVersionId;
            return (
              <div key={executionId} className="reliability-candidate">
                <div>
                  <Select
                    label={`${methodLabel(item?.method ?? selectedDataset?.method ?? 'rbd')} · ${sourceLabel}`}
                    data={[
                      { value: 'pending', label: 'Решение не принято' },
                      { value: 'included', label: 'Включить' },
                      { value: 'excluded', label: 'Исключить' },
                    ]}
                    value={decision.decision}
                    required
                    disabled={datasetDraftLocked}
                    onChange={(value) => {
                      if (value === null) return;
                      setCandidateDecisions((current) => ({
                        ...current,
                        [executionId]: {
                          ...decision,
                          decision: value,
                          reason: '',
                        },
                      }));
                      setDatasetDirty(true);
                    }}
                  />
                  <Text size="xs" c="dimmed">
                    {exactVersion === undefined
                      ? `Выбрана версия ${decision.observationVersionId}; сведения версии не загружены.`
                      : `Выбрана версия ${String(exactVersion.versionNumber)}: ${classificationLabel(exactVersion.classification)}; ${observationMetricLabel(exactVersion)}.`}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {item?.packageKind === 'diagnostic_partial'
                      ? 'Политика life_metric_exact_v1: diagnostic_partial не включается.'
                      : item?.method === 'pmn'
                        ? 'Политика life_metric_exact_v1: ПМН не относится к выборке наработки.'
                        : 'Endpoint, metric и применимость Python повторно проверит при фиксации.'}
                  </Text>
                  {hasNewerVersion ? (
                    <Button
                      size="compact-sm"
                      variant="subtle"
                      aria-label={`Заменить ${sourceLabel}: версия ${String(exactVersion?.versionNumber ?? decision.observationVersionId)} на версию ${String(latestVersion?.versionNumber ?? latestVersionId)}`}
                      disabled={datasetDraftLocked || latestVersionId === null}
                      onClick={() =>
                        latestVersionId === null
                          ? undefined
                          : void runPending('candidate-replace', () =>
                              replaceCandidateWithLatest(executionId, latestVersionId),
                            )
                      }
                    >
                      Доступна новая версия — заменить явно
                    </Button>
                  ) : null}
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
                  disabled={datasetDraftLocked || decision.decision === 'pending'}
                  maxLength={2000}
                  onChange={(event) => {
                    const reason = event.currentTarget.value;
                    setCandidateDecisions((current) => ({
                      ...current,
                      [executionId]: { ...decision, reason },
                    }));
                    setDatasetDirty(true);
                  }}
                />
                <Button
                  size="compact-sm"
                  variant="subtle"
                  aria-label={`Убрать из черновика: ${sourceLabel}, версия ${String(exactVersion?.versionNumber ?? decision.observationVersionId)}`}
                  disabled={datasetDraftLocked}
                  onClick={() => {
                    setCandidateDecisions((current) =>
                      Object.fromEntries(
                        Object.entries(current).filter(
                          ([candidateId]) => candidateId !== executionId,
                        ),
                      ),
                    );
                    setDatasetDirty(true);
                  }}
                >
                  Убрать из черновика
                </Button>
              </div>
            );
          })}
        </div>
        <Textarea
          label="Основание версии выборки"
          required
          placeholder="Опишите, почему зафиксирован именно этот состав и решения"
          disabled={datasetDraftLocked}
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
              datasetTitle.trim() === '' ||
              populationBasis.trim() === '' ||
              methodologyBasis.trim() === '' ||
              comparabilityBasis.trim() === '' ||
              datasetReason.trim() === '' ||
              Object.values(candidateDecisions).some((item) => item.decision === 'pending') ||
              Object.values(candidateDecisions).some((item) => item.reason.trim() === '')
            }
            onClick={() => void saveDataset()}
          >
            {datasetWriteUnresolved
              ? 'Повторить сохранение версии выборки'
              : 'Зафиксировать новую версию выборки'}
          </Button>
        </Group>
        <div className="reliability-version-strip" aria-label="Сохранённые выборки">
          {datasets.map((item) => (
            <button
              key={item.datasetVersionId}
              type="button"
              aria-pressed={selectedDataset?.datasetVersionId === item.datasetVersionId}
              disabled={dirty || busy !== null}
              onClick={() =>
                void runPending('dataset-detail', async () => {
                  await selectDatasetVersion(item);
                })
              }
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
                  if (
                    detail.result.datasetVersionId !== versionId ||
                    detail.result.wheelModelId !== wheelModelId ||
                    !hasUniqueDatasetMembers(detail.result.members)
                  )
                    return setError(contractError());
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

function observationMetricLabel(version: ReliabilityObservationVersion): string {
  if (version.metricKind === null || version.metricUnit === null || version.lowerValue === null)
    return 'числовое значение отсутствует';
  const unit = version.metricUnit === 'hours' ? 'ч' : 'циклов';
  if (version.endpointKind === 'interval' && version.upperValue !== null)
    return `${version.lowerValue}–${version.upperValue} ${unit}`;
  return `${version.lowerValue} ${unit}`;
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

function contractError(): DesktopError {
  return {
    code: 'contract_error',
    message: 'Полученные сведения не относятся к выбранному исполнению. Обновите раздел.',
    details: {},
    retryable: true,
  };
}

function unresolvedWriteError(entityLabel: string): DesktopError {
  return {
    code: 'revision_conflict',
    message: `Не удалось подтвердить сохранение ${entityLabel} после перезапуска. Черновик и идентификатор повтора сохранены; повторите сверку или сохранение.`,
    details: {},
    retryable: true,
  };
}
