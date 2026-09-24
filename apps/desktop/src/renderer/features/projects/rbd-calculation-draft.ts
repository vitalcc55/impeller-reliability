import type {
  CaseDocumentSummary,
  RbdCalculationCreateCommand,
  RbdPlanSource,
} from '@impeller-reliability/contracts';

export const RBD_INPUT_FIELDS = [
  'nominal_rpm',
  'base_cycles',
  'reserve_factor',
  'acceleration_duration_s',
  'deceleration_duration_s',
] as const;

export type RbdInputField = (typeof RBD_INPUT_FIELDS)[number];

export const RBD_FIELD_LABELS: Record<RbdInputField, { label: string; unit: string }> = {
  nominal_rpm: { label: 'Номинальная частота nP', unit: 'об/мин' },
  base_cycles: { label: 'Базовое число циклов N0', unit: 'циклов' },
  reserve_factor: { label: 'Коэффициент запаса k1', unit: 'безразмерный' },
  acceleration_duration_s: { label: 'Время разгона tP1', unit: 'с' },
  deceleration_duration_s: { label: 'Время торможения tT1', unit: 'с' },
};

export interface RbdFieldDraft {
  readonly origin: 'source' | 'manual' | null;
  readonly manualValue: string;
  readonly basis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
}

export interface RbdFailureDraft {
  readonly applicability:
    | 'unavailable'
    | 'exact_supported'
    | 'interval_endpoint'
    | 'right_censored'
    | 'unsupported_phase'
    | 'ambiguous_pauses';
  readonly durationToFailureS: string;
  readonly basis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
}

export interface RbdCalculationDraft {
  readonly fields: Record<RbdInputField, RbdFieldDraft>;
  readonly failure: RbdFailureDraft;
  readonly reason: string;
}

const emptyField = (): RbdFieldDraft => ({
  origin: null,
  manualValue: '',
  basis: '',
  documentId: null,
  documentLocator: '',
});

export function emptyCalculationDraft(): RbdCalculationDraft {
  return {
    fields: {
      nominal_rpm: emptyField(),
      base_cycles: emptyField(),
      reserve_factor: emptyField(),
      acceleration_duration_s: emptyField(),
      deceleration_duration_s: emptyField(),
    },
    failure: {
      applicability: 'unavailable',
      durationToFailureS: '',
      basis: '',
      documentId: null,
      documentLocator: '',
    },
    reason: '',
  };
}

export function sourceValueForField(source: RbdPlanSource, field: RbdInputField): string | null {
  switch (field) {
    case 'nominal_rpm':
      return source.sourceValues.nominalRpm;
    case 'base_cycles':
      return source.sourceValues.baseCycles;
    case 'reserve_factor':
      return source.sourceValues.reserveFactor;
    case 'acceleration_duration_s':
      return source.sourceValues.accelerationDurationS;
    case 'deceleration_duration_s':
      return source.sourceValues.decelerationDurationS;
  }
}

interface CalculationIds {
  readonly analysisInputSnapshotId: string;
  readonly calculationSnapshotId: string;
}

interface BuildResult {
  readonly command: RbdCalculationCreateCommand | null;
  readonly error: string | null;
}

export function buildCalculationCommand(
  source: RbdPlanSource,
  draft: RbdCalculationDraft,
  documents: readonly CaseDocumentSummary[],
  ids: CalculationIds,
): BuildResult {
  if (draft.reason.trim() === '')
    return { command: null, error: 'Укажите основание создания расчётного снимка.' };
  const selections: RbdCalculationCreateCommand['selections'] = [];
  for (const field of RBD_INPUT_FIELDS) {
    const choice = draft.fields[field];
    if (choice.origin === null)
      return { command: null, error: `Выберите происхождение: ${RBD_FIELD_LABELS[field].label}.` };
    if (choice.origin === 'source') {
      if (sourceValueForField(source, field) === null)
        return {
          command: null,
          error: `${RBD_FIELD_LABELS[field].label}: в выбранной редакции плана значение отсутствует.`,
        };
      selections.push({ field, origin: 'source', manualValue: null, basis: '', evidence: null });
      continue;
    }
    if (choice.manualValue.trim() === '' || choice.basis.trim() === '')
      return {
        command: null,
        error: `${RBD_FIELD_LABELS[field].label}: укажите ручное значение и основание.`,
      };
    const document = documents.find((item) => item.caseDocumentId === choice.documentId);
    if (choice.documentId !== null && document === undefined)
      return { command: null, error: `${RBD_FIELD_LABELS[field].label}: документ недоступен.` };
    selections.push({
      field,
      origin: 'manual',
      manualValue: choice.manualValue,
      basis: choice.basis,
      evidence:
        document === undefined
          ? null
          : {
              documentId: document.caseDocumentId,
              documentRecordRevision: document.recordRevision,
              documentLocator: choice.documentLocator,
              observationVersionId: null,
            },
    });
  }
  const failure = draft.failure;
  let failureEvidence: RbdCalculationCreateCommand['failureEvidence'] = null;
  if (failure.applicability !== 'unavailable' || failure.basis.trim() !== '') {
    if (failure.basis.trim() === '')
      return { command: null, error: 'Укажите основание применимости расчёта по таблице 3.' };
    const document =
      failure.applicability === 'exact_supported'
        ? documents.find((item) => item.caseDocumentId === failure.documentId)
        : undefined;
    if (failure.applicability === 'exact_supported') {
      if (failure.durationToFailureS.trim() === '' || document === undefined)
        return {
          command: null,
          error: 'Для точного T_OTK нужны значение и документ с началом отсчёта.',
        };
      if (failure.documentLocator.trim() === '')
        return { command: null, error: 'Укажите место значения T_OTK в документе.' };
    }
    failureEvidence = {
      applicability: failure.applicability,
      durationToFailureS:
        failure.applicability === 'exact_supported' ? failure.durationToFailureS : null,
      basis: failure.basis,
      failureObservationIds: [],
      evidence:
        document === undefined
          ? null
          : {
              documentId: document.caseDocumentId,
              documentRecordRevision: document.recordRevision,
              documentLocator: failure.documentLocator,
              observationVersionId: null,
            },
    };
  }
  return {
    command: {
      ...ids,
      executionId: source.executionId,
      planSelection: source.planSelection,
      selections,
      failureEvidence,
      actor: 'local_user',
      reason: draft.reason,
    },
    error: null,
  };
}
