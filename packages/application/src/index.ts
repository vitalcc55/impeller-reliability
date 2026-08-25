export interface RevisionGate {
  readonly currentRevision: number;
  accepts(responseRevision: number): boolean;
}

export function createRevisionGate(currentRevision: number): RevisionGate {
  return {
    currentRevision,
    accepts: (responseRevision) => responseRevision === currentRevision,
  };
}
