import { describe, expect, it } from 'vitest';

import { pmnPlanSourceSchema } from '@impeller-reliability/contracts';

import {
  buildPmnCalculationCommand,
  emptyPmnCalculationDraft,
  PMN_INPUT_FIELDS,
} from './pmn-calculation-draft';

const source = pmnPlanSourceSchema.parse({
  executionId: '333ec2c8-9439-4ce8-823d-3e2b0de8f003',
  localImportId: '113ec2c8-9439-4ce8-823d-3e2b0de8f001',
  packageId: 'package-1',
  runId: 'run-1',
  exportRevision: 1,
  outerPackageSha256: 'a'.repeat(64),
  sourceSnapshotSha256: 'b'.repeat(64),
  producerName: 'R130SH',
  producerVersion: '1',
  producerBuildId: 'build-1',
  producerGitCommit: 'commit-1',
  planSelection: 'original',
  payloadPath: 'plan/original.json',
  payloadSha256: 'c'.repeat(64),
  planId: 'plan-1',
  planRevision: 1,
  sourceValues: {
    nominalRpm: '1500',
    speedFactor: '1.1',
    targetCycles: '2',
    accelerationDurationS: '2',
    steadyDurationS: '1',
    decelerationDurationS: '2',
  },
  methodicalRequirements: {
    targetMaxRpmExact: '1650',
    cycleDurationSExact: '5',
    totalDurationSExact: '10',
  },
  executionTargets: {
    targetMaxRpm: '1650',
    targetCycles: '2',
    cycleDurationS: '5',
    totalDurationS: '10',
  },
});
const ids = {
  analysisInputSnapshotId: '613ec2c8-9439-4ce8-823d-3e2b0de8f006',
  calculationSnapshotId: '713ec2c8-9439-4ce8-823d-3e2b0de8f007',
};
const document = {
  caseDocumentId: '813ec2c8-9439-4ce8-823d-3e2b0de8f008',
  documentKind: 'measurement_or_attestation_record' as const,
  title: 'Протокол отказа',
  designation: 'ПМН-01',
  recordRevision: 2,
  archivedAtUtc: null,
  warnings: [],
};

describe('PMN calculation draft', () => {
  it('requires an explicit origin for every input and keeps the six source selections distinct', () => {
    const empty = emptyPmnCalculationDraft();
    expect(buildPmnCalculationCommand(source, empty, [], ids)).toMatchObject({
      command: null,
      errorTarget: { field: 'reason', control: 'reason' },
    });
    const fields = { ...empty.fields };
    for (const field of PMN_INPUT_FIELDS) fields[field] = { ...fields[field], origin: 'source' };
    const built = buildPmnCalculationCommand(
      source,
      { ...empty, fields, reason: 'Проверка ПМН' },
      [],
      ids,
    );
    expect(built.error).toBeNull();
    expect(built.command?.selections.map((item) => item.field)).toEqual(PMN_INPUT_FIELDS);
    expect(built.command?.failureEvidence).toBeNull();
  });

  it('uses manual evidence and accepts exact documented time to failure of zero', () => {
    const empty = emptyPmnCalculationDraft();
    const fields = { ...empty.fields };
    for (const field of PMN_INPUT_FIELDS) fields[field] = { ...fields[field], origin: 'source' };
    fields.speed_factor = {
      origin: 'manual',
      manualValue: '1.2',
      basis: 'Сверено с протоколом',
      documentId: document.caseDocumentId,
      documentLocator: 'раздел 2',
    };
    const draft = {
      ...empty,
      fields,
      reason: 'Расчёт по выбранному исполнению',
      failure: {
        applicability: 'exact_supported' as const,
        durationToFailureS: '0',
        basis: 'От начала отсчёта до отказа',
        documentId: document.caseDocumentId,
        documentLocator: 'раздел 3',
      },
    };
    expect(
      buildPmnCalculationCommand(
        source,
        {
          ...draft,
          fields: { ...fields, speed_factor: { ...fields.speed_factor, manualValue: '' } },
        },
        [document],
        ids,
      ),
    ).toMatchObject({
      command: null,
      errorTarget: { field: 'speed_factor', control: 'manualValue' },
    });
    expect(buildPmnCalculationCommand(source, draft, [], ids)).toMatchObject({
      command: null,
      errorTarget: { field: 'speed_factor', control: 'document' },
    });
    const built = buildPmnCalculationCommand(source, draft, [document], ids);
    expect(
      buildPmnCalculationCommand(
        source,
        { ...draft, failure: { ...draft.failure, documentId: null } },
        [document],
        ids,
      ),
    ).toMatchObject({ command: null, errorTarget: { field: 'failure', control: 'document' } });
    expect(built.command?.selections.find((item) => item.field === 'speed_factor')).toMatchObject({
      origin: 'manual',
      manualValue: '1.2',
      evidence: { documentRecordRevision: 2 },
    });
    expect(built.command?.failureEvidence).toMatchObject({
      durationToFailureS: '0',
      evidence: { documentLocator: 'раздел 3' },
    });
  });
});
