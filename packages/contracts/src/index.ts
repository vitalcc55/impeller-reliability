import { z } from 'zod';

export const IPC_PROTOCOL_VERSION = 1 as const;

const requestBaseSchema = z.object({
  protocolVersion: z.literal(IPC_PROTOCOL_VERSION),
  requestId: z.string().min(1),
  kind: z.literal('request'),
  revision: z.number().int().nonnegative(),
  deadlineMs: z.number().int().positive().max(30_000),
});

export const workerRequestSchema = z.discriminatedUnion('operation', [
  requestBaseSchema.extend({ operation: z.literal('system.handshake'), payload: z.object({}) }),
  requestBaseSchema.extend({ operation: z.literal('system.ping'), payload: z.object({}) }),
  requestBaseSchema.extend({ operation: z.literal('system.shutdown'), payload: z.object({}) }),
  requestBaseSchema.extend({ operation: z.literal('storage.health'), payload: z.object({}) }),
]);

export const workerErrorSchema = z.object({
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
});

const responseBaseSchema = z.object({
  protocolVersion: z.literal(IPC_PROTOCOL_VERSION),
  requestId: z.string().min(1),
  kind: z.literal('response'),
});

export const workerResponseSchema = z.discriminatedUnion('ok', [
  responseBaseSchema.extend({
    ok: z.literal(true),
    result: z.record(z.string(), z.unknown()),
    evidence: z.record(z.string(), z.unknown()),
    warnings: z.array(z.string()),
  }),
  responseBaseSchema.extend({ ok: z.literal(false), error: workerErrorSchema }),
]);

export const runtimeStatusSchema = z.object({
  applicationVersion: z.string(),
  electronVersion: z.string(),
  workerStatus: z.enum(['starting', 'ready', 'unavailable', 'stopped']),
  workerVersion: z.string().nullable(),
  protocolVersion: z.number().int().nullable(),
  sqliteStatus: z.enum(['pending', 'ok', 'error']),
  mode: z.enum(['development', 'packaged']),
  message: z.string(),
});

export type WorkerRequest = z.infer<typeof workerRequestSchema>;
export type WorkerResponse = z.infer<typeof workerResponseSchema>;
export type RuntimeStatus = z.infer<typeof runtimeStatusSchema>;

export interface ImpellerApi {
  readonly system: {
    getStatus(): Promise<RuntimeStatus>;
    ping(): Promise<RuntimeStatus>;
    openLog(): Promise<void>;
  };
}
