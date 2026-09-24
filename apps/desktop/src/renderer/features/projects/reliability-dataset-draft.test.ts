import { describe, expect, it } from 'vitest';

import {
  MAX_DATASET_CANDIDATES,
  addDatasetCandidate,
  hasUniqueDatasetMembers,
  observationResponseMatches,
  replaceDatasetCandidateVersion,
} from './reliability-dataset-draft';

describe('reliability dataset draft membership', () => {
  it('keeps a small explicit draft after viewing more than one hundred executions', () => {
    const viewedExecutionIds = Array.from({ length: 125 }, (_, index) => `execution-${index}`);
    let decisions = {};
    for (const executionId of viewedExecutionIds.slice(0, 2)) {
      decisions = addDatasetCandidate(
        decisions,
        executionId,
        `observation-${executionId}`,
      ).decisions;
    }

    expect(viewedExecutionIds).toHaveLength(125);
    expect(Object.keys(decisions)).toEqual(['execution-0', 'execution-1']);
  });

  it('adds only the explicitly selected observation version', () => {
    const result = addDatasetCandidate({}, 'execution-1', 'observation-v1');

    expect(result).toEqual({
      decisions: {
        'execution-1': {
          observationVersionId: 'observation-v1',
          decision: 'pending',
          reason: '',
        },
      },
      limitReached: false,
    });
  });

  it('rejects the 101st candidate without changing the selected decisions', () => {
    const selected = Object.fromEntries(
      Array.from({ length: MAX_DATASET_CANDIDATES }, (_, index) => [
        `execution-${index}`,
        {
          observationVersionId: `observation-${index}`,
          decision: 'excluded' as const,
          reason: 'Рассмотрено инженером',
        },
      ]),
    );

    const result = addDatasetCandidate(selected, 'execution-101', 'observation-101');

    expect(result.limitReached).toBe(true);
    expect(result.decisions).toBe(selected);
    expect(Object.keys(result.decisions)).toHaveLength(MAX_DATASET_CANDIDATES);
  });

  it('keeps the historical version until replacement is explicit', () => {
    const selected = {
      'execution-1': {
        observationVersionId: 'observation-v1',
        decision: 'included' as const,
        reason: 'Версия 1 рассмотрена',
      },
    };

    expect(selected['execution-1'].observationVersionId).toBe('observation-v1');
    expect(replaceDatasetCandidateVersion(selected, 'execution-1', 'observation-v2')).toEqual({
      'execution-1': {
        observationVersionId: 'observation-v2',
        decision: 'pending',
        reason: '',
      },
    });
    expect(selected['execution-1'].observationVersionId).toBe('observation-v1');
  });

  it('rejects a response for another version of the same execution', () => {
    expect(
      observationResponseMatches(
        { observationVersionId: 'observation-v2', executionId: 'execution-1' },
        'observation-v1',
        'execution-1',
      ),
    ).toBe(false);
  });

  it('rejects duplicate execution or observation identities in persisted members', () => {
    expect(
      hasUniqueDatasetMembers([
        { executionId: 'execution-1', observationVersionId: 'observation-v1' },
        { executionId: 'execution-1', observationVersionId: 'observation-v2' },
      ]),
    ).toBe(false);
    expect(
      hasUniqueDatasetMembers([
        { executionId: 'execution-1', observationVersionId: 'observation-v1' },
        { executionId: 'execution-2', observationVersionId: 'observation-v1' },
      ]),
    ).toBe(false);
  });
});
