import { describe, expect, it } from 'vitest';

import { classifyMissingVersionAfterReattach } from './reliability-write-reconciliation';

describe('versioned reliability write reconciliation', () => {
  it('unlocks an unresolved version only when the authoritative read proves it absent', () => {
    expect(classifyMissingVersionAfterReattach(true, 'entity_not_found')).toBe('retry_same_id');
    expect(classifyMissingVersionAfterReattach(true, 'worker_unavailable')).toBe('block');
    expect(classifyMissingVersionAfterReattach(false, 'entity_not_found')).toBe('block');
  });
});
