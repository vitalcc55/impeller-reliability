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
  'caseCustomer.get',
  'caseCustomer.upsert',
  'wheelModel.create',
  'wheelModel.list',
  'wheelModel.get',
  'wheelModel.update',
  'wheelModel.archive',
  'wheelModel.restore',
  'specimen.create',
  'specimen.list',
  'specimen.get',
  'specimen.update',
  'specimen.archive',
  'specimen.restore',
  'caseDocument.create',
  'caseDocument.createWithFile',
  'caseDocument.list',
  'caseDocument.get',
  'caseDocument.update',
  'caseDocument.attachFile',
  'caseDocument.verifyFile',
  'caseDocument.archive',
  'caseDocument.restore',
  'caseDocument.resolveFile',
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
export const projectIdSchema = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
export const entityIdSchema = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
const applicationVersionSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/u);
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
    applicationVersion: applicationVersionSchema,
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
const canonicalUtcTimestampSchema = z
  .string()
  .regex(/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$/u)
  .refine((value) => {
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value;
  });
export const projectOverviewSchema = z
  .object({
    projectId: projectIdSchema,
    path: z.string().min(1),
    name: z.string().min(1).max(200),
    projectNumber: z.string().max(100),
    description: z.string().max(4_000),
    status: projectStatusSchema,
    recordRevision: z.number().int().positive(),
    createdAtUtc: canonicalUtcTimestampSchema,
    updatedAtUtc: canonicalUtcTimestampSchema,
    createdWithApplicationVersion: applicationVersionSchema,
    schemaVersion: z.number().int().positive(),
  })
  .strict();
export const projectCloseResultSchema = z.object({ closed: z.boolean() }).strict();
export const projectBackupResultSchema = z
  .object({
    fileName: z.string().min(1),
    sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    createdAtUtc: canonicalUtcTimestampSchema,
  })
  .strict();

export const completenessWarningSchema = z.enum([
  'customer_address_missing',
  'wheel_nominal_diameter_missing',
  'wheel_nominal_speed_missing',
  'specimen_working_diameter_missing',
]);
const optionalCanonicalDecimalSchema = z
  .union([z.string(), z.null()])
  .transform((value, context): string | null => {
    if (value === null || value.trim() === '') return null;
    const normalizedInput = value.trim().replace(',', '.');
    if (
      normalizedInput.length > 64 ||
      !/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/u.test(normalizedInput) ||
      !/[1-9]/u.test(normalizedInput)
    ) {
      context.addIssue({ code: 'custom', message: 'Введите положительное число.' });
      return z.NEVER;
    }
    const [integer = '', fraction] = normalizedInput.split('.');
    const normalizedInteger = integer.replace(/^0+(?=\d)/u, '') || '0';
    const normalizedFraction = fraction?.replace(/0+$/u, '');
    return normalizedFraction === undefined || normalizedFraction === ''
      ? normalizedInteger
      : `${normalizedInteger}.${normalizedFraction}`;
  });
const optionalDateSchema = z
  .union([z.string(), z.null()])
  .transform((value): string | null =>
    value === null || value.trim() === '' ? null : value.trim(),
  )
  .pipe(
    z
      .string()
      .regex(/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/u)
      .refine((value) => {
        if (value.startsWith('0000-')) return false;
        const parsed = new Date(`${value}T00:00:00.000Z`);
        return Number.isFinite(parsed.getTime()) && parsed.toISOString().startsWith(value);
      })
      .nullable(),
  );

export const customerDraftSchema = z
  .object({
    fullName: z.string().trim().min(1).max(300),
    legalAddress: z.string().trim().max(1_000),
    actualAddress: z.string().trim().max(1_000),
    notes: z.string().trim().max(4_000),
  })
  .strict();
export const customerUpsertPayloadSchema = z
  .object({
    expectedRevision: z.number().int().positive().nullable(),
    customer: customerDraftSchema,
  })
  .strict();
export const customerProfileSchema = customerDraftSchema
  .extend({
    projectId: projectIdSchema,
    recordRevision: z.number().int().positive(),
    createdAtUtc: canonicalUtcTimestampSchema,
    updatedAtUtc: canonicalUtcTimestampSchema,
    warnings: z.array(completenessWarningSchema),
  })
  .strict();
