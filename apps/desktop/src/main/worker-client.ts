import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';

import { createRevisionGate } from '@impeller-reliability/application';
import {
  IPC_PROTOCOL_VERSION,
  parseWorkerResponse,
  workerRequestSchema,
  workerResponseIdentitySchema,
} from '@impeller-reliability/contracts';
import type {
  WorkerOperationMap,
  WorkerLifecycleState,
  WorkerOperation,
  WorkerResponse,
  WorkerResponseFor,
} from '@impeller-reliability/contracts';

import type { JsonlLogger } from './logging';
import { assertWorkerIntegrity } from './worker-integrity';
import { createWorkerEnvironment } from './worker-location';
import type { WorkerLocation } from './worker-location';

interface PendingRequest {
  readonly operation: WorkerOperation;
  readonly revision: number;
  readonly resolve: (response: WorkerResponse) => void;
  readonly reject: (error: Error) => void;
  readonly timeout: NodeJS.Timeout;
}

export interface WorkerOperationPolicy {
  readonly domainDeadlineMs: number;
  readonly transportTimeoutMs: number;
  readonly terminateWorkerOnTimeout: boolean;
}

export const WORKER_OPERATION_POLICIES = {
  'system.handshake': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'system.ping': {
    domainDeadlineMs: 3_000,
    transportTimeoutMs: 5_000,
    terminateWorkerOnTimeout: false,
  },
  'system.shutdown': {
    domainDeadlineMs: 2_000,
    transportTimeoutMs: 3_000,
    terminateWorkerOnTimeout: false,
  },
  'storage.health': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'project.create': {
    domainDeadlineMs: 15_000,
    transportTimeoutMs: 18_000,
    terminateWorkerOnTimeout: true,
  },
  'project.open': {
    domainDeadlineMs: 15_000,
    transportTimeoutMs: 18_000,
    terminateWorkerOnTimeout: true,
  },
  'project.close': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'project.getOverview': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'project.updateMetadata': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'project.createBackup': {
    domainDeadlineMs: 25_000,
    transportTimeoutMs: 28_000,
    terminateWorkerOnTimeout: true,
  },
  'caseCustomer.get': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'caseCustomer.upsert': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'wheelModel.create': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'wheelModel.list': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'wheelModel.get': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'wheelModel.update': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'wheelModel.archive': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'wheelModel.restore': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'specimen.create': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'specimen.list': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'specimen.get': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'specimen.update': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'specimen.archive': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'specimen.restore': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.create': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.createWithFile': {
    domainDeadlineMs: 30_000,
    transportTimeoutMs: 35_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.list': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'caseDocument.get': {
    domainDeadlineMs: 30_000,
    transportTimeoutMs: 35_000,
    terminateWorkerOnTimeout: false,
  },
  'caseDocument.update': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.attachFile': {
    domainDeadlineMs: 30_000,
    transportTimeoutMs: 35_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.verifyFile': {
    domainDeadlineMs: 30_000,
    transportTimeoutMs: 35_000,
    terminateWorkerOnTimeout: false,
  },
  'caseDocument.archive': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.restore': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: true,
  },
  'caseDocument.resolveFile': {
    domainDeadlineMs: 30_000,
    transportTimeoutMs: 35_000,
    terminateWorkerOnTimeout: false,
  },
  'runPackageValidation.start': {
    domainDeadlineMs: 5_000,
    transportTimeoutMs: 7_000,
    terminateWorkerOnTimeout: false,
  },
  'runPackageValidation.get': {
    domainDeadlineMs: 3_000,
    transportTimeoutMs: 5_000,
    terminateWorkerOnTimeout: false,
  },
  'runPackageValidation.cancel': {
    domainDeadlineMs: 3_000,
    transportTimeoutMs: 5_000,
    terminateWorkerOnTimeout: false,
  },
  'runPackageValidation.discard': {
    domainDeadlineMs: 3_000,
    transportTimeoutMs: 5_000,
    terminateWorkerOnTimeout: false,
  },
} as const satisfies Readonly<Record<WorkerOperation, WorkerOperationPolicy>>;

export interface WorkerLifecycleEvent {
  readonly state: WorkerLifecycleState;
  readonly reason: string | null;
}

const MAX_MESSAGE_BYTES = 1_048_576;
const MAX_ACCEPTED_REQUESTS = 2;

