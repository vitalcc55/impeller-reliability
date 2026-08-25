import { describe, expect, it } from 'vitest';

import { runtimeStatusSchema, workerRequestSchema } from './index';

describe('worker contracts', () => {
  it('rejects generic operations', () => {
    expect(
      workerRequestSchema.safeParse({
        protocolVersion: 1,
        requestId: 'request-1',
        kind: 'request',
        operation: 'system.execute',
        revision: 0,
        deadlineMs: 1_000,
        payload: {},
      }).success,
    ).toBe(false);
  });

  it('accepts a canonical runtime status', () => {
    expect(
      runtimeStatusSchema.parse({
        applicationVersion: '0.1.0',
        electronVersion: '43.4.1',
        workerStatus: 'ready',
        workerVersion: '0.1.0',
        protocolVersion: 1,
        sqliteStatus: 'ok',
        mode: 'development',
        message: 'Готово',
      }).workerStatus,
    ).toBe('ready');
  });
});