export const customerGetResultSchema = z
  .object({ customer: customerProfileSchema.nullable() })
  .strict();

export const wheelModelDraftSchema = z
  .object({
    fullName: z.string().trim().min(1).max(300),
    designation: z.string().trim().max(200),
    nominalDiameterMm: optionalCanonicalDecimalSchema,
    nominalSpeedRpm: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).nullable(),
    bladeCount: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).nullable(),
    geometryDescription: z.string().trim().max(4_000),
    compositionDescription: z.string().trim().max(4_000),
    materialDescription: z.string().trim().max(4_000),
    notes: z.string().trim().max(4_000),
  })
  .strict();
export const wheelModelCreatePayloadSchema = wheelModelDraftSchema
  .extend({ wheelModelId: entityIdSchema })
  .strict();
export const wheelModelSchema = wheelModelDraftSchema
  .extend({
    wheelModelId: entityIdSchema,
    recordRevision: z.number().int().positive(),
    archivedAtUtc: canonicalUtcTimestampSchema.nullable(),
    createdAtUtc: canonicalUtcTimestampSchema,
    updatedAtUtc: canonicalUtcTimestampSchema,
    warnings: z.array(completenessWarningSchema),
  })
  .strict();
export const wheelModelSummarySchema = z
  .object({
    wheelModelId: entityIdSchema,
    fullName: z.string(),
    designation: z.string(),
    recordRevision: z.number().int().positive(),
    archivedAtUtc: canonicalUtcTimestampSchema.nullable(),
    warnings: z.array(completenessWarningSchema),
  })
  .strict();
export const wheelModelListResultSchema = z
  .object({ items: z.array(wheelModelSummarySchema) })
  .strict();
export const wheelModelListPayloadSchema = z.object({ includeArchived: z.boolean() }).strict();
export const wheelModelIdPayloadSchema = z.object({ wheelModelId: entityIdSchema }).strict();
export const wheelModelUpdatePayloadSchema = wheelModelIdPayloadSchema
  .extend({ expectedRevision: z.number().int().positive(), wheelModel: wheelModelDraftSchema })
  .strict();
export const wheelModelRevisionPayloadSchema = wheelModelIdPayloadSchema
  .extend({ expectedRevision: z.number().int().positive() })
  .strict();

export const specimenDraftSchema = z
  .object({
    wheelModelId: entityIdSchema,
    identificationNumber: z.string().trim().min(1).max(200),
    batchNumber: z.string().trim().max(200),
    marking: z.string().trim().max(500),
    manufacturedOn: optionalDateSchema,
    receivedOn: optionalDateSchema,
    workingDiameterMm: optionalCanonicalDecimalSchema,
    initialConditionNotes: z.string().trim().max(4_000),
    notes: z.string().trim().max(4_000),
  })
  .strict();
export const specimenCreatePayloadSchema = specimenDraftSchema
  .extend({ specimenId: entityIdSchema })
  .strict();
export const specimenSchema = specimenDraftSchema
  .extend({
    specimenId: entityIdSchema,
    wheelModelName: z.string(),
    recordRevision: z.number().int().positive(),
    archivedAtUtc: canonicalUtcTimestampSchema.nullable(),
    createdAtUtc: canonicalUtcTimestampSchema,
    updatedAtUtc: canonicalUtcTimestampSchema,
    warnings: z.array(completenessWarningSchema),
  })
  .strict();
export const specimenSummarySchema = z
  .object({
    specimenId: entityIdSchema,
    wheelModelId: entityIdSchema,
    wheelModelName: z.string(),
    identificationNumber: z.string(),
    recordRevision: z.number().int().positive(),
    archivedAtUtc: canonicalUtcTimestampSchema.nullable(),
    warnings: z.array(completenessWarningSchema),
  })
  .strict();
export const specimenListResultSchema = z
  .object({ items: z.array(specimenSummarySchema) })
  .strict();
export const specimenListPayloadSchema = z.object({ includeArchived: z.boolean() }).strict();
export const specimenIdPayloadSchema = z.object({ specimenId: entityIdSchema }).strict();
export const specimenUpdatePayloadSchema = specimenIdPayloadSchema
  .extend({ expectedRevision: z.number().int().positive(), specimen: specimenDraftSchema })
  .strict();
