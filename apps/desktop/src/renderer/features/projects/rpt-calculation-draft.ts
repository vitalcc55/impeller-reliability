import type {
  CaseDocumentSummary,
  RptCalculationCreateCommand,
  RptPlanSource,
} from '@impeller-reliability/contracts';

export const RPT_INPUT_FIELDS = [
  'nominal_rpm',
  'design_cycles',
  'reserve_factor',
  'acceleration_duration_s',
  'steady_duration_s',
  'deceleration_duration_s',
] as const;

export type RptInputField = (typeof RPT_INPUT_FIELDS)[number];

export const RPT_FIELD_LABELS: Record<RptInputField, { label: string; unit: string }> = {
  nominal_rpm: { label: 'Номинальная частота nP', unit: 'об/мин' },
  design_cycles: { label: 'Заданное число циклов NЦ', unit: 'циклов' },
  reserve_factor: { label: 'Коэффициент запаса k2', unit: 'безразмерный' },
  acceleration_duration_s: { label: 'Время разгона tР', unit: 'с' },
  steady_duration_s: { label: 'Время установившегося вращения tУСТ', unit: 'с' },
  deceleration_duration_s: { label: 'Время торможения tТ', unit: 'с' },
};

export interface RptFieldDraft {
  readonly origin: 'source' | 'manual' | null;
  readonly manualValue: string;
  readonly basis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
}

export type RptFailureApplicability = NonNullable<
  RptCalculationCreateCommand['failureEvidence']
>['applicability'];

export interface RptFailureDraft {
  readonly applicability: RptFailureApplicability;
  readonly durationToFailureS: string;
  readonly basis: string;
  readonly documentId: string | null;
  readonly documentLocator: string;
}

export interface RptCalculationDraft {
  readonly fields: Record<RptInputField, RptFieldDraft>;
  readonly failure: RptFailureDraft;
  readonly reason: string;
}

const emptyField = (): RptFieldDraft => ({
  origin: null,
  manualValue: '',
  basis: '',
  documentId: null,
  documentLocator: '',
});

export function emptyRptCalculationDraft(): RptCalculationDraft {
  return {
    fields: {
      nominal_rpm: emptyField(),
      design_cycles: emptyField(),
      reserve_factor: emptyField(),
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

export function rptSourceValueForField(source: RptPlanSource, field: RptInputField): string | null {
  switch (field) {
    case 'nominal_rpm':
      return source.sourceValues.nominalRpm;
    case 'design_cycles':
      return source.sourceValues.designCycles;
    case 'reserve_factor':
      return source.sourceValues.reserveFactor;
    case 'acceleration_duration_s':
      return source.sourceValues.accelerationDurationS;
    case 'steady_duration_s':
      return source.sourceValues.steadyDurationS;
    case 'deceleration_duration_s':
      return source.sourceValues.decelerationDurationS;
  }
}

export function buildRptCalculationCommand(
  source: RptPlanSource,
  draft: RptCalculationDraft,
  documents: readonly CaseDocumentSummary[],
  ids: Pick<RptCalculationCreateCommand, 'analysisInputSnapshotId' | 'calculationSnapshotId'>,
): { command: RptCalculationCreateCommand | null; error: string | null } {
  if (!draft.reason.trim())
    return { command: null, error: 'Укажите основание создания расчётного снимка.' };
  const selections: RptCalculationCreateCommand['selections'] = [];
  for (const field of RPT_INPUT_FIELDS) {
    const choice = draft.fields[field];
    if (choice.origin === null)
      return { command: null, error: `Выберите происхождение: ${RPT_FIELD_LABELS[field].label}.` };
    if (choice.origin === 'source') {
      if (rptSourceValueForField(source, field) === null)
        return {
          command: null,
          error: `${RPT_FIELD_LABELS[field].label}: в выбранном плане значение отсутствует.`,
        };
      selections.push({ field, origin: 'source', manualValue: null, basis: '', evidence: null });
      continue;
    }
    if (!choice.manualValue.trim() || !choice.basis.trim())
      return {
        command: null,
        error: `${RPT_FIELD_LABELS[field].label}: укажите значение и основание.`,
      };
    const document = documents.find((item) => item.caseDocumentId === choice.documentId);
    if (choice.documentId !== null && document === undefined)
      return { command: null, error: `${RPT_FIELD_LABELS[field].label}: документ недоступен.` };
    if (document !== undefined && !choice.documentLocator.trim())
      return {
        command: null,
        error: `${RPT_FIELD_LABELS[field].label}: укажите место значения в документе.`,
      };
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
  let failureEvidence: RptCalculationCreateCommand['failureEvidence'] = null;
  if (failure.applicability !== 'unavailable' || failure.basis.trim()) {
    if (!failure.basis.trim())
      return { command: null, error: 'Укажите основание применимости таблицы 4.' };
    const document = documents.find((item) => item.caseDocumentId === failure.documentId);
    if (
      failure.applicability === 'exact_supported' &&
      (!failure.durationToFailureS.trim() ||
        document === undefined ||
        !failure.documentLocator.trim())
    )
      return {
        command: null,
        error: 'Для точного T_ОТК нужны значение, документ, начало отсчёта и место записи.',
      };
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
  };
}
