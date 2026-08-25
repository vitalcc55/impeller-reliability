import { z } from 'zod';

export const IPC_PROTOCOL_VERSION = 1 as const;

const emptyPayloadSchema = z.object({}).strict();

export const workerOperationSchema = z.enum([
  'system.handshake',
  'system.ping',
  'system.shutdown',
  'storage.health',
]);

export type WorkerOperation = z.infer<typeof workerOperationSchema>;

export const handshakeResultSchema = z
  .object({
    workerVersion: z.string(),
    protocolVersions: z.array(z.number().int().positive()),
    pythonVersion: z.string(),
    numpyVersion: z.string(),
    scipyVersion: z.string(),
    databaseSchemaVersions: z.array(z.number().int().nonnegative()),
    algorithmVersions: z.record(z.string(), z.string()),
    supportedRunPackageSchemas: z.array(z.string()),
    supportedPlanSchemas: z.array(z.string()),
    capabilities: z.array(workerOperationSchema),
  })
  .strict();

export const pingResultSchema = z.object({ pong: z.literal(true) }).strict();
export const shutdownResultSchema = z.object({ accepted: z.literal(true) }).strict();
export const storageHealthResultSchema = z
  .object({
    status: z.enum(['ok', 'error']),
    databaseSchemaVersion: z.number().int().nonnegative(),
    quickCheck: z.string(),
    foreignKeys: z.boolean(),
    journalMode: z.string(),
  })
  .strict();

export type EmptyWorkerPayload = z.infer<typeof emptyPayloadSchema>;
export type HandshakeResult = z.infer<typeof handshakeResultSchema>;
export type PingResult = z.infer<typeof pingResultSchema>;
export type ShutdownResult = z.infer<typeof shutdownResultSchema>;
export type StorageHealthResult = z.infer<typeof storageHealthResultSchema>;

export interface WorkerOperationMap {
  readonly 'system.handshake': {
    readonly request: EmptyWorkerPayload;
    readonly result: HandshakeResult;
  };
  readonly 'system.ping': {
    readonly request: EmptyWorkerPayload;
    readonly result: PingResult;
  };
  readonly 'system.shutdown': {
    readonly request: EmptyWorkerPayload;
    readonly result: ShutdownResult;
  };
  readonly 'storage.health': {
    readonly request: EmptyWorkerPayload;
    readonly result: StorageHealthResult;
  };
}

const requestBaseSchema = z.object({
  protocolVersion: z.literal(IPC_PROTOCOL_VERSION),
  requestId: z.string().min(1),
  kind: z.literal('request'),
  revision: z.number().int().nonnegative(),
  deadlineMs: z.number().int().positive().max(30_000),
});

export const workerRequestSchema = z.discriminatedUnion('operation', [
  requestBaseSchema
    .extend({
      operation: z.literal('system.handshake'),
      payload: emptyPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('system.ping'), payload: emptyPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('system.shutdown'),
      payload: emptyPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('storage.health'), payload: emptyPayloadSchema })
    .strict(),
]);

export const workerErrorSchema = z
  .object({
    code: z.enum([
      'contract_error',
      'validation_error',
      'domain_error',
      'storage_error',
      'cancelled',
      'timeout',
      'worker_unavailable',
      'internal_error',
    ]),
    message: z.string(),
    details: z.record(z.string(), z.unknown()),
    retryable: z.boolean(),
  })
  .strict();

const responseBaseSchema = z.object({
  protocolVersion: z.literal(IPC_PROTOCOL_VERSION),
  requestId: z.string().min(1),
  revision: z.number().int().nonnegative(),
  kind: z.literal('response'),
});

export const workerResponseIdentitySchema = responseBaseSchema.passthrough();

const createSuccessResponseSchema = <TResult extends z.ZodType>(result: TResult) =>
  responseBaseSchema
    .extend({
      ok: z.literal(true),
      result,
      evidence: z.record(z.string(), z.unknown()),
      warnings: z.array(z.string()),
    })
    .strict();