export class WorkerClient {
  readonly #pending = new Map<string, PendingRequest>();
  readonly #expectedStops = new WeakSet<ChildProcessWithoutNullStreams>();
  readonly #idleWaiters = new Set<() => void>();
  #process: ChildProcessWithoutNullStreams | null = null;
  #requestQueue: Promise<void> = Promise.resolve();
  #queuedRequestCount = 0;
  #workerGeneration = 0;
  #revision = 0;
  #state: WorkerLifecycleState = 'stopped';
  #acceptingRequests = false;
  #finalShutdownRequested = false;
  #startPromise: Promise<void> | null = null;
  #restartPromise: Promise<void> | null = null;
  #shutdownPromise: Promise<void> | null = null;

  public constructor(
    private readonly location: WorkerLocation,
    private readonly stateDirectory: string,
    private readonly logger: JsonlLogger,
    private readonly onLifecycleChange: (event: WorkerLifecycleEvent) => void,
    private readonly operationPolicies: Readonly<
      Record<WorkerOperation, WorkerOperationPolicy>
    > = WORKER_OPERATION_POLICIES,
  ) {}

  public get processId(): number | null {
    return this.#process?.pid ?? null;
  }

  public start(): Promise<void> {
    if (this.#process !== null) return Promise.resolve();
    if (this.#startPromise !== null) return this.#startPromise;
    const startPromise = this.#startInternal()
      .catch((error: unknown) => {
        this.#emitLifecycle('unavailable', String(error));
        throw error;
      })
      .finally(() => {
        if (this.#startPromise === startPromise) this.#startPromise = null;
      });
    this.#startPromise = startPromise;
    return startPromise;
  }

  public markReady(): void {
    if (this.#process !== null) this.#emitLifecycle('ready', null);
  }

  public request(
    operation: 'system.handshake',
    payload: WorkerOperationMap['system.handshake']['request'],
  ): Promise<WorkerResponseFor<'system.handshake'>>;
  public request(
    operation: 'system.ping',
    payload: WorkerOperationMap['system.ping']['request'],
  ): Promise<WorkerResponseFor<'system.ping'>>;
  public request(
    operation: 'system.shutdown',
    payload: WorkerOperationMap['system.shutdown']['request'],
  ): Promise<WorkerResponseFor<'system.shutdown'>>;
  public request(
    operation: 'storage.health',
    payload: WorkerOperationMap['storage.health']['request'],
  ): Promise<WorkerResponseFor<'storage.health'>>;
  public request(
    operation: 'project.create',
    payload: WorkerOperationMap['project.create']['request'],
  ): Promise<WorkerResponseFor<'project.create'>>;
  public request(
    operation: 'project.open',
    payload: WorkerOperationMap['project.open']['request'],
  ): Promise<WorkerResponseFor<'project.open'>>;
  public request(
    operation: 'project.close',
    payload: WorkerOperationMap['project.close']['request'],
  ): Promise<WorkerResponseFor<'project.close'>>;
  public request(
    operation: 'project.getOverview',
    payload: WorkerOperationMap['project.getOverview']['request'],
  ): Promise<WorkerResponseFor<'project.getOverview'>>;
  public request(
    operation: 'project.updateMetadata',
    payload: WorkerOperationMap['project.updateMetadata']['request'],
  ): Promise<WorkerResponseFor<'project.updateMetadata'>>;
  public request(
    operation: 'project.createBackup',
    payload: WorkerOperationMap['project.createBackup']['request'],
  ): Promise<WorkerResponseFor<'project.createBackup'>>;
  public request<TOperation extends WorkerOperation>(
    operation: TOperation,
    payload: WorkerOperationMap[TOperation]['request'],
  ): Promise<WorkerResponseFor<TOperation>>;
  public request(
    operation: WorkerOperation,
    payload: WorkerOperationMap[WorkerOperation]['request'],
  ): Promise<WorkerResponse> {
    const child = this.#process;
    if (child === null) return Promise.reject(new Error('worker_unavailable'));
    if (!this.#acceptingRequests && operation !== 'system.shutdown') {
      return Promise.reject(new Error('worker_stopping'));
    }
    if (this.#queuedRequestCount >= MAX_ACCEPTED_REQUESTS) {
      return Promise.reject(new Error('worker_queue_full'));
    }
    const generation = this.#workerGeneration;
    this.#queuedRequestCount += 1;
    const execute = async (): Promise<WorkerResponse> => {
      try {
        if (this.#process !== child || this.#workerGeneration !== generation) {
          throw new Error('worker_unavailable');
        }
        return await this.#dispatchRequest(child, operation, payload);
      } finally {
        this.#queuedRequestCount -= 1;
        this.#notifyIdle();
      }
    };
    const result = this.#requestQueue.then(execute, execute);
    this.#requestQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  #dispatchRequest(
    child: ChildProcessWithoutNullStreams,
    operation: WorkerOperation,
    payload: WorkerOperationMap[WorkerOperation]['request'],
  ): Promise<WorkerResponse> {
    const requestId = randomUUID();
    const revision = this.#revision++;
    const policy = this.operationPolicies[operation];
    const request = workerRequestSchema.parse({
      protocolVersion: IPC_PROTOCOL_VERSION,
      requestId,
      kind: 'request',
      operation,
      revision,
      deadlineMs: policy.domainDeadlineMs,
      payload,
    });
    return new Promise<WorkerResponse>((resolve, reject) => {
      const timeout = setTimeout(() => {
        const pending = this.#removePending(requestId);
        if (pending === undefined) return;
        const error = new Error(`worker_transport_timeout:${operation}`);
        pending.reject(error);
        if (policy.terminateWorkerOnTimeout) this.#terminateAfterTimeout(child, error);
      }, policy.transportTimeoutMs);
      this.#pending.set(requestId, { operation, revision, resolve, reject, timeout });
      child.stdin.write(`${JSON.stringify(request)}\n`, 'utf8', (error) => {
        if (error === null || error === undefined) return;
        const pending = this.#removePending(requestId);
        if (pending === undefined) return;
        clearTimeout(pending.timeout);
        pending.reject(error);
      });
    });
  }

  public restart(): Promise<void> {
    if (this.#finalShutdownRequested) return Promise.reject(new Error('worker_stopping'));
    if (this.#restartPromise !== null) return this.#restartPromise;
    const restartPromise = this.#restartInternal().finally(() => {
      if (this.#restartPromise === restartPromise) this.#restartPromise = null;
    });
    this.#restartPromise = restartPromise;
    return restartPromise;
  }

  public shutdown(exitTimeoutMs = 3_000): Promise<void> {
    this.#finalShutdownRequested = true;
    return this.#beginShutdown(exitTimeoutMs);
  }

  #beginShutdown(exitTimeoutMs: number): Promise<void> {
    if (this.#shutdownPromise !== null) return this.#shutdownPromise;
    const shutdownPromise = this.#shutdownInternal(exitTimeoutMs).finally(() => {
      if (this.#shutdownPromise === shutdownPromise) this.#shutdownPromise = null;
    });
    this.#shutdownPromise = shutdownPromise;
    return shutdownPromise;
  }

  async #shutdownInternal(exitTimeoutMs: number): Promise<void> {
    const child = this.#process;
    if (child === null) {
      this.#acceptingRequests = false;
      this.#emitLifecycle('stopped', null);
      return;
    }
    this.#acceptingRequests = false;
    this.#expectedStops.add(child);
    this.#emitLifecycle('stopping', null);
    await this.#waitForIdle();
    if (this.#process !== child) return;
    const closePromise = new Promise<void>((resolve) => child.once('close', () => resolve()));
    try {
      await this.request('system.shutdown', {});
    } catch (error) {
      await this.logger.write({
        severity: 'warning',
        component: 'worker',
        event: 'shutdown_failed',
        details: { error: String(error) },
      });
    }
    await Promise.race([
      closePromise,
      new Promise<void>((resolve) => setTimeout(resolve, exitTimeoutMs)),
    ]);
    if (this.#process === child) {
      child.kill('SIGKILL');
      await Promise.race([
        closePromise,
        new Promise<void>((resolve) => setTimeout(resolve, 1_000)),
      ]);
    }
    if (this.#process === child) throw new Error('worker_shutdown_timeout');
  }

  async #startInternal(): Promise<void> {
    this.#emitLifecycle('starting', null);
    if (this.location.executablePath !== null) {
      await assertWorkerIntegrity(this.location.executablePath);
    }
    const child = spawn(this.location.command, [...this.location.arguments], {
      cwd: this.location.cwd,
      env: createWorkerEnvironment(process.env, this.stateDirectory),
      shell: false,
      stdio: 'pipe',
      windowsHide: true,
    });
    this.#process = child;
    this.#workerGeneration += 1;
    createInterface({ input: child.stdout, crlfDelay: Infinity }).on('line', (line) => {
      this.#handleLine(line);
    });
    createInterface({ input: child.stderr, crlfDelay: Infinity }).on('line', (line) => {
      void this.logger.write({
        severity: 'info',
        component: 'worker',
        event: 'stderr',
        details: { line },
      });
    });
    child.once('error', (error) => this.#handleTermination(child, error));
    child.once('close', (code, signal) => {
      this.#handleTermination(child, new Error(`worker_closed:${String(code)}:${String(signal)}`));
    });
    await new Promise<void>((resolve, reject) => {
      const onSpawn = (): void => {
        child.off('error', onError);
        resolve();
      };
      const onError = (error: Error): void => {
        child.off('spawn', onSpawn);
        reject(error);
      };
      child.once('spawn', onSpawn);
      child.once('error', onError);
    });
    if (this.#process === child && this.#shutdownPromise === null) {
      this.#acceptingRequests = true;
    }
  }

  async #restartInternal(): Promise<void> {
    await this.#beginShutdown(3_000);
    if (this.#finalShutdownRequested) return;
    await this.start();
  }

  #handleLine(line: string): void {
    if (Buffer.byteLength(line, 'utf8') > MAX_MESSAGE_BYTES) {
      this.#failProtocol(new Error('worker_message_too_large'));
      return;
    }
    let rawResponse: unknown;
    try {
      rawResponse = JSON.parse(line);
    } catch {
      this.#failProtocol(new Error('worker_invalid_json'));
      return;
    }
    const identity = workerResponseIdentitySchema.safeParse(rawResponse);
    if (!identity.success) {
      this.#failProtocol(new Error('worker_contract_error'));
      return;
    }
    const pending = this.#pending.get(identity.data.requestId);
    if (pending === undefined) return;
    let response: WorkerResponse;
    try {
      response = parseWorkerResponse(pending.operation, rawResponse);
    } catch {
      this.#failProtocol(new Error('worker_contract_error'));
      return;
    }
    if (!createRevisionGate(pending.revision).accepts(response.revision)) {
      clearTimeout(pending.timeout);
      this.#removePending(identity.data.requestId);
      pending.reject(new Error('worker_stale_revision'));
      return;
    }
    clearTimeout(pending.timeout);
    this.#removePending(identity.data.requestId);
    pending.resolve(response);
  }

  #failProtocol(error: Error): void {
    this.#acceptingRequests = false;
    this.#workerGeneration += 1;
    this.#failAll(error);
    const child = this.#process;
    if (child !== null) child.kill('SIGKILL');
    this.#emitLifecycle('unavailable', error.message);
  }

  #terminateAfterTimeout(child: ChildProcessWithoutNullStreams, error: Error): void {
    this.#acceptingRequests = false;
    this.#workerGeneration += 1;
    this.#failAll(error);
    if (this.#process === child) child.kill('SIGKILL');
    this.#emitLifecycle('unavailable', error.message);
  }

  #handleTermination(child: ChildProcessWithoutNullStreams, error: Error): void {
    const expected = this.#expectedStops.has(child);
    if (this.#process === child) {
      this.#process = null;
      this.#acceptingRequests = false;
      this.#workerGeneration += 1;
    }
    this.#failAll(error);
    this.#emitLifecycle(expected ? 'stopped' : 'unavailable', expected ? null : error.message);
  }

  #failAll(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
    this.#pending.clear();
    this.#notifyIdle();
  }

  #waitForIdle(): Promise<void> {
    if (this.#pending.size === 0 && this.#queuedRequestCount === 0) return Promise.resolve();
    return new Promise<void>((resolve) => this.#idleWaiters.add(resolve));
  }

  #removePending(requestId: string): PendingRequest | undefined {
    const pending = this.#pending.get(requestId);
    if (pending === undefined) return undefined;
    this.#pending.delete(requestId);
    this.#notifyIdle();
    return pending;
  }

  #notifyIdle(): void {
    if (this.#pending.size !== 0 || this.#queuedRequestCount !== 0) return;
    for (const resolve of this.#idleWaiters) resolve();
    this.#idleWaiters.clear();
  }

  #emitLifecycle(state: WorkerLifecycleState, reason: string | null): void {
    if (this.#state === state && reason === null) return;
    this.#state = state;
    this.onLifecycleChange({ state, reason });
  }
}