export const specimenRevisionPayloadSchema = specimenIdPayloadSchema
  .extend({ expectedRevision: z.number().int().positive() })
  .strict();

export const caseDocumentKindSchema = z.enum([
  'technical_specification',
  'individual_test_method',
  'typical_test_method',
  'customer_requirement',
  'test_request',
  'operational_documentation',
  'standard',
  'drawing',
  'measurement_or_attestation_record',
  'other',
]);
export const caseDocumentIntegrityStatusSchema = z.enum([
  'not_attached',
  'verified',
  'missing',
  'modified',
  'verification_error',
]);
export const caseDocumentWarningSchema = z.enum([
  'case_document_file_missing',
  'case_document_designation_missing',
  'case_document_revision_missing',
]);
export const caseDocumentDraftSchema = z
  .object({
    documentKind: caseDocumentKindSchema,
    title: z.string().trim().min(1).max(300),
    designation: z.string().trim().max(200),
    revisionLabel: z.string().trim().max(200),
    documentDate: optionalDateSchema,
    issuer: z.string().trim().max(300),
    notes: z.string().trim().max(4_000),
  })
  .strict();
export const caseDocumentCreateCommandSchema = z
  .object({
    caseDocumentId: entityIdSchema,
    document: caseDocumentDraftSchema,
    wheelModelIds: z.array(entityIdSchema),
    specimenIds: z.array(entityIdSchema),
  })
  .strict();
export const caseDocumentCreateWithFilePayloadSchema = caseDocumentCreateCommandSchema
  .extend({ sourcePath: z.string().min(1).max(32_767) })
  .strict();
export const caseDocumentIdPayloadSchema = z.object({ caseDocumentId: entityIdSchema }).strict();
export const caseDocumentListPayloadSchema = z
  .object({
    includeArchived: z.boolean(),
    documentKind: caseDocumentKindSchema.nullable(),
  })
  .strict();
export const caseDocumentUpdatePayloadSchema = caseDocumentCreateCommandSchema
  .extend({ expectedRevision: z.number().int().positive() })
  .strict();
export const caseDocumentAttachFileCommandSchema = caseDocumentIdPayloadSchema
  .extend({ expectedRevision: z.number().int().positive() })
  .strict();
export const caseDocumentAttachFilePayloadSchema = caseDocumentAttachFileCommandSchema
  .extend({ sourcePath: z.string().min(1).max(32_767) })
  .strict();
export const caseDocumentRevisionPayloadSchema = caseDocumentIdPayloadSchema
  .extend({ expectedRevision: z.number().int().positive() })
  .strict();
export const caseDocumentFileSchema = z
  .object({
    originalFileName: z
      .string()
      .min(1)
      .max(255)
      .regex(/^[^/\\]+$/u)
      .refine((value) => value !== '.' && value !== '..'),
    mediaType: z.string().min(1).max(128),
    sizeBytes: z
      .number()
      .int()
      .positive()
      .max(100 * 1024 * 1024),
    sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    attachedAtUtc: canonicalUtcTimestampSchema,
  })
  .strict();
export const caseDocumentSchema = caseDocumentDraftSchema
  .extend({
    caseDocumentId: entityIdSchema,
    recordRevision: z.number().int().positive(),
    archivedAtUtc: canonicalUtcTimestampSchema.nullable(),
    createdAtUtc: canonicalUtcTimestampSchema,
    updatedAtUtc: canonicalUtcTimestampSchema,
    file: caseDocumentFileSchema.nullable(),
    integrityStatus: caseDocumentIntegrityStatusSchema,
    wheelModelIds: z.array(entityIdSchema),
    specimenIds: z.array(entityIdSchema),
    warnings: z.array(caseDocumentWarningSchema),
  })
  .strict();
export const caseDocumentSummarySchema = z
  .object({
    caseDocumentId: entityIdSchema,
    documentKind: caseDocumentKindSchema,
    title: z.string(),
    designation: z.string(),
    recordRevision: z.number().int().positive(),
    archivedAtUtc: canonicalUtcTimestampSchema.nullable(),
    warnings: z.array(caseDocumentWarningSchema),
  })
  .strict();
export const caseDocumentListResultSchema = z
  .object({ items: z.array(caseDocumentSummarySchema) })
  .strict();