export const handshakeSuccessResponseSchema = createSuccessResponseSchema(handshakeResultSchema);
export const pingSuccessResponseSchema = createSuccessResponseSchema(pingResultSchema);
export const shutdownSuccessResponseSchema = createSuccessResponseSchema(shutdownResultSchema);
export const storageHealthSuccessResponseSchema =
  createSuccessResponseSchema(storageHealthResultSchema);
export const workerErrorResponseSchema = responseBaseSchema
  .extend({
    ok: z.literal(false),
    error: workerErrorSchema,
  })
  .strict();

const handshakeResponseSchema = z.union([
  handshakeSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const pingResponseSchema = z.union([pingSuccessResponseSchema, workerErrorResponseSchema]);
const shutdownResponseSchema = z.union([shutdownSuccessResponseSchema, workerErrorResponseSchema]);
const storageHealthResponseSchema = z.union([
  storageHealthSuccessResponseSchema,
  workerErrorResponseSchema,
]);

export type WorkerRequest = z.infer<typeof workerRequestSchema>;
export type WorkerErrorResponse = z.infer<typeof workerErrorResponseSchema>;

export interface WorkerResponseMap {
  readonly 'system.handshake': z.infer<typeof handshakeResponseSchema>;
  readonly 'system.ping': z.infer<typeof pingResponseSchema>;
  readonly 'system.shutdown': z.infer<typeof shutdownResponseSchema>;
  readonly 'storage.health': z.infer<typeof storageHealthResponseSchema>;
}

export type WorkerResponseFor<TOperation extends WorkerOperation> = WorkerResponseMap[TOperation];
export type WorkerResponse = WorkerResponseMap[WorkerOperation];

export function parseWorkerResponse(
  operation: 'system.handshake',
  input: unknown,
): WorkerResponseMap['system.handshake'];
export function parseWorkerResponse(
  operation: 'system.ping',
  input: unknown,
): WorkerResponseMap['system.ping'];
export function parseWorkerResponse(
  operation: 'system.shutdown',
  input: unknown,
): WorkerResponseMap['system.shutdown'];
export function parseWorkerResponse(
  operation: 'storage.health',
  input: unknown,
): WorkerResponseMap['storage.health'];
export function parseWorkerResponse(operation: WorkerOperation, input: unknown): WorkerResponse;
export function parseWorkerResponse(operation: WorkerOperation, input: unknown): WorkerResponse {
  switch (operation) {
    case 'system.handshake':
      return handshakeResponseSchema.parse(input);
    case 'system.ping':
      return pingResponseSchema.parse(input);
    case 'system.shutdown':
      return shutdownResponseSchema.parse(input);
    case 'storage.health':
      return storageHealthResponseSchema.parse(input);
  }
}

export type WorkerLifecycleState = 'starting' | 'ready' | 'unavailable' | 'stopping' | 'stopped';

export const runtimeStatusSchema = z
  .object({
    applicationVersion: z.string(),
    electronVersion: z.string(),
    workerStatus: z.enum(['starting', 'ready', 'unavailable', 'stopping', 'stopped']),
    workerVersion: z.string().nullable(),
    protocolVersion: z.number().int().nullable(),
    sqliteStatus: z.enum(['pending', 'ok', 'error']),
    mode: z.enum(['development', 'packaged']),
    message: z.string(),
  })
  .strict();

export type RuntimeStatus = z.infer<typeof runtimeStatusSchema>;

export interface ImpellerApi {
  readonly system: {
    getStatus(): Promise<RuntimeStatus>;
    ping(): Promise<RuntimeStatus>;
    restart(): Promise<RuntimeStatus>;
    openLog(): Promise<void>;
    subscribeStatus(listener: (status: RuntimeStatus) => void): () => void;
  };
}
