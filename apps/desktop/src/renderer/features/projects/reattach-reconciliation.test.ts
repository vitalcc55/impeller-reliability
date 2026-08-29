import { describe, expect, it } from 'vitest';

import { decideReattachEntity } from './reattach-reconciliation';

describe('entity reconciliation after worker reattach', () => {
  it.each([
    {
      name: 'adopts an authoritative snapshot for a clean existing entity',
      dirty: false,
      localRevision: 1,
      remoteRevision: 2,
      expected: 'adopt',
    },
    {
      name: 'preserves a dirty draft when its baseline revision is still current',
      dirty: true,
      localRevision: 1,
      remoteRevision: 1,
      expected: 'preserve-draft',
    },
    {
      name: 'reports a conflict when a dirty entity changed remotely',
      dirty: true,
      localRevision: 1,
      remoteRevision: 2,
      expected: 'conflict',
    },
    {
      name: 'keeps a new form when its preallocated id was not committed',
      dirty: true,
      localRevision: null,
      remoteRevision: null,
      expected: 'unchanged-new',
    },
    {
      name: 'reports a conflict when a new dirty entity was committed without a usable response',
      dirty: true,
      localRevision: null,
      remoteRevision: 1,
      expected: 'conflict',
    },
    {
      name: 'reports a conflict when an existing entity is unexpectedly absent',
      dirty: false,
      localRevision: 1,
      remoteRevision: null,
      expected: 'conflict',
    },
  ] as const)('$name', ({ dirty, localRevision, remoteRevision, expected }) => {
    expect(decideReattachEntity({ dirty, localRevision, remoteRevision })).toBe(expected);
  });
});
