export const MAX_DATASET_CANDIDATES = 100;

export interface DatasetCandidateDecision {
  readonly observationVersionId: string;
  readonly decision: 'pending' | 'included' | 'excluded';
  readonly reason: string;
}

export type DatasetCandidateDecisions = Readonly<Record<string, DatasetCandidateDecision>>;

interface AddDatasetCandidateResult {
  readonly decisions: DatasetCandidateDecisions;
  readonly limitReached: boolean;
}

export function addDatasetCandidate(
  current: DatasetCandidateDecisions,
  executionId: string,
  observationVersionId: string,
): AddDatasetCandidateResult {
  if (current[executionId] !== undefined) return { decisions: current, limitReached: false };
  if (Object.keys(current).length >= MAX_DATASET_CANDIDATES)
    return { decisions: current, limitReached: true };
  return {
    decisions: {
      ...current,
      [executionId]: { observationVersionId, decision: 'pending', reason: '' },
    },
    limitReached: false,
  };
}

export function replaceDatasetCandidateVersion(
  current: DatasetCandidateDecisions,
  executionId: string,
  observationVersionId: string,
): DatasetCandidateDecisions {
  if (current[executionId] === undefined) return current;
  return {
    ...current,
    [executionId]: { observationVersionId, decision: 'pending', reason: '' },
  };
}

export function observationResponseMatches(
  response: { readonly observationVersionId: string; readonly executionId: string },
  requestedObservationVersionId: string,
  expectedExecutionId: string,
): boolean {
  return (
    response.observationVersionId === requestedObservationVersionId &&
    response.executionId === expectedExecutionId
  );
}

export function hasUniqueDatasetMembers(
  members: readonly {
    readonly observationVersionId: string;
    readonly executionId: string;
  }[],
): boolean {
  return (
    new Set(members.map((member) => member.observationVersionId)).size === members.length &&
    new Set(members.map((member) => member.executionId)).size === members.length
  );
}
