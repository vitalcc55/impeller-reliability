import { describe, expect, it } from 'vitest';

import { parseWorkerResponse, runtimeStatusSchema, workerRequestSchema } from './index';

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

  it('validates operation-specific results and response revision', () => {
    const response = parseWorkerResponse('system.ping', {
      protocolVersion: 1,
      requestId: 'request-1',
      revision: 12,
      kind: 'response',
      ok: true,
      result: { pong: true },
      evidence: {},
      warnings: [],
    });
    expect(response.revision).toBe(12);
    expect(response.ok && response.result.pong).toBe(true);
    expect(() =>
      parseWorkerResponse('system.ping', {
        ...response,
        result: { workerVersion: 'wrong-operation-result' },
      }),
    ).toThrow();
  });
});
