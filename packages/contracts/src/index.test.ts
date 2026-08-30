import { describe, expect, it } from 'vitest';

import {
  parseWorkerResponse,
  customerUpsertPayloadSchema,
  customerProfileSchema,
  caseDocumentCreateCommandSchema,
  caseDocumentSchema,
  entityIdSchema,
  projectIdSchema,
  projectBackupResultSchema,
  projectOverviewSchema,
  runIdSchema,
  runPackageIdSchema,
  runPackageValidationJobSchema,
  runPackageValidationReportSchema,
  runPackageValidationStartCommandSchema,
  runPackageValidationStartPayloadSchema,
  runtimeStatusSchema,
  specimenDraftSchema,
  wheelModelDraftSchema,
  workerRequestSchema,
} from './index';

describe('worker contracts', () => {
  it('accepts canonical RFC 4122 project IDs across versions without weakening entity IDs', () => {
    const projectId = '019c89f0-0b57-7ef5-9656-595184fcb272';
    expect(projectIdSchema.parse(projectId)).toBe(projectId);
    expect(entityIdSchema.safeParse(projectId).success).toBe(false);
    expect(
      customerProfileSchema.parse({
        projectId,
        fullName: 'Заказчик',
        legalAddress: '',
        actualAddress: '',
        notes: '',
        recordRevision: 1,
        createdAtUtc: '2026-08-26T00:00:00.000Z',
        updatedAtUtc: '2026-08-26T00:00:00.000Z',
        warnings: [],
      }).projectId,
    ).toBe(projectId);
  });

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

  it('separates upstream UUID v4/v7 identities from local entity IDs', () => {
    const packageId = '019d3c80-3d21-7a65-8e5a-111111111111';
    const runId = '8ab377f2-cfd8-4983-86ea-25f5d0171bd7';
    expect(runPackageIdSchema.parse(packageId)).toBe(packageId);
    expect(runIdSchema.parse(runId)).toBe(runId);
    expect(entityIdSchema.safeParse(packageId).success).toBe(false);
    expect(runPackageIdSchema.safeParse('5ab377f2-cfd8-1983-86ea-25f5d0171bd7').success).toBe(
      false,
    );
  });

  it('keeps source path and validation budget out of the Renderer start command', () => {
    const command = { jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212' };
    expect(runPackageValidationStartCommandSchema.parse(command)).toEqual(command);
    expect(
      runPackageValidationStartCommandSchema.safeParse({
        ...command,
        sourcePath: 'C:\\secret.r130run',
        validationBudgetMs: 1_800_000,
      }).success,
    ).toBe(false);
    expect(
      runPackageValidationStartPayloadSchema.parse({
        ...command,
        sourcePath: 'C:\\approved.r130run',
        validationBudgetMs: 1_800_000,
      }).validationBudgetMs,
    ).toBe(1_800_000);
    for (const invalidBudget of [0, 999, 1_800_001, 1.5]) {
      expect(
        runPackageValidationStartPayloadSchema.safeParse({
          ...command,
          sourcePath: 'C:\\approved.r130run',
          validationBudgetMs: invalidBudget,
        }).success,
      ).toBe(false);
    }
  });

  it('validates terminal job invariants and rejects path or import claims in reports', () => {
    const report = {
      validatorVersion: 'm03a.1',
      validationLevel: 'synthetic_contract_foundation',
      upstreamRepository: 'https://github.com/vitalcc55/R130SH',
      upstreamCommit: 'f02f6d954246a5ab6f57d33dac724ce03d7fb841',
      contractSchema: 'r130sh.run-package.v1',
      sourceFileName: 'candidate.r130run',
      outerPackageSha256: 'a'.repeat(64),
      outerSizeBytes: 1024,
      packageId: '019d3c80-3d21-7a65-8e5a-111111111111',
      exportRevision: 1,
      runId: '019d3c80-3d21-7a65-8e5a-222222222222',
      packageKind: 'final',
      producer: {
        name: 'R130SH',
        version: 'synthetic',
        buildId: 'fixture',
        gitCommit: 'f02f6d954246a5ab6f57d33dac724ce03d7fb841',
      },
      entryCount: 15,
      declaredPayloadBytes: 512,
      validatedPayloadBytes: 512,
      structuralVerdict: 'passed',
      semanticVerdict: 'partial',
      semanticCoverage: [
        { area: 'manifest', status: 'covered', contractSource: 'manifest-example' },
      ],
      findingCounts: { error: 0, warning: 0, info: 0, total: 0, truncated: false },
      findings: [],
      startedAtUtc: '2026-08-29T12:00:00.000Z',
      finishedAtUtc: '2026-08-29T12:00:01.000Z',
    } as const;
    expect(runPackageValidationReportSchema.parse(report).structuralVerdict).toBe('passed');
    expect(
      runPackageValidationReportSchema.safeParse({
        ...report,
        exportRevision: Number.MAX_SAFE_INTEGER + 1,
      }).success,
    ).toBe(false);
    expect(
      runPackageValidationReportSchema.safeParse({
        ...report,
        sourcePath: 'C:\\secret.r130run',
      }).success,
    ).toBe(false);
    expect(runPackageValidationReportSchema.safeParse({ ...report, imported: true }).success).toBe(
      false,
    );
    const completed = {
      jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
      state: 'completed',
      phase: 'finalizing',
      progress: {
        kind: 'known',
        completedBytes: 1024,
        totalBytes: 1024,
        completedEntries: 15,
        totalEntries: 15,
      },
      startedAtUtc: '2026-08-29T12:00:00.000Z',
      finishedAtUtc: '2026-08-29T12:00:01.000Z',
      report,
      typedError: null,
    } as const;
    expect(runPackageValidationJobSchema.parse(completed).state).toBe('completed');
    expect(
      runPackageValidationJobSchema.safeParse({
        ...completed,
        progress: { ...completed.progress, completedBytes: 1025 },
      }).success,
    ).toBe(false);
    expect(runPackageValidationJobSchema.safeParse({ ...completed, report: null }).success).toBe(
      false,
    );
  });

  it('keeps case-document source paths out of Renderer commands and DTOs', () => {
    const command = {
      caseDocumentId: '113ec2c8-9439-4ce8-823d-3e2b0de8f001',
      document: {
        documentKind: 'standard',
        title: ' ГОСТ ',
        designation: '',
        revisionLabel: '',
        documentDate: null,
        issuer: '',
        notes: '',
      },
      wheelModelIds: [],
      specimenIds: [],
    } as const;
    expect(caseDocumentCreateCommandSchema.parse(command).document.title).toBe('ГОСТ');
    expect(
      caseDocumentCreateCommandSchema.safeParse({ ...command, sourcePath: 'C:\\secret.pdf' })
        .success,
    ).toBe(false);
    expect(
      workerRequestSchema.safeParse({
        protocolVersion: 1,
        requestId: 'document-1',
        kind: 'request',
        operation: 'caseDocument.createWithFile',
        revision: 1,
        deadlineMs: 30_000,
        payload: { ...command, sourcePath: 'C:\\approved.pdf' },
      }).success,
    ).toBe(true);
    const result = {
      ...command.document,
      caseDocumentId: command.caseDocumentId,
      recordRevision: 1,
      archivedAtUtc: null,
      createdAtUtc: '2026-08-28T00:00:00.000Z',
      updatedAtUtc: '2026-08-28T00:00:00.000Z',
      file: null,
      integrityStatus: 'not_attached',
      wheelModelIds: [],
      specimenIds: [],
      warnings: ['case_document_file_missing'],
    } as const;
    expect(caseDocumentSchema.parse(result).integrityStatus).toBe('not_attached');
    expect(
      caseDocumentSchema.safeParse({ ...result, absolutePath: 'C:\\managed.pdf' }).success,
    ).toBe(false);
    expect(
      caseDocumentSchema.safeParse({
        ...result,
        file: {
          originalFileName: 'C:\\secret.pdf',
          mediaType: 'application/pdf',
          sizeBytes: 10,
          sha256: 'a'.repeat(64),
          attachedAtUtc: '2026-08-28T00:00:00.000Z',
        },
        integrityStatus: 'verified',
        warnings: [],
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

  it('normalizes analyst dossier values and keeps incomplete fields optional', () => {
    expect(
      wheelModelDraftSchema.parse({
        fullName: ' Модель ',
        designation: '',
        nominalDiameterMm: '0500,5000',
        nominalSpeedRpm: null,
        bladeCount: null,
        geometryDescription: '',
        compositionDescription: '',
        materialDescription: '',
        notes: '',
      }).nominalDiameterMm,
    ).toBe('500.5');
    expect(
      specimenDraftSchema.parse({
        wheelModelId: '113ec2c8-9439-4ce8-823d-3e2b0de8f001',
        identificationNumber: ' SN-1 ',
        batchNumber: '',
        marking: '',
        manufacturedOn: '',
        receivedOn: null,
        workingDiameterMm: '',
        initialConditionNotes: '',
        notes: '',
      }),
    ).toMatchObject({
      identificationNumber: 'SN-1',
      manufacturedOn: null,
      workingDiameterMm: null,
    });
    expect(
      customerUpsertPayloadSchema.parse({
        expectedRevision: null,
        customer: { fullName: ' Заказчик ', legalAddress: '', actualAddress: '', notes: '' },
      }).customer.fullName,
    ).toBe('Заказчик');
  });

  it('rejects invalid dossier dates and entity-specific response shapes', () => {
    expect(
      specimenDraftSchema.safeParse({
        wheelModelId: '113ec2c8-9439-4ce8-823d-3e2b0de8f001',
        identificationNumber: 'SN-1',
        batchNumber: '',
        marking: '',
        manufacturedOn: '2026-02-30',
        receivedOn: null,
        workingDiameterMm: null,
        initialConditionNotes: '',
        notes: '',
      }).success,
    ).toBe(false);
    expect(() =>
      parseWorkerResponse('wheelModel.create', {
        protocolVersion: 1,
        requestId: 'wheel-1',
        revision: 1,
        kind: 'response',
        ok: true,
        result: { specimenId: 'wrong-result' },
        evidence: {},
        warnings: [],
      }),
    ).toThrow();
  });

  it('uses the same exact dossier scalar contract as the Python boundary', () => {
    expect(
      wheelModelDraftSchema.safeParse({
        fullName: 'Модель',
        designation: '',
        nominalDiameterMm: '1e100',
        nominalSpeedRpm: null,
        bladeCount: null,
        geometryDescription: '',
        compositionDescription: '',
        materialDescription: '',
        notes: '',
      }).success,
    ).toBe(false);
    expect(
      specimenDraftSchema.safeParse({
        wheelModelId: '113ec2c8-9439-7ce8-823d-3e2b0de8f001',
        identificationNumber: 'SN-1',
        batchNumber: '',
        marking: '',
        manufacturedOn: '0000-01-01',
        receivedOn: null,
        workingDiameterMm: null,
        initialConditionNotes: '',
        notes: '',
      }).success,
    ).toBe(false);
  });
});
