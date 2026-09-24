import { describe, expect, it } from 'vitest';

import type { RbdPlanSource } from '@impeller-reliability/contracts';

import {
  buildCalculationCommand,
  emptyCalculationDraft,
  sourceValueForField,
} from './rbd-calculation-draft';

const source: RbdPlanSource = {
  executionId: '333ec2c8-9439-4ce8-823d-3e2b0de8f003',
  localImportId: '113ec2c8-9439-4ce8-823d-3e2b0de8f001',
  packageId: 'package-1',
  runId: 'run-1',
  exportRevision: 2,
  outerPackageSha256: 'a'.repeat(64),
  sourceSnapshotSha256: 'b'.repeat(64),
  producerName: 'R130SH',
  producerVersion: '0.9.45',
  producerBuildId: 'build-1',
  producerGitCommit: 'commit-1',
  planSelection: 'original',
  payloadPath: 'plan/original.json',
  payloadSha256: 'c'.repeat(64),
  planId: 'plan-1',
  planRevision: 1,
  sourceValues: {
    baseCycles: '1000',
    reserveFactor: '1.5003',
    nominalRpm: '1500',
    accelerationDurationS: '5',
    decelerationDurationS: '5',
  },
  methodicalRequirements: {
    requiredCyclesExact: '1500.3',
    requiredSteadyDurationSExact: '60.012',
  },
  executionTargets: {
    targetCycles: '1501',
    targetSteadyDurationS: '60.04',
    totalDurationS: '70.04',
    roundingPolicy: 'producer rounding',
  },
};

const ids = {
  analysisInputSnapshotId: '443ec2c8-9439-4ce8-823d-3e2b0de8f004',
  calculationSnapshotId: '553ec2c8-9439-4ce8-823d-3e2b0de8f005',
};

describe('RBD calculation draft', () => {
  it('requires five explicit choices without turning producer targets into inputs', () => {
    const draft = emptyCalculationDraft();
    expect(buildCalculationCommand(source, draft, [], ids).command).toBeNull();
    const chosen = {
      ...draft,
      fields: {
        nominal_rpm: { ...draft.fields.nominal_rpm, origin: 'source' as const },
        base_cycles: { ...draft.fields.base_cycles, origin: 'source' as const },
        reserve_factor: { ...draft.fields.reserve_factor, origin: 'source' as const },
        acceleration_duration_s: {
          ...draft.fields.acceleration_duration_s,
          origin: 'source' as const,
        },
        deceleration_duration_s: {
          ...draft.fields.deceleration_duration_s,
          origin: 'source' as const,
        },
      },
      reason: 'Выбран исходный план',
    };
    const built = buildCalculationCommand(source, chosen, [], ids);
    expect(built.error).toBeNull();
    expect(built.command?.selections.map((item) => item.manualValue)).toEqual([
      null,
      null,
      null,
      null,
      null,
    ]);
    expect(sourceValueForField(source, 'base_cycles')).toBe('1000');
    expect(built.command).not.toHaveProperty('calculatedOutputs');
  });

  it('requires a reason for manual replacement and a documented exact failure endpoint', () => {
    const draft = emptyCalculationDraft();
    const chosen = {
      ...draft,
      fields: {
        base_cycles: {
          ...draft.fields.base_cycles,
          origin: 'manual' as const,
          manualValue: '1200',
        },
        reserve_factor: { ...draft.fields.reserve_factor, origin: 'source' as const },
        nominal_rpm: { ...draft.fields.nominal_rpm, origin: 'source' as const },
        acceleration_duration_s: {
          ...draft.fields.acceleration_duration_s,
          origin: 'source' as const,
        },
        deceleration_duration_s: {
          ...draft.fields.deceleration_duration_s,
          origin: 'source' as const,
        },
      },
      reason: 'Отдельный сценарий',
    };
    expect(buildCalculationCommand(source, chosen, [], ids).command).toBeNull();
    const documented = {
      ...chosen,
      fields: {
        ...chosen.fields,
        base_cycles: { ...chosen.fields.base_cycles, basis: 'Принято по протоколу' },
      },
      failure: {
        ...chosen.failure,
        applicability: 'exact_supported' as const,
        durationToFailureS: '60000',
        basis: 'Простой запуск без пауз',
      },
    };
    expect(buildCalculationCommand(source, documented, [], ids).error).toContain('документ');
    const interval = {
      ...documented,
      failure: {
        ...documented.failure,
        applicability: 'interval_endpoint' as const,
        documentId: '663ec2c8-9439-4ce8-823d-3e2b0de8f006',
        documentLocator: 'устаревший скрытый локатор',
      },
    };
    const intervalCommand = buildCalculationCommand(source, interval, [], ids).command;
    expect(intervalCommand?.failureEvidence?.evidence).toBeNull();
  });
});
