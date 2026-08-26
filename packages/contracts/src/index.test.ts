import { describe, expect, it } from 'vitest';

import {
  parseWorkerResponse,
  projectBackupResultSchema,
  projectOverviewSchema,
  runtimeStatusSchema,
  workerRequestSchema,
} from './index';

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

  it('validates project commands and rejects a path on session operations', () => {
    expect(
      workerRequestSchema.parse({
        protocolVersion: 1,
        requestId: 'project-1',
        kind: 'request',
        operation: 'project.create',
        revision: 4,
        deadlineMs: 5_000,
        payload: {
          path: 'C:\\Проекты\\Колесо.irproj',
          applicationInstanceId: 'instance-1',
          applicationVersion: '0.1.0',
          draft: { name: 'Колесо', projectNumber: '', description: '', status: 'draft' },
        },
      }).operation,
    ).toBe('project.create');
    expect(
      workerRequestSchema.safeParse({
        protocolVersion: 1,
        requestId: 'project-2',
        kind: 'request',
        operation: 'project.getOverview',
        revision: 5,
        deadlineMs: 5_000,
        payload: { path: 'C:\\arbitrary.irproj' },
      }).success,
    ).toBe(false);
  });

  it('rejects non-canonical UTC timestamps at the desktop boundary', () => {
    const overview = {
      projectId: '019c89f0-0b57-7ef5-9656-595184fcb272',
      path: 'C:\\Projects\\Wheel.irproj',
      name: 'Wheel',
      projectNumber: '',
      description: '',
      status: 'draft',
      recordRevision: 1,
      createdAtUtc: '2026-08-26T00:00:00.000Z',
      updatedAtUtc: '2026-08-26T00:00:00.000Z',
      createdWithApplicationVersion: '0.1.0',
      schemaVersion: 1,
    } as const;

    expect(projectOverviewSchema.safeParse({ ...overview, updatedAtUtc: 'invalid' }).success).toBe(
      false,
    );
    expect(
      projectBackupResultSchema.safeParse({
        fileName: 'project-v1.sqlite',
        sha256: 'a'.repeat(64),
        createdAtUtc: '2026-99-99T25:61:61.000Z',
      }).success,
    ).toBe(false);
  });

  it('rejects invalid application versions at the desktop boundary', () => {
    const overview = {
      projectId: '019c89f0-0b57-7ef5-9656-595184fcb272',
      path: 'C:\\Projects\\Wheel.irproj',
      name: 'Wheel',
      projectNumber: '',
      description: '',
      status: 'draft',
      recordRevision: 1,
      createdAtUtc: '2026-08-26T00:00:00.000Z',
      updatedAtUtc: '2026-08-26T00:00:00.000Z',
      createdWithApplicationVersion: '0.1.0',
      schemaVersion: 1,
    } as const;

    expect(
      projectOverviewSchema.safeParse({ ...overview, createdWithApplicationVersion: '' }).success,
    ).toBe(false);
    expect(
      projectOverviewSchema.safeParse({ ...overview, createdWithApplicationVersion: ' 0.1.0' })
        .success,
    ).toBe(false);
    expect(
      projectOverviewSchema.safeParse({ ...overview, createdWithApplicationVersion: '\ufeff0.1.0' })
        .success,
    ).toBe(false);
    expect(
      projectOverviewSchema.safeParse({
        ...overview,
        createdWithApplicationVersion: '🚀'.repeat(33),
      }).success,
    ).toBe(false);
  });
});
