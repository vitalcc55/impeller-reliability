import type {
  CaseDocumentSummary,
  PmnCalculationCreateCommand,
  PmnPlanSource,
} from '@impeller-reliability/contracts';

export const PMN_INPUT_FIELDS = [
  'nominal_rpm',
  'speed_factor',
  'target_cycles',
  'acceleration_duration_s',
  'steady_duration_s',
  'deceleration_duration_s',
] as const;

export type PmnInputField = (typeof PMN_INPUT_FIELDS)[number];

export const PMN_FIELD_LABELS: Record<PmnInputField, { label: string; unit: string }> = {
  nominal_rpm: { label: 'Номинальная частота nР', unit: 'об/мин' },
  speed_factor: { label: 'Коэффициент превышения частоты k3', unit: 'безразмерный' },
  target_cycles: { label: 'Число циклов NЦ3', unit: 'циклов' },
  acceleration_duration_s: { label: 'Время разгона tР3', unit: 'с' },
  steady_duration_s: { label: 'Время установившегося вращения tУСТ3', unit: 'с' },
  deceleration_duration_s: { label: 'Время торможения tТ3', unit: 'с' },
};

export interface PmnFieldDraft {
  readonly origin: 'source' | 'manual' | null;
  readonly manualValue: string;
  readonly basis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
}

export type PmnFailureApplicability = NonNullable<
  PmnCalculationCreateCommand['failureEvidence']
>['applicability'];

export interface PmnFailureDraft {
  readonly applicability: PmnFailureApplicability;
  readonly durationToFailureS: string;
  readonly basis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
}

export interface PmnCalculationDraft {
  readonly fields: Record<PmnInputField, PmnFieldDraft>;
  readonly failure: PmnFailureDraft;
  readonly reason: string;
}

export type PmnDraftErrorTarget =
  | {
      readonly field: PmnInputField;
      readonly control: 'origin' | 'manualValue' | 'basis' | 'document' | 'locator';
    }
  | { readonly field: 'failure'; readonly control: 'duration' | 'basis' | 'document' | 'locator' }
  | { readonly field: 'reason'; readonly control: 'reason' };

interface PmnCommandBuildResult {
  readonly command: PmnCalculationCreateCommand | null;
  readonly error: string | null;
  readonly errorTarget: PmnDraftErrorTarget | null;
}

const invalid = (error: string, errorTarget: PmnDraftErrorTarget): PmnCommandBuildResult => ({
  command: null,
  error,
  errorTarget,
});

const emptyField = (): PmnFieldDraft => ({
  origin: null,
  manualValue: '',
  basis: '',
  documentId: null,
  documentLocator: '',
});

export function emptyPmnCalculationDraft(): PmnCalculationDraft {
  return {
    fields: {
      nominal_rpm: emptyField(),
      speed_factor: emptyField(),
      target_cycles: emptyField(),
      acceleration_duration_s: emptyField(),
      steady_duration_s: emptyField(),
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

export function pmnSourceValueForField(source: PmnPlanSource, field: PmnInputField): string | null {
  switch (field) {
    case 'nominal_rpm':
      return source.sourceValues.nominalRpm;
    case 'speed_factor':
      return source.sourceValues.speedFactor;
    case 'target_cycles':
      return source.sourceValues.targetCycles;
    case 'acceleration_duration_s':
      return source.sourceValues.accelerationDurationS;
    case 'steady_duration_s':
      return source.sourceValues.steadyDurationS;
    case 'deceleration_duration_s':
      return source.sourceValues.decelerationDurationS;
  }
}

export function buildPmnCalculationCommand(
  source: PmnPlanSource,
  draft: PmnCalculationDraft,
  documents: readonly CaseDocumentSummary[],
  ids: Pick<PmnCalculationCreateCommand, 'analysisInputSnapshotId' | 'calculationSnapshotId'>,
): PmnCommandBuildResult {
  if (!draft.reason.trim())
    return invalid('Укажите основание создания расчётного снимка.', {
      field: 'reason',
      control: 'reason',
    });
  const selections: PmnCalculationCreateCommand['selections'] = [];
  for (const field of PMN_INPUT_FIELDS) {
    const choice = draft.fields[field];
    if (choice.origin === null)
      return invalid(`Выберите происхождение: ${PMN_FIELD_LABELS[field].label}.`, {
        field,
        control: 'origin',
      });
    if (choice.origin === 'source') {
      if (pmnSourceValueForField(source, field) === null)
        return invalid(
          `${PMN_FIELD_LABELS[field].label}: в выбранном плане значение отсутствует.`,
          { field, control: 'origin' },
        );
      selections.push({ field, origin: 'source', manualValue: null, basis: '', evidence: null });
      continue;
    }
    if (!choice.manualValue.trim())
      return invalid(`${PMN_FIELD_LABELS[field].label}: укажите значение дополнения.`, {
        field,
        control: 'manualValue',
      });
    if (!choice.basis.trim())
      return invalid(`${PMN_FIELD_LABELS[field].label}: укажите основание замещения.`, {
        field,
        control: 'basis',
      });
    const document = documents.find((item) => item.caseDocumentId === choice.documentId);
    if (choice.documentId !== null && document === undefined)
      return invalid(`${PMN_FIELD_LABELS[field].label}: документ недоступен.`, {
        field,
        control: 'document',
      });
    if (document !== undefined && !choice.documentLocator.trim())
      return invalid(`${PMN_FIELD_LABELS[field].label}: укажите место значения в документе.`, {
        field,
        control: 'locator',
      });
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
            },
    });
  }
  const failure = draft.failure;
  let failureEvidence: PmnCalculationCreateCommand['failureEvidence'] = null;
  if (failure.applicability !== 'unavailable' || failure.basis.trim()) {
    if (!failure.basis.trim())
      return invalid('Укажите основание применимости таблицы 5.', {
        field: 'failure',
        control: 'basis',
      });
    const document = documents.find((item) => item.caseDocumentId === failure.documentId);
    if (failure.applicability === 'exact_supported' && !failure.durationToFailureS.trim())
      return invalid('Для таблицы 5 укажите точное T_ОТК.', {
        field: 'failure',
        control: 'duration',
      });
    if (failure.applicability === 'exact_supported' && document === undefined)
      return invalid('Для точного T_ОТК выберите документ с началом отсчёта и отказом.', {
        field: 'failure',
        control: 'document',
      });
    if (failure.applicability === 'exact_supported' && !failure.documentLocator.trim())
      return invalid('Укажите место начала отсчёта и отказа в документе.', {
        field: 'failure',
        control: 'locator',
      });
    failureEvidence = {
      applicability: failure.applicability,
      durationToFailureS:
        failure.applicability === 'exact_supported' ? failure.durationToFailureS : null,
      basis: failure.basis,
      evidence:
        document === undefined
          ? null
          : {
              documentId: document.caseDocumentId,
              documentRecordRevision: document.recordRevision,
              documentLocator: failure.documentLocator,
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
    errorTarget: null,
  };
}
