import { z } from 'zod';

export const IPC_PROTOCOL_VERSION = 1 as const;

const emptyPayloadSchema = z.object({}).strict();

export const workerOperationSchema = z.enum([
  'system.handshake',
  'system.ping',
  'system.shutdown',
  'storage.health',
  'project.create',
  'project.open',
  'project.close',
  'project.getOverview',
  'project.updateMetadata',
  'project.createBackup',
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

export const projectStatusSchema = z.enum(['draft', 'active', 'completed', 'archived']);
export const projectDraftSchema = z
  .object({
    name: z.string().trim().min(1).max(200),
    projectNumber: z.string().trim().max(100),
    description: z.string().trim().max(4_000),
    status: projectStatusSchema,
  })
  .strict();
export const projectCreatePayloadSchema = z
  .object({
    path: z.string().min(1).max(32_767),
    applicationInstanceId: z.string().min(1).max(128),
    applicationVersion: z.string().min(1).max(64),
    draft: projectDraftSchema,
  })
  .strict();
export const projectOpenPayloadSchema = z
  .object({
    path: z.string().min(1).max(32_767),
    applicationInstanceId: z.string().min(1).max(128),
  })
  .strict();
export const projectUpdateMetadataPayloadSchema = z
  .object({
    expectedRevision: z.number().int().positive(),
    metadata: projectDraftSchema,
  })
  .strict();
export const projectOverviewSchema = z
  .object({
    projectId: z.string().uuid(),
    path: z.string().min(1),
    name: z.string().min(1).max(200),
    projectNumber: z.string().max(100),
    description: z.string().max(4_000),
    status: projectStatusSchema,
    recordRevision: z.number().int().positive(),
    createdAtUtc: z.string().min(1),
    updatedAtUtc: z.string().min(1),
    createdWithApplicationVersion: z.string().min(1),
    schemaVersion: z.number().int().positive(),
  })
  .strict();
export const projectCloseResultSchema = z.object({ closed: z.boolean() }).strict();
export const projectBackupResultSchema = z
  .object({
    fileName: z.string().min(1),
    sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    createdAtUtc: z.string().min(1),
  })
  .strict();

export type EmptyWorkerPayload = z.infer<typeof emptyPayloadSchema>;
export type HandshakeResult = z.infer<typeof handshakeResultSchema>;
export type PingResult = z.infer<typeof pingResultSchema>;
export type ShutdownResult = z.infer<typeof shutdownResultSchema>;
export type StorageHealthResult = z.infer<typeof storageHealthResultSchema>;
export type ProjectStatus = z.infer<typeof projectStatusSchema>;
export type ProjectDraft = z.infer<typeof projectDraftSchema>;
export type ProjectOverview = z.infer<typeof projectOverviewSchema>;
export type ProjectBackupResult = z.infer<typeof projectBackupResultSchema>;

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
  readonly 'project.create': {
    readonly request: z.infer<typeof projectCreatePayloadSchema>;
    readonly result: ProjectOverview;
  };
  readonly 'project.open': {
    readonly request: z.infer<typeof projectOpenPayloadSchema>;
    readonly result: ProjectOverview;
  };
  readonly 'project.close': {
    readonly request: EmptyWorkerPayload;
    readonly result: z.infer<typeof projectCloseResultSchema>;
  };
  readonly 'project.getOverview': {
    readonly request: EmptyWorkerPayload;
    readonly result: ProjectOverview;
  };
  readonly 'project.updateMetadata': {
    readonly request: z.infer<typeof projectUpdateMetadataPayloadSchema>;
    readonly result: ProjectOverview;
  };
  readonly 'project.createBackup': {
    readonly request: EmptyWorkerPayload;
    readonly result: ProjectBackupResult;
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
  requestBaseSchema
    .extend({ operation: z.literal('project.create'), payload: projectCreatePayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('project.open'), payload: projectOpenPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('project.close'), payload: emptyPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('project.getOverview'), payload: emptyPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('project.updateMetadata'),
      payload: projectUpdateMetadataPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('project.createBackup'), payload: emptyPayloadSchema })
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
      'project_locked',
      'corrupt_project',
      'incompatible_schema',
      'revision_conflict',
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
export const projectOverviewSuccessResponseSchema =
  createSuccessResponseSchema(projectOverviewSchema);
export const projectCloseSuccessResponseSchema =
  createSuccessResponseSchema(projectCloseResultSchema);
export const projectBackupSuccessResponseSchema =
  createSuccessResponseSchema(projectBackupResultSchema);
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
const projectOverviewResponseSchema = z.union([
  projectOverviewSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const projectCloseResponseSchema = z.union([
  projectCloseSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const projectBackupResponseSchema = z.union([
  projectBackupSuccessResponseSchema,
  workerErrorResponseSchema,
]);

export type WorkerRequest = z.infer<typeof workerRequestSchema>;
export type WorkerErrorResponse = z.infer<typeof workerErrorResponseSchema>;

export interface WorkerResponseMap {
  readonly 'system.handshake': z.infer<typeof handshakeResponseSchema>;
  readonly 'system.ping': z.infer<typeof pingResponseSchema>;
  readonly 'system.shutdown': z.infer<typeof shutdownResponseSchema>;
  readonly 'storage.health': z.infer<typeof storageHealthResponseSchema>;
  readonly 'project.create': z.infer<typeof projectOverviewResponseSchema>;
  readonly 'project.open': z.infer<typeof projectOverviewResponseSchema>;
  readonly 'project.close': z.infer<typeof projectCloseResponseSchema>;
  readonly 'project.getOverview': z.infer<typeof projectOverviewResponseSchema>;
  readonly 'project.updateMetadata': z.infer<typeof projectOverviewResponseSchema>;
  readonly 'project.createBackup': z.infer<typeof projectBackupResponseSchema>;
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
export function parseWorkerResponse(
  operation: 'project.create',
  input: unknown,
): WorkerResponseMap['project.create'];
export function parseWorkerResponse(
  operation: 'project.open',
  input: unknown,
): WorkerResponseMap['project.open'];
export function parseWorkerResponse(
  operation: 'project.close',
  input: unknown,
): WorkerResponseMap['project.close'];
export function parseWorkerResponse(
  operation: 'project.getOverview',
  input: unknown,
): WorkerResponseMap['project.getOverview'];
export function parseWorkerResponse(
  operation: 'project.updateMetadata',
  input: unknown,
): WorkerResponseMap['project.updateMetadata'];
export function parseWorkerResponse(
  operation: 'project.createBackup',
  input: unknown,
): WorkerResponseMap['project.createBackup'];
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
    case 'project.create':
    case 'project.open':
    case 'project.getOverview':
    case 'project.updateMetadata':
      return projectOverviewResponseSchema.parse(input);
    case 'project.close':
      return projectCloseResponseSchema.parse(input);
    case 'project.createBackup':
      return projectBackupResponseSchema.parse(input);
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

export const desktopErrorSchema = z
  .object({
    code: z.enum([
      'cancelled',
      'contract_error',
      'validation_error',
      'domain_error',
      'project_locked',
      'corrupt_project',
      'incompatible_schema',
      'revision_conflict',
      'storage_error',
      'worker_unavailable',
      'timeout',
      'internal_error',
    ]),
    message: z.string(),
    details: z.record(z.string(), z.unknown()),
    retryable: z.boolean(),
  })
  .strict();

export type DesktopError = z.infer<typeof desktopErrorSchema>;
export type DesktopResult<TResult> =
  | { readonly ok: true; readonly result: TResult }
  | { readonly ok: false; readonly error: DesktopError };

export const createDesktopResultSchema = <TResult extends z.ZodType>(result: TResult) =>
  z.discriminatedUnion('ok', [
    z.object({ ok: z.literal(true), result }).strict(),
    z.object({ ok: z.literal(false), error: desktopErrorSchema }).strict(),
  ]);

export const recentProjectSchema = z
  .object({
    path: z.string().min(1),
    name: z.string().min(1),
    projectNumber: z.string(),
    lastOpenedAtUtc: z.string().min(1),
  })
  .strict();
export const recentProjectsSchema = z.array(recentProjectSchema);
export type RecentProject = z.infer<typeof recentProjectSchema>;

export type ProjectMetadataCommand = {
  readonly expectedRevision: number;
  readonly metadata: ProjectDraft;
};

export interface ImpellerApi {
  readonly system: {
    getStatus(): Promise<RuntimeStatus>;
    ping(): Promise<RuntimeStatus>;
    restart(): Promise<RuntimeStatus>;
    openLog(): Promise<void>;
    subscribeStatus(listener: (status: RuntimeStatus) => void): () => void;
  };
  readonly project: {
    create(draft: ProjectDraft): Promise<DesktopResult<ProjectOverview>>;
    open(): Promise<DesktopResult<ProjectOverview>>;
    openRecent(path: string): Promise<DesktopResult<ProjectOverview>>;
    close(): Promise<DesktopResult<{ readonly closed: boolean }>>;
    getOverview(): Promise<DesktopResult<ProjectOverview>>;
    updateMetadata(command: ProjectMetadataCommand): Promise<DesktopResult<ProjectOverview>>;
    createBackup(): Promise<DesktopResult<ProjectBackupResult>>;
    listRecent(): Promise<DesktopResult<readonly RecentProject[]>>;
  };
}
