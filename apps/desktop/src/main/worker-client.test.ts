import { describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { JsonlLogger } from './logging';
import { WorkerClient, WORKER_OPERATION_POLICIES } from './worker-client';

function serialWorkerScript(operationDelayMs: number): string {
  return String.raw`
    const readline = require('node:readline');
    const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    let queue = Promise.resolve();
    input.on('line', (line) => {
      queue = queue.then(async () => {
        const request = JSON.parse(line);
        if (request.operation === 'project.createBackup') {
          await new Promise((resolve) => setTimeout(resolve, ${String(operationDelayMs)}));
          process.stdout.write(JSON.stringify({
            protocolVersion: 1,
            requestId: request.requestId,
            revision: request.revision,
            kind: 'response',
            ok: true,
            result: { fileName: 'project-v1.sqlite', sha256: 'a'.repeat(64), createdAtUtc: '2026-08-26T00:00:00.000Z' },
            evidence: {},
            warnings: [],
          }) + '\n');
          return;
        }
        if (request.operation === 'system.shutdown') {
          process.stdout.write(JSON.stringify({
            protocolVersion: 1,
            requestId: request.requestId,
            revision: request.revision,
            kind: 'response',
            ok: true,
            result: { accepted: true },
            evidence: {},
            warnings: [],
          }) + '\n');
          process.exit(0);
        }
      });
    });
  `;
}

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

  it('drains an active stateful operation before graceful shutdown', async () => {
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-drain-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', serialWorkerScript(100)],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'drain-test.jsonl')),
      () => undefined,
      {
        ...WORKER_OPERATION_POLICIES,
        'system.shutdown': {
          domainDeadlineMs: 20,
          transportTimeoutMs: 40,
          terminateWorkerOnTimeout: false,
        },
        'project.createBackup': {
          domainDeadlineMs: 150,
          transportTimeoutMs: 180,
          terminateWorkerOnTimeout: true,
        },
      },
    );
    try {
      await client.start();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const backup = client.request('project.createBackup', {});
      const shutdown = client.shutdown(30);

      await expect(client.request('system.ping', {})).rejects.toThrow('worker_stopping');
      await expect(backup).resolves.toMatchObject({ ok: true });
      await expect(shutdown).resolves.toBeUndefined();
      expect(client.processId).toBeNull();
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });

  it('starts each transport deadline only when the serial worker can dispatch it', async () => {
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-queue-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', serialWorkerScript(90)],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'queue-test.jsonl')),
      () => undefined,
      {
        ...WORKER_OPERATION_POLICIES,
        'project.createBackup': {
          domainDeadlineMs: 110,
          transportTimeoutMs: 140,
          terminateWorkerOnTimeout: true,
        },
      },
    );
    try {
      await client.start();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const first = client.request('project.createBackup', {});
      const second = client.request('project.createBackup', {});

      await expect(Promise.all([first, second])).resolves.toHaveLength(2);
      expect(client.processId).not.toBeNull();
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });

  it('drains an active stateful operation before controlled restart', async () => {
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-restart-drain-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', serialWorkerScript(100)],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'restart-drain-test.jsonl')),
      () => undefined,
      {
        ...WORKER_OPERATION_POLICIES,
        'project.createBackup': {
          domainDeadlineMs: 150,
          transportTimeoutMs: 180,
          terminateWorkerOnTimeout: true,
        },
      },
    );
    try {
      await client.start();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const originalProcessId = client.processId;
      const backup = client.request('project.createBackup', {});
      const restart = client.restart();

      await expect(backup).resolves.toMatchObject({ ok: true });
      await expect(restart).resolves.toBeUndefined();
      expect(client.processId).not.toBeNull();
      expect(client.processId).not.toBe(originalProcessId);
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });

  it('does not start a replacement worker when final shutdown overtakes restart', async () => {
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-restart-cancel-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', serialWorkerScript(100)],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'restart-cancel-test.jsonl')),
      () => undefined,
      {
        ...WORKER_OPERATION_POLICIES,
        'project.createBackup': {
          domainDeadlineMs: 150,
          transportTimeoutMs: 180,
          terminateWorkerOnTimeout: true,
        },
      },
    );
    try {
      await client.start();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const backup = client.request('project.createBackup', {});
      const restart = client.restart();
      const finalShutdown = client.shutdown();

      await expect(backup).resolves.toMatchObject({ ok: true });
      await expect(Promise.all([restart, finalShutdown])).resolves.toHaveLength(2);
      expect(client.processId).toBeNull();
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });

  it('bounds accepted work to one active and one queued request', async () => {
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-backpressure-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', serialWorkerScript(70)],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'backpressure-test.jsonl')),
      () => undefined,
      {
        ...WORKER_OPERATION_POLICIES,
        'project.createBackup': {
          domainDeadlineMs: 250,
          transportTimeoutMs: 300,
          terminateWorkerOnTimeout: true,
        },
      },
    );
    try {
      await client.start();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const first = client.request('project.createBackup', {});
      const second = client.request('project.createBackup', {});

      await expect(client.request('project.createBackup', {})).rejects.toThrow('worker_queue_full');
      await expect(Promise.all([first, second])).resolves.toHaveLength(2);
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });

  it('rejects an oversized UTF-8 JSONL request before writing to the worker', async () => {
    const stateDirectory = mkdtempSync(join(tmpdir(), 'impeller-worker-request-size-'));
    const client = new WorkerClient(
      {
        command: process.execPath,
        arguments: ['-e', serialWorkerScript(0)],
        cwd: stateDirectory,
        executablePath: null,
      },
      stateDirectory,
      new JsonlLogger(join(stateDirectory, 'request-size-test.jsonl')),
      () => undefined,
    );
    try {
      await client.start();
      const decisions = Array.from({ length: 100 }, (_, index) => ({
        observationVersionId: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
        decision: 'excluded' as const,
        reason: '\u0000'.repeat(2_000),
      }));
      await expect(
        client.request('reliabilityDataset.createVersion', {
          datasetId: '00000000-0000-4000-8000-000000000101',
          datasetVersionId: '00000000-0000-4000-8000-000000000102',
          wheelModelId: '00000000-0000-4000-8000-000000000103',
          expectedPreviousVersionId: null,
          title: 'Oversized request',
          method: 'rbd',
          metricKind: 'rbd_steady_rotation_time',
          metricUnit: 'hours',
          populationBasis: '\u0000'.repeat(2_000),
          methodologyBasis: '\u0000'.repeat(2_000),
          comparabilityBasis: '\u0000'.repeat(2_000),
          decisions,
          actor: 'local_user',
          reason: '\u0000'.repeat(2_000),
        }),
      ).rejects.toThrow('worker_request_too_large:reliabilityDataset.createVersion');
      expect(client.processId).not.toBeNull();
    } finally {
      await client.shutdown();
      rmSync(stateDirectory, { recursive: true, force: true });
    }
  });
});
