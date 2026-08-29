import { describe, expect, it } from 'vitest';

import { parseOptionalPositiveInteger } from './optional-positive-integer';

describe('parseOptionalPositiveInteger', () => {
  it.each(['', '   '])('treats %j as an intentional empty value', (input) => {
    expect(parseOptionalPositiveInteger(input)).toEqual({ kind: 'empty', value: null });
  });

  it.each([
    ['1500', 1500],
    [' 12 ', 12],
  ])('accepts %j as a positive safe integer', (input, expected) => {
    expect(parseOptionalPositiveInteger(input)).toEqual({ kind: 'valid', value: expected });
  });

  it.each(['12.5', '1 500', 'abc', '0', '-1', '9007199254740992'])(
    'preserves %j as invalid instead of turning it into null',
    (input) => {
      expect(parseOptionalPositiveInteger(input)).toEqual({ kind: 'invalid' });
    },
  );
});
