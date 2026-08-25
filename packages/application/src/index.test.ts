import { describe, expect, it } from 'vitest';

import { createRevisionGate } from './index';

describe('revision gate', () => {
  it('accepts only the response revision owned by the request', () => {
    const gate = createRevisionGate(12);
    expect(gate.accepts(12)).toBe(true);
    expect(gate.accepts(11)).toBe(false);
    expect(gate.accepts(13)).toBe(false);
  });
});
