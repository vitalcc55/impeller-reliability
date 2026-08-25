import { describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { JsonlLogger } from './logging';
import { WorkerClient, WORKER_OPERATION_POLICIES } from './worker-client';

describe('worker operation deadlines', () => {
  it('keeps transport timeout above every domain deadline', () => {
    for (const policy of Object.values(WORKER_OPERATION_POLICIES)) {
      expect(policy.transportTimeoutMs).toBeGreaterThan(policy.domainDeadlineMs);
    }
  });

  it('terminates the worker when a stateful project transport timeout is indeterminate', () => {
    expect(WORKER_OPERATION_POLICIES['project.create'].terminateWorkerOnTimeout).toBe(true);
    expect(WORKER_OPERATION_POLICIES['project.open'].terminateWorkerOnTimeout).toBe(true);
    expect(WORKER_OPERATION_POLICIES['project.close'].terminateWorkerOnTimeout).toBe(true);
    expect(WORKER_OPERATION_POLICIES['project.updateMetadata'].terminateWorkerOnTimeout).toBe(true);
    expect(WORKER_OPERATION_POLICIES['project.createBackup'].terminateWorkerOnTimeout).toBe(true);
    expect(WORKER_OPERATION_POLICIES['project.getOverview'].terminateWorkerOnTimeout).toBe(false);
  });

  it('kills a non-responsive worker after a stateful transport timeout', async () => {
    const events: string[] = [];
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-timeout-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', 'process.stdin.resume()'],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'timeout-test.jsonl')),
      (event) => events.push(event.state),
      {
        ...WORKER_OPERATION_POLICIES,
        'project.create': {
          domainDeadlineMs: 10,
          transportTimeoutMs: 25,
          terminateWorkerOnTimeout: true,
        },
      },
    );
    try {
      await client.start();
      await expect(
        client.request('project.create', {
          path: 'C:\\Temp\\timeout.irproj',
          applicationInstanceId: 'timeout-test',
          applicationVersion: '0.1.0',
          draft: {
            name: 'Timeout test',
            projectNumber: '',
            description: '',
            status: 'draft',
          },
        }),
      ).rejects.toThrow('worker_transport_timeout:project.create');
      await expect.poll(() => client.processId).toBeNull();
      expect(events).toContain('unavailable');
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });
});
