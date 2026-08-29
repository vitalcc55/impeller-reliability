export type ReattachEntityDecision = 'adopt' | 'preserve-draft' | 'conflict' | 'unchanged-new';

interface ReattachEntityState {
  readonly dirty: boolean;
  readonly localRevision: number | null;
  readonly remoteRevision: number | null;
}

export function decideReattachEntity({
  dirty,
  localRevision,
  remoteRevision,
}: ReattachEntityState): ReattachEntityDecision {
  if (remoteRevision === null) return localRevision === null ? 'unchanged-new' : 'conflict';
  if (!dirty) return 'adopt';
  return localRevision === remoteRevision ? 'preserve-draft' : 'conflict';
}
