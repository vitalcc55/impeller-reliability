import type { DesktopError } from '@impeller-reliability/contracts';

export function classifyMissingVersionAfterReattach(
  unresolvedWrite: boolean,
  errorCode: DesktopError['code'],
): 'retry_same_id' | 'block' {
  return unresolvedWrite && errorCode === 'entity_not_found' ? 'retry_same_id' : 'block';
}
