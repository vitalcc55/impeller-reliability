import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';

import { IPC_PROTOCOL_VERSION, workerResponseSchema } from '@impeller-reliability/contracts';
import type { WorkerRequest, WorkerResponse } from '@impeller-reliability/contracts';

import type { JsonlLogger } from './logging';
import { assertWorkerIntegrity } from './worker-integrity';
import { createWorkerEnvironment } from './worker-location';
import type { WorkerLocation } from './worker-location';

interface PendingRequest {
  readonly resolve: (response: WorkerResponse) => void;
  readonly reject: (error: Error) => void;
  readonly timeout: NodeJS.Timeout;
}

const MAX_MESSAGE_BYTES = 1_048_576;

export class WorkerClient {
  readonly #pending = new Map<string, PendingRequest>();
  #process: ChildProcessWithoutNullStreams | null = null;
  #revision = 0;

  public constructor(
    private readonly location: WorkerLocation,
    private readonly stateDirectory: string,
    private readonly logger: JsonlLogger,
  ) {}

  public async start(): Promise<void> {
    if (this.#process !== null) return;
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
    child.once('error', (error) => this.#failAll(error));
    child.once('close', (code, signal) => {
      this.#process = null;
      this.#failAll(new Error(`worker_closed:${String(code)}:${String(signal)}`));
    });
  }

  public async request(
    operation: WorkerRequest['operation'],
    deadlineMs = 5_000,
  ): Promise<WorkerResponse> {
    const child = this.#process;
    if (child === null) throw new Error('worker_unavailable');
    const requestId = randomUUID();
    const request = {
      protocolVersion: IPC_PROTOCOL_VERSION,
      requestId,
      kind: 'request',
      operation,
      revision: this.#revision++,
      deadlineMs,
      payload: {},
    } satisfies WorkerRequest;
    return new Promise<WorkerResponse>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.#pending.delete(requestId);
        reject(new Error('worker_timeout'));
      }, deadlineMs);
      this.#pending.set(requestId, { resolve, reject, timeout });
      child.stdin.write(`${JSON.stringify(request)}\n`, 'utf8');
    });
  }

  public async shutdown(timeoutMs = 3_000): Promise<void> {
    const child = this.#process;
    if (child === null) return;
    try {
      await this.request('system.shutdown', timeoutMs);
    } catch (error) {
      await this.logger.write({
        severity: 'warning',
        component: 'worker',
        event: 'shutdown_failed',
        details: { error: String(error) },
      });
    }
    await Promise.race([
      new Promise<void>((resolve) => child.once('close', () => resolve())),
      new Promise<void>((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
    if (this.#process !== null) child.kill('SIGKILL');
  }

  #handleLine(line: string): void {
    if (Buffer.byteLength(line, 'utf8') > MAX_MESSAGE_BYTES) {
      this.#failAll(new Error('worker_message_too_large'));
      return;
    }
    let rawResponse: unknown;
    try {
      rawResponse = JSON.parse(line);
    } catch {
      this.#failAll(new Error('worker_invalid_json'));
      return;
    }
    const parsed = workerResponseSchema.safeParse(rawResponse);
    if (!parsed.success) {
      this.#failAll(new Error('worker_contract_error'));
      return;
    }
    const pending = this.#pending.get(parsed.data.requestId);
    if (pending === undefined) return;
    clearTimeout(pending.timeout);
    this.#pending.delete(parsed.data.requestId);
    pending.resolve(parsed.data);
  }

  #failAll(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
    this.#pending.clear();
  }
}