export const caseDocumentResolveFileResultSchema = z
  .object({ absolutePath: z.string().min(1).max(32_767) })
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
export type CompletenessWarning = z.infer<typeof completenessWarningSchema>;
export type CustomerDraft = z.infer<typeof customerDraftSchema>;
export type CustomerProfile = z.infer<typeof customerProfileSchema>;
export type WheelModelDraft = z.infer<typeof wheelModelDraftSchema>;
export type WheelModelCreateCommand = z.infer<typeof wheelModelCreatePayloadSchema>;
export type WheelModel = z.infer<typeof wheelModelSchema>;
export type WheelModelSummary = z.infer<typeof wheelModelSummarySchema>;
export type SpecimenDraft = z.infer<typeof specimenDraftSchema>;
export type SpecimenCreateCommand = z.infer<typeof specimenCreatePayloadSchema>;
export type Specimen = z.infer<typeof specimenSchema>;
export type SpecimenSummary = z.infer<typeof specimenSummarySchema>;
export type CaseDocumentKind = z.infer<typeof caseDocumentKindSchema>;
export type CaseDocumentIntegrityStatus = z.infer<typeof caseDocumentIntegrityStatusSchema>;
export type CaseDocumentWarning = z.infer<typeof caseDocumentWarningSchema>;
export type CaseDocumentDraft = z.infer<typeof caseDocumentDraftSchema>;
export type CaseDocumentCreateCommand = z.infer<typeof caseDocumentCreateCommandSchema>;
export type CaseDocument = z.infer<typeof caseDocumentSchema>;
export type CaseDocumentSummary = z.infer<typeof caseDocumentSummarySchema>;

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
  readonly 'caseCustomer.get': {
    readonly request: EmptyWorkerPayload;
    readonly result: z.infer<typeof customerGetResultSchema>;
  };
  readonly 'caseCustomer.upsert': {
    readonly request: z.infer<typeof customerUpsertPayloadSchema>;
    readonly result: CustomerProfile;
  };
  readonly 'wheelModel.create': {
    readonly request: WheelModelCreateCommand;
    readonly result: WheelModel;
  };
  readonly 'wheelModel.list': {
    readonly request: z.infer<typeof wheelModelListPayloadSchema>;
    readonly result: z.infer<typeof wheelModelListResultSchema>;
  };
  readonly 'wheelModel.get': {
    readonly request: z.infer<typeof wheelModelIdPayloadSchema>;
    readonly result: WheelModel;
  };
  readonly 'wheelModel.update': {
    readonly request: z.infer<typeof wheelModelUpdatePayloadSchema>;
    readonly result: WheelModel;
  };
  readonly 'wheelModel.archive': {
    readonly request: z.infer<typeof wheelModelRevisionPayloadSchema>;
    readonly result: WheelModel;
  };
  readonly 'wheelModel.restore': {
    readonly request: z.infer<typeof wheelModelRevisionPayloadSchema>;
    readonly result: WheelModel;
  };
  readonly 'specimen.create': {
    readonly request: SpecimenCreateCommand;
    readonly result: Specimen;
  };
  readonly 'specimen.list': {
    readonly request: z.infer<typeof specimenListPayloadSchema>;
    readonly result: z.infer<typeof specimenListResultSchema>;
  };
  readonly 'specimen.get': {
    readonly request: z.infer<typeof specimenIdPayloadSchema>;
    readonly result: Specimen;
  };
  readonly 'specimen.update': {
    readonly request: z.infer<typeof specimenUpdatePayloadSchema>;
    readonly result: Specimen;
  };
  readonly 'specimen.archive': {
    readonly request: z.infer<typeof specimenRevisionPayloadSchema>;
    readonly result: Specimen;
  };
  readonly 'specimen.restore': {
    readonly request: z.infer<typeof specimenRevisionPayloadSchema>;
    readonly result: Specimen;
  };
  readonly 'caseDocument.create': {
    readonly request: CaseDocumentCreateCommand;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.createWithFile': {
    readonly request: z.infer<typeof caseDocumentCreateWithFilePayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.list': {
    readonly request: z.infer<typeof caseDocumentListPayloadSchema>;
    readonly result: z.infer<typeof caseDocumentListResultSchema>;
  };
  readonly 'caseDocument.get': {
    readonly request: z.infer<typeof caseDocumentIdPayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.update': {
    readonly request: z.infer<typeof caseDocumentUpdatePayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.attachFile': {
    readonly request: z.infer<typeof caseDocumentAttachFilePayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.verifyFile': {
    readonly request: z.infer<typeof caseDocumentIdPayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.archive': {
    readonly request: z.infer<typeof caseDocumentRevisionPayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.restore': {
    readonly request: z.infer<typeof caseDocumentRevisionPayloadSchema>;
    readonly result: CaseDocument;
  };
  readonly 'caseDocument.resolveFile': {
    readonly request: z.infer<typeof caseDocumentIdPayloadSchema>;
    readonly result: z.infer<typeof caseDocumentResolveFileResultSchema>;
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
  requestBaseSchema
    .extend({ operation: z.literal('caseCustomer.get'), payload: emptyPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('caseCustomer.upsert'), payload: customerUpsertPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('wheelModel.create'), payload: wheelModelCreatePayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('wheelModel.list'), payload: wheelModelListPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('wheelModel.get'), payload: wheelModelIdPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('wheelModel.update'), payload: wheelModelUpdatePayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('wheelModel.archive'),
      payload: wheelModelRevisionPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('wheelModel.restore'),
      payload: wheelModelRevisionPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('specimen.create'), payload: specimenCreatePayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('specimen.list'), payload: specimenListPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('specimen.get'), payload: specimenIdPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('specimen.update'), payload: specimenUpdatePayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('specimen.archive'), payload: specimenRevisionPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('specimen.restore'), payload: specimenRevisionPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.create'),
      payload: caseDocumentCreateCommandSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.createWithFile'),
      payload: caseDocumentCreateWithFilePayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('caseDocument.list'), payload: caseDocumentListPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({ operation: z.literal('caseDocument.get'), payload: caseDocumentIdPayloadSchema })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.update'),
      payload: caseDocumentUpdatePayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.attachFile'),
      payload: caseDocumentAttachFilePayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.verifyFile'),
      payload: caseDocumentIdPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.archive'),
      payload: caseDocumentRevisionPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.restore'),
      payload: caseDocumentRevisionPayloadSchema,
    })
    .strict(),
  requestBaseSchema
    .extend({
      operation: z.literal('caseDocument.resolveFile'),
      payload: caseDocumentIdPayloadSchema,
    })
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
      'entity_not_found',
      'entity_archived',
      'entity_in_use',
      'duplicate_entity',
      'duplicate_document_content',
      'file_already_attached',
      'unsupported_file_type',
      'file_too_large',
      'file_missing',
      'file_integrity_mismatch',
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
export const customerGetSuccessResponseSchema =
  createSuccessResponseSchema(customerGetResultSchema);
export const customerSuccessResponseSchema = createSuccessResponseSchema(customerProfileSchema);
export const wheelModelSuccessResponseSchema = createSuccessResponseSchema(wheelModelSchema);
export const wheelModelListSuccessResponseSchema = createSuccessResponseSchema(
  wheelModelListResultSchema,
);
export const specimenSuccessResponseSchema = createSuccessResponseSchema(specimenSchema);
export const specimenListSuccessResponseSchema =
  createSuccessResponseSchema(specimenListResultSchema);
export const caseDocumentSuccessResponseSchema = createSuccessResponseSchema(caseDocumentSchema);
export const caseDocumentListSuccessResponseSchema = createSuccessResponseSchema(
  caseDocumentListResultSchema,
);
export const caseDocumentResolveFileSuccessResponseSchema = createSuccessResponseSchema(
  caseDocumentResolveFileResultSchema,
);
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
const customerGetResponseSchema = z.union([
  customerGetSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const customerResponseSchema = z.union([customerSuccessResponseSchema, workerErrorResponseSchema]);
const wheelModelResponseSchema = z.union([
  wheelModelSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const wheelModelListResponseSchema = z.union([
  wheelModelListSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const specimenResponseSchema = z.union([specimenSuccessResponseSchema, workerErrorResponseSchema]);
const specimenListResponseSchema = z.union([
  specimenListSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const caseDocumentResponseSchema = z.union([
  caseDocumentSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const caseDocumentListResponseSchema = z.union([
  caseDocumentListSuccessResponseSchema,
  workerErrorResponseSchema,
]);
const caseDocumentResolveFileResponseSchema = z.union([
  caseDocumentResolveFileSuccessResponseSchema,
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
  readonly 'caseCustomer.get': z.infer<typeof customerGetResponseSchema>;
  readonly 'caseCustomer.upsert': z.infer<typeof customerResponseSchema>;
  readonly 'wheelModel.create': z.infer<typeof wheelModelResponseSchema>;
  readonly 'wheelModel.list': z.infer<typeof wheelModelListResponseSchema>;
  readonly 'wheelModel.get': z.infer<typeof wheelModelResponseSchema>;
  readonly 'wheelModel.update': z.infer<typeof wheelModelResponseSchema>;
  readonly 'wheelModel.archive': z.infer<typeof wheelModelResponseSchema>;
  readonly 'wheelModel.restore': z.infer<typeof wheelModelResponseSchema>;
  readonly 'specimen.create': z.infer<typeof specimenResponseSchema>;
  readonly 'specimen.list': z.infer<typeof specimenListResponseSchema>;
  readonly 'specimen.get': z.infer<typeof specimenResponseSchema>;
  readonly 'specimen.update': z.infer<typeof specimenResponseSchema>;
  readonly 'specimen.archive': z.infer<typeof specimenResponseSchema>;
  readonly 'specimen.restore': z.infer<typeof specimenResponseSchema>;
  readonly 'caseDocument.create': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.createWithFile': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.list': z.infer<typeof caseDocumentListResponseSchema>;
  readonly 'caseDocument.get': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.update': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.attachFile': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.verifyFile': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.archive': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.restore': z.infer<typeof caseDocumentResponseSchema>;
  readonly 'caseDocument.resolveFile': z.infer<typeof caseDocumentResolveFileResponseSchema>;
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
    case 'caseCustomer.get':
      return customerGetResponseSchema.parse(input);
    case 'caseCustomer.upsert':
      return customerResponseSchema.parse(input);
    case 'wheelModel.create':
    case 'wheelModel.get':
    case 'wheelModel.update':
    case 'wheelModel.archive':
    case 'wheelModel.restore':
      return wheelModelResponseSchema.parse(input);
    case 'wheelModel.list':
      return wheelModelListResponseSchema.parse(input);
    case 'specimen.create':
    case 'specimen.get':
    case 'specimen.update':
    case 'specimen.archive':
    case 'specimen.restore':
      return specimenResponseSchema.parse(input);
    case 'specimen.list':
      return specimenListResponseSchema.parse(input);
    case 'caseDocument.create':
    case 'caseDocument.createWithFile':
    case 'caseDocument.get':
    case 'caseDocument.update':
    case 'caseDocument.attachFile':
    case 'caseDocument.verifyFile':
    case 'caseDocument.archive':
    case 'caseDocument.restore':
      return caseDocumentResponseSchema.parse(input);
    case 'caseDocument.list':
      return caseDocumentListResponseSchema.parse(input);
    case 'caseDocument.resolveFile':
      return caseDocumentResolveFileResponseSchema.parse(input);
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
      'entity_not_found',
      'entity_archived',
      'entity_in_use',
      'duplicate_entity',
      'duplicate_document_content',
      'file_already_attached',
      'unsupported_file_type',
      'file_too_large',
      'file_missing',
      'file_integrity_mismatch',
      'operation_in_progress',
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
    lastOpenedAtUtc: canonicalUtcTimestampSchema,
  })
  .strict();
export const recentProjectsSchema = z.array(recentProjectSchema);
export type RecentProject = z.infer<typeof recentProjectSchema>;

export type ProjectMetadataCommand = {
  readonly expectedRevision: number;
  readonly metadata: ProjectDraft;
};
export type CustomerUpsertCommand = z.infer<typeof customerUpsertPayloadSchema>;
export type WheelModelUpdateCommand = z.infer<typeof wheelModelUpdatePayloadSchema>;
export type WheelModelRevisionCommand = z.infer<typeof wheelModelRevisionPayloadSchema>;
export type SpecimenUpdateCommand = z.infer<typeof specimenUpdatePayloadSchema>;
export type SpecimenRevisionCommand = z.infer<typeof specimenRevisionPayloadSchema>;
export type CaseDocumentUpdateCommand = z.infer<typeof caseDocumentUpdatePayloadSchema>;
export type CaseDocumentAttachFileCommand = z.infer<typeof caseDocumentAttachFileCommandSchema>;
export type CaseDocumentRevisionCommand = z.infer<typeof caseDocumentRevisionPayloadSchema>;
export type CaseDocumentListQuery = z.infer<typeof caseDocumentListPayloadSchema>;

export interface ImpellerApi {
  readonly system: {
    getStatus(): Promise<RuntimeStatus>;
    ping(): Promise<RuntimeStatus>;
    restart(): Promise<RuntimeStatus>;
    openLog(): Promise<void>;
    confirmClose(): Promise<void>;
    cancelClose(): Promise<void>;
    subscribeStatus(listener: (status: RuntimeStatus) => void): () => void;
    subscribeCloseRequested(listener: () => void): () => void;
  };
  readonly project: {
    create(draft: ProjectDraft): Promise<DesktopResult<ProjectOverview>>;
    open(): Promise<DesktopResult<ProjectOverview>>;
    openRecent(path: string): Promise<DesktopResult<ProjectOverview>>;
    close(): Promise<DesktopResult<{ readonly closed: boolean }>>;
    releaseLocalWorkspace(): Promise<void>;
    getOverview(): Promise<DesktopResult<ProjectOverview>>;
    updateMetadata(command: ProjectMetadataCommand): Promise<DesktopResult<ProjectOverview>>;
    createBackup(): Promise<DesktopResult<ProjectBackupResult>>;
    listRecent(): Promise<DesktopResult<readonly RecentProject[]>>;
  };
  readonly caseCustomer: {
    get(): Promise<DesktopResult<CustomerProfile | null>>;
    upsert(command: CustomerUpsertCommand): Promise<DesktopResult<CustomerProfile>>;
  };
  readonly wheelModel: {
    create(command: WheelModelCreateCommand): Promise<DesktopResult<WheelModel>>;
    list(includeArchived: boolean): Promise<DesktopResult<readonly WheelModelSummary[]>>;
    get(wheelModelId: string): Promise<DesktopResult<WheelModel>>;
    update(command: WheelModelUpdateCommand): Promise<DesktopResult<WheelModel>>;
    archive(command: WheelModelRevisionCommand): Promise<DesktopResult<WheelModel>>;
    restore(command: WheelModelRevisionCommand): Promise<DesktopResult<WheelModel>>;
  };
  readonly specimen: {
    create(command: SpecimenCreateCommand): Promise<DesktopResult<Specimen>>;
    list(includeArchived: boolean): Promise<DesktopResult<readonly SpecimenSummary[]>>;
    get(specimenId: string): Promise<DesktopResult<Specimen>>;
    update(command: SpecimenUpdateCommand): Promise<DesktopResult<Specimen>>;
    archive(command: SpecimenRevisionCommand): Promise<DesktopResult<Specimen>>;
    restore(command: SpecimenRevisionCommand): Promise<DesktopResult<Specimen>>;
  };
  readonly caseDocument: {
    create(command: CaseDocumentCreateCommand): Promise<DesktopResult<CaseDocument>>;
    createWithFile(command: CaseDocumentCreateCommand): Promise<DesktopResult<CaseDocument>>;
    list(query: CaseDocumentListQuery): Promise<DesktopResult<readonly CaseDocumentSummary[]>>;
    get(caseDocumentId: string): Promise<DesktopResult<CaseDocument>>;
    update(command: CaseDocumentUpdateCommand): Promise<DesktopResult<CaseDocument>>;
    attachFile(command: CaseDocumentAttachFileCommand): Promise<DesktopResult<CaseDocument>>;
    verifyFile(caseDocumentId: string): Promise<DesktopResult<CaseDocument>>;
    openFile(caseDocumentId: string): Promise<DesktopResult<{ readonly opened: boolean }>>;
    archive(command: CaseDocumentRevisionCommand): Promise<DesktopResult<CaseDocument>>;
    restore(command: CaseDocumentRevisionCommand): Promise<DesktopResult<CaseDocument>>;
  };
}
