import type {
  CaseDocument,
  CaseDocumentCreateCommand,
  CaseDocumentSummary,
  DesktopResult,
  CustomerProfile,
  ImpellerApi,
  ImportedRunDetail,
  ImportedRunSummary,
  ReliabilityExecution,
  ProjectOverview,
  RecentProject,
  RunPackageValidationJob,
  RunPackageImportJob,
  SpecimenBinding,
  RuntimeStatus,
  Specimen,
  SpecimenDraft,
  SpecimenSummary,
  WheelModel,
  WheelModelDraft,
  WheelModelSummary,
} from '@impeller-reliability/contracts';
import {
  importedRunDetailSchema,
  reliabilityExecutionSchema,
  planIdSchema,
  runPackageImportJobSchema,
  runPackageValidationJobSchema,
  specimenSourceIdSchema,
} from '@impeller-reliability/contracts';

export type PreviewMode = 'ready' | 'unavailable';

const previewStatuses: Readonly<Record<PreviewMode, RuntimeStatus>> = {
  ready: {
    applicationVersion: '0.1.0',
    electronVersion: '43.4.1',
    workerStatus: 'ready',
    workerVersion: '0.1.0',
    protocolVersion: 1,
    sqliteStatus: 'ok',
    mode: 'development',
    message: 'Browser preview (DEV, без сохранения): синтетический локальный контур готов.',
  },
  unavailable: {
    applicationVersion: '0.1.0',
    electronVersion: '43.4.1',
    workerStatus: 'unavailable',
    workerVersion: null,
    protocolVersion: 1,
    sqliteStatus: 'error',
    mode: 'development',
    message: 'Browser preview (DEV, без сохранения): смоделирован недоступный Python worker.',
  },
};

export function createPreviewApi(mode: PreviewMode): ImpellerApi {
  let status = previewStatuses[mode];
  let activeProject: ProjectOverview | null = null;
  let customer: CustomerProfile | null = null;
  const wheels = new Map<string, WheelModel>();
  const specimens = new Map<string, Specimen>();
  const documents = new Map<string, CaseDocument>();
  let validationJob: RunPackageValidationJob | null = null;
  let validationPolls = 0;
  let importJob: RunPackageImportJob | null = null;
  let importPolls = 0;
  let importedRun = previewImportedRunDetail();
  let reliabilityExecutions: readonly ReliabilityExecution[] = [];
  const recentProject: RecentProject = {
    path: 'C:\\Проекты\\Надёжность рабочего колеса.irproj',
    name: 'Надёжность рабочего колеса',
    projectNumber: 'ИР-2026-001',
    lastOpenedAtUtc: '2026-08-25T15:00:00.000Z',
  };
  const listeners = new Set<(nextStatus: RuntimeStatus) => void>();
  return {
    system: {
      getStatus: () => Promise.resolve(status),
      ping: () =>
        status.workerStatus === 'ready'
          ? Promise.resolve(status)
          : Promise.reject(new Error('preview_worker_unavailable')),
      restart: () => {
        status = previewStatuses.ready;
        validationJob = null;
        validationPolls = 0;
        importJob = null;
        importPolls = 0;
        for (const listener of listeners) listener(status);
        return Promise.resolve(status);
      },
      openLog: () => Promise.resolve(),
      confirmClose: () => Promise.resolve(),
      cancelClose: () => Promise.resolve(),
      subscribeStatus: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      subscribeCloseRequested: () => () => undefined,
    },
    project: {
      create: (draft) => {
        activeProject = projectOverview(draft, 'C:\\Проекты\\Новый проект.irproj');
        return Promise.resolve(success(activeProject));
      },
      open: () => {
        activeProject = projectOverview(
          {
            name: recentProject.name,
            projectNumber: recentProject.projectNumber,
            description: 'Синтетический проект Browser preview без записи на диск.',
            status: 'active',
          },
          recentProject.path,
        );
        return Promise.resolve(success(activeProject));
      },
      openRecent: () => {
        activeProject = projectOverview(
          {
            name: recentProject.name,
            projectNumber: recentProject.projectNumber,
            description: 'Синтетический проект Browser preview без записи на диск.',
            status: 'active',
          },
          recentProject.path,
        );
        return Promise.resolve(success(activeProject));
      },
      close: () => {
        activeProject = null;
        return Promise.resolve(success({ closed: true }));
      },
      releaseLocalWorkspace: () => Promise.resolve(),
      getOverview: () =>
        Promise.resolve(activeProject === null ? noProject() : success(activeProject)),
      updateMetadata: ({ expectedRevision, metadata }) => {
        if (activeProject === null) return Promise.resolve(noProject());
        if (activeProject.recordRevision !== expectedRevision) {
          return Promise.resolve({
            ok: false,
            error: {
              code: 'revision_conflict',
              message: 'Синтетический конфликт редакции.',
              details: {},
              retryable: false,
            },
          });
        }
        activeProject = {
          ...activeProject,
          ...metadata,
          recordRevision: expectedRevision + 1,
          updatedAtUtc: new Date().toISOString(),
        };
        return Promise.resolve(success(activeProject));
      },
      createBackup: () =>
        Promise.resolve(
          success({
            fileName: 'project-v1-preview.sqlite',
            sha256: '0'.repeat(64),
            createdAtUtc: new Date().toISOString(),
          }),
        ),
      listRecent: () => Promise.resolve(success([recentProject])),
    },
    caseCustomer: {
      get: () => Promise.resolve(success(customer)),
      upsert: ({ expectedRevision, customer: draft }) => {
        const revision = customer === null ? 1 : customer.recordRevision + 1;
        if (customer !== null && expectedRevision !== customer.recordRevision)
          return Promise.resolve(conflict());
        customer = {
          projectId: activeProject?.projectId ?? '019d2ca4-b4e6-7e18-8f5e-36ce99ab87da',
          ...draft,
          recordRevision: revision,
          createdAtUtc: customer?.createdAtUtc ?? '2026-08-25T15:00:00.000Z',
          updatedAtUtc: '2026-08-26T12:00:00.000Z',
          warnings:
            draft.legalAddress === '' || draft.actualAddress === ''
              ? ['customer_address_missing']
              : [],
        };
        return Promise.resolve(success(customer));
      },
    },
    wheelModel: {
      create: (command) => {
        const existing = wheels.get(command.wheelModelId);
        if (existing !== undefined) return Promise.resolve(success(existing));
        const wheel = previewWheel(command.wheelModelId, command);
        wheels.set(wheel.wheelModelId, wheel);
        return Promise.resolve(success(wheel));
      },
      list: (includeArchived) =>
        Promise.resolve(
          success(
            [...wheels.values()]
              .filter((item) => includeArchived || item.archivedAtUtc === null)
              .map<WheelModelSummary>((item) => ({
                wheelModelId: item.wheelModelId,
                fullName: item.fullName,
                designation: item.designation,
                recordRevision: item.recordRevision,
                archivedAtUtc: item.archivedAtUtc,
                warnings: item.warnings,
              })),
          ),
        ),
      get: (wheelModelId) => Promise.resolve(entityResult(wheels.get(wheelModelId))),
      update: ({ wheelModelId, expectedRevision, wheelModel: draft }) => {
        const current = wheels.get(wheelModelId);
        if (current === undefined) return Promise.resolve(notFound());
        if (current.recordRevision !== expectedRevision) return Promise.resolve(conflict());
        const updated = previewWheel(
          wheelModelId,
          draft,
          expectedRevision + 1,
          current.archivedAtUtc,
        );
        wheels.set(wheelModelId, updated);
        return Promise.resolve(success(updated));
      },
      archive: (command) =>
        Promise.resolve(
          setPreviewWheelArchived(wheels, command.wheelModelId, command.expectedRevision, true),
        ),
      restore: (command) =>
        Promise.resolve(
          setPreviewWheelArchived(wheels, command.wheelModelId, command.expectedRevision, false),
        ),
    },
    specimen: {
      create: (command) => {
        const existing = specimens.get(command.specimenId);
        if (existing !== undefined) return Promise.resolve(success(existing));
        const wheel = wheels.get(command.wheelModelId);
        if (wheel === undefined) return Promise.resolve(notFound());
        const specimen = previewSpecimen(command.specimenId, command, wheel.fullName);
        specimens.set(specimen.specimenId, specimen);
        return Promise.resolve(success(specimen));
      },
      list: (includeArchived) =>
        Promise.resolve(
          success(
            [...specimens.values()]
              .filter((item) => includeArchived || item.archivedAtUtc === null)
              .map<SpecimenSummary>((item) => ({
                specimenId: item.specimenId,
                wheelModelId: item.wheelModelId,
                wheelModelName: item.wheelModelName,
                identificationNumber: item.identificationNumber,
                recordRevision: item.recordRevision,
                archivedAtUtc: item.archivedAtUtc,
                warnings: item.warnings,
              })),
          ),
        ),
      get: (specimenId) => Promise.resolve(entityResult(specimens.get(specimenId))),
      update: ({ specimenId, expectedRevision, specimen: draft }) => {
        const current = specimens.get(specimenId);
        const wheel = wheels.get(draft.wheelModelId);
        if (current === undefined || wheel === undefined) return Promise.resolve(notFound());
        if (current.recordRevision !== expectedRevision) return Promise.resolve(conflict());
        const updated = previewSpecimen(
          specimenId,
          draft,
          wheel.fullName,
          expectedRevision + 1,
          current.archivedAtUtc,
        );
        specimens.set(specimenId, updated);
        return Promise.resolve(success(updated));
      },
      archive: (command) =>
        Promise.resolve(
          setPreviewSpecimenArchived(specimens, command.specimenId, command.expectedRevision, true),
        ),
      restore: (command) =>
        Promise.resolve(
          setPreviewSpecimenArchived(
            specimens,
            command.specimenId,
            command.expectedRevision,
            false,
          ),
        ),
    },
    caseDocument: {
      create: (command) => {
        const current = documents.get(command.caseDocumentId);
        if (current !== undefined) return Promise.resolve(success(current));
        const created = previewCaseDocument(command, false);
        documents.set(command.caseDocumentId, created);
        return Promise.resolve(success(created));
      },
      createWithFile: (command) => {
        const current = documents.get(command.caseDocumentId);
        if (current !== undefined) return Promise.resolve(success(current));
        const created = previewCaseDocument(command, true);
        documents.set(command.caseDocumentId, created);
        return Promise.resolve(success(created));
      },
      list: ({ includeArchived, documentKind }) =>
        Promise.resolve(
          success(
            [...documents.values()]
              .filter(
                (item) =>
                  (includeArchived || item.archivedAtUtc === null) &&
                  (documentKind === null || item.documentKind === documentKind),
              )
              .sort((left, right) => {
                const archiveOrder =
                  Number(left.archivedAtUtc !== null) - Number(right.archivedAtUtc !== null);
                return archiveOrder !== 0
                  ? archiveOrder
                  : left.title.localeCompare(right.title, 'ru');
              })
              .map((item): CaseDocumentSummary => ({
                caseDocumentId: item.caseDocumentId,
                documentKind: item.documentKind,
                title: item.title,
                designation: item.designation,
                recordRevision: item.recordRevision,
                archivedAtUtc: item.archivedAtUtc,
                warnings: item.warnings,
              })),
          ),
        ),
      get: (caseDocumentId) => Promise.resolve(entityResult(documents.get(caseDocumentId))),
      update: ({ caseDocumentId, expectedRevision, document, wheelModelIds, specimenIds }) => {
        const current = documents.get(caseDocumentId);
        if (current === undefined) return Promise.resolve(notFound());
        if (current.recordRevision !== expectedRevision) return Promise.resolve(conflict());
        const updated: CaseDocument = {
          ...current,
          ...document,
          wheelModelIds,
          specimenIds,
          recordRevision: expectedRevision + 1,
          updatedAtUtc: '2026-08-28T12:00:00.000Z',
          warnings: documentWarnings(document, current.integrityStatus),
        };
        documents.set(caseDocumentId, updated);
        return Promise.resolve(success(updated));
      },
      attachFile: ({ caseDocumentId, expectedRevision }) => {
        const current = documents.get(caseDocumentId);
        if (current === undefined) return Promise.resolve(notFound());
        if (current.recordRevision !== expectedRevision) return Promise.resolve(conflict());
        if (current.file !== null) {
          return Promise.resolve({
            ok: false,
            error: {
              code: 'file_already_attached',
              message: 'К документу уже прикреплён файл.',
              details: {},
              retryable: false,
            },
          });
        }
        const updated: CaseDocument = {
          ...current,
          file: previewFile(),
          integrityStatus: 'verified',
          recordRevision: expectedRevision + 1,
          updatedAtUtc: '2026-08-28T12:00:00.000Z',
          warnings: documentWarnings(current, 'verified'),
        };
        documents.set(caseDocumentId, updated);
        return Promise.resolve(success(updated));
      },
      verifyFile: (caseDocumentId) => Promise.resolve(entityResult(documents.get(caseDocumentId))),
      openFile: (caseDocumentId) => {
        const current = documents.get(caseDocumentId);
        if (current === undefined) return Promise.resolve(notFound());
        if (current.integrityStatus !== 'verified') {
          return Promise.resolve({
            ok: false,
            error: {
              code: 'file_missing',
              message: 'Управляемый файл отсутствует.',
              details: {},
              retryable: false,
            },
          });
        }
        return Promise.resolve(success({ opened: true }));
      },
      archive: (command) =>
        Promise.resolve(
          setPreviewDocumentArchived(
            documents,
            command.caseDocumentId,
            command.expectedRevision,
            true,
          ),
        ),
      restore: (command) =>
        Promise.resolve(
          setPreviewDocumentArchived(
            documents,
            command.caseDocumentId,
            command.expectedRevision,
            false,
          ),
        ),
    },
    runPackageValidation: {
      selectAndStart: ({ jobId, replaceJobId }) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (validationJob !== null) {
          if (validationJob.jobId === jobId) return Promise.resolve(success(validationJob));
          const terminal = ['completed', 'failed', 'cancelled'].includes(validationJob.state);
          if (!terminal || replaceJobId !== validationJob.jobId)
            return Promise.resolve(operationInProgress());
        }
        validationPolls = 0;
        validationJob = previewValidationActive(jobId);
        return Promise.resolve(success(validationJob));
      },
      get: (jobId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (validationJob === null || validationJob.jobId !== jobId)
          return Promise.resolve(notFound());
        validationPolls += 1;
        if (validationPolls >= 2 && validationJob.state !== 'cancelled') {
          validationJob = previewValidationCompleted(jobId);
        }
        return Promise.resolve(success(validationJob));
      },
      cancel: (jobId) => {
        if (validationJob === null || validationJob.jobId !== jobId)
          return Promise.resolve(notFound());
        if (!['completed', 'failed', 'cancelled'].includes(validationJob.state))
          validationJob = previewValidationCancelled(jobId);
        return Promise.resolve(success(validationJob));
      },
      discard: (jobId) => {
        if (validationJob === null || validationJob.jobId !== jobId)
          return Promise.resolve(notFound());
        if (!['completed', 'failed', 'cancelled'].includes(validationJob.state))
          return Promise.resolve(operationInProgress());
        validationJob = null;
        return Promise.resolve(success({ jobId, discarded: true }));
      },
    },
    runPackageImport: {
      selectAndStart: ({ jobId, replaceJobId }) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        if (importJob !== null) {
          const terminal = ['completed', 'failed', 'cancelled'].includes(importJob.state);
          if (!terminal || replaceJobId !== importJob.jobId)
            return Promise.resolve(operationInProgress());
        }
        importPolls = 0;
        importJob = previewImportActive(jobId);
        return Promise.resolve(success(importJob));
      },
      get: (jobId) => {
        if (importJob === null || importJob.jobId !== jobId) return Promise.resolve(notFound());
        importPolls += 1;
        if (importPolls >= 2 && importJob.state !== 'cancelled') {
          importJob = previewImportCompleted(jobId, importedRun.summary);
        }
        return Promise.resolve(success(importJob));
      },
      cancel: (jobId) => {
        if (importJob === null || importJob.jobId !== jobId) return Promise.resolve(notFound());
        if (!['completed', 'failed', 'cancelled'].includes(importJob.state))
          importJob = previewImportCancelled(jobId);
        return Promise.resolve(success(importJob));
      },
      discard: (jobId) => {
        if (importJob === null || importJob.jobId !== jobId) return Promise.resolve(notFound());
        if (!['completed', 'failed', 'cancelled'].includes(importJob.state))
          return Promise.resolve(operationInProgress());
        importJob = null;
        return Promise.resolve(success({ jobId, discarded: true }));
      },
    },
    importedRun: {
      list: () => Promise.resolve(success([importedRun.summary])),
      get: (localImportId) =>
        Promise.resolve(
          localImportId === importedRun.summary.localImportId ? success(importedRun) : notFound(),
        ),
      verifySource: (localImportId) =>
        Promise.resolve(
          localImportId === importedRun.summary.localImportId
            ? success({ localImportId, sourceIntegrity: 'verified' as const })
            : notFound(),
        ),
      getResolutionState: (sourceSpecimenId) =>
        Promise.resolve(
          sourceSpecimenId === importedRun.summary.sourceSpecimenId
            ? success(previewBinding(importedRun.summary))
            : notFound(),
        ),
      bindSpecimen: (command) => {
        if (command.sourceSpecimenId !== importedRun.summary.sourceSpecimenId)
          return Promise.resolve(notFound());
        if (command.localSpecimenId === importedRun.summary.localSpecimenId)
          return Promise.resolve(success(previewBinding(importedRun.summary)));
        importedRun = importedRunDetailSchema.parse({
          ...importedRun,
          summary: {
            ...importedRun.summary,
            localSpecimenId: command.localSpecimenId,
            bindingRevision: command.expectedRevision + 1,
          },
        });
        return Promise.resolve(success(previewBinding(importedRun.summary)));
      },
      applyEnrichmentResolution: (command) => {
        if (command.localImportId !== importedRun.summary.localImportId)
          return Promise.resolve(notFound());
        importedRun = importedRunDetailSchema.parse({
          ...importedRun,
          enrichmentResolutions: [
            ...importedRun.enrichmentResolutions,
            {
              resolutionId: command.resolutionId,
              sourcePayloadPath: command.sourcePayloadPath,
              sourceField: command.sourceField,
              targetEntityType: command.targetEntityType,
              targetEntityId: command.targetEntityId,
              targetField: command.targetField,
              decision: command.decision,
              actor: command.actor,
              occurredAtUtc: '2026-08-31T12:00:00.000Z',
              reason: command.reason,
            },
          ],
        });
        return Promise.resolve(success(importedRun));
      },
    },
    reliabilityExecution: {
      materialize: (localImportId) => {
        if (localImportId !== importedRun.summary.localImportId) return Promise.resolve(notFound());
        if (importedRun.summary.localSpecimenId === null)
          return Promise.resolve({
            ok: false,
            error: {
              code: 'validation_error',
              message: 'Сначала свяжите исходный образец с локальным Specimen.',
              details: {},
              retryable: false,
            },
          });
        const existing = reliabilityExecutions.find((item) => item.localImportId === localImportId);
        if (existing !== undefined) return Promise.resolve(success(existing));
        const execution = reliabilityExecutionSchema.parse({
          executionId: '4c7462d8-2222-4d19-8b8c-222222222222',
          localImportId,
          localSpecimenId: importedRun.summary.localSpecimenId,
          sourceSpecimenId: importedRun.summary.sourceSpecimenId,
          method: importedRun.summary.mode,
          lifecycleStatus: 'completed',
          plannedParametersSnapshot: {},
          resultSummary: {},
          sourceOuterPackageSha256: importedRun.summary.outerPackageSha256,
          materializedAtUtc: '2026-09-01T12:00:00.000Z',
          failureObservations: [],
        });
        reliabilityExecutions = [execution];
        return Promise.resolve(success(execution));
      },
      listByWheel: (wheelModelId) =>
        Promise.resolve(
          success(
            reliabilityExecutions.filter(
              (execution) =>
                specimens.get(execution.localSpecimenId)?.wheelModelId === wheelModelId,
            ),
          ),
        ),
    },
  };
}

function previewValidationActive(jobId: string): RunPackageValidationJob {
  return runPackageValidationJobSchema.parse({
    jobId,
    state: 'running',
    phase: 'payload_integrity',
    progress: {
      kind: 'known',
      completedBytes: 4_096,
      totalBytes: 12_288,
      completedEntries: 4,
      totalEntries: 15,
    },
    startedAtUtc: '2026-08-29T12:00:00.000Z',
    finishedAtUtc: null,
    report: null,
    typedError: null,
  });
}

function previewValidationCompleted(jobId: string): RunPackageValidationJob {
  return runPackageValidationJobSchema.parse({
    jobId,
    state: 'completed',
    phase: 'finalizing',
    progress: {
      kind: 'known',
      completedBytes: 12_288,
      totalBytes: 12_288,
      completedEntries: 15,
      totalEntries: 15,
    },
    startedAtUtc: '2026-08-29T12:00:00.000Z',
    finishedAtUtc: '2026-08-29T12:00:01.000Z',
    typedError: null,
    report: {
      validatorVersion: 'm03b.1',
      validationLevel: 'producer_m9a_contract',
      upstreamRepository: 'https://github.com/vitalcc55/R130SH',
      upstreamCommit: '01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63',
      contractSchema: 'r130sh.run-package.v1',
      sourceFileName: 'synthetic-preview.r130run',
      outerPackageSha256: 'a'.repeat(64),
      outerSizeBytes: 12_288,
      packageId: '019d3c80-3d21-7a65-8e5a-111111111111',
      exportRevision: 1,
      runId: 'normal_final_rbd',
      packageKind: 'final',
      producer: {
        name: 'R130SH',
        version: 'synthetic-m03a',
        buildId: 'downstream_synthetic_contract_fixture',
        gitCommit: 'm9a-commit',
      },
      entryCount: 15,
      declaredPayloadBytes: 8_192,
      validatedPayloadBytes: 8_192,
      structuralVerdict: 'passed',
      semanticVerdict: 'passed',
      semanticCoverage: [
        { area: 'manifest', status: 'covered', contractSource: 'manifest-example' },
        {
          area: 'measurements_csv',
          status: 'covered',
          contractSource: 'r130sh-m9a-contract',
        },
      ],
      findingCounts: { error: 0, warning: 0, info: 0, total: 0, truncated: false },
      findings: [],
      startedAtUtc: '2026-08-29T12:00:00.000Z',
      finishedAtUtc: '2026-08-29T12:00:01.000Z',
    },
  });
}

function previewValidationCancelled(jobId: string): RunPackageValidationJob {
  return runPackageValidationJobSchema.parse({
    jobId,
    state: 'cancelled',
    phase: 'payload_integrity',
    progress: {
      kind: 'known',
      completedBytes: 4_096,
      totalBytes: 12_288,
      completedEntries: 4,
      totalEntries: 15,
    },
    startedAtUtc: '2026-08-29T12:00:00.000Z',
    finishedAtUtc: '2026-08-29T12:00:00.500Z',
    report: null,
    typedError: { code: 'cancelled', message: 'Проверка отменена.', retryable: false },
  });
}

function previewImportActive(jobId: string): RunPackageImportJob {
  return runPackageImportJobSchema.parse({
    jobId,
    state: 'copying',
    phase: 'streaming_copy',
    completedBytes: 4_096,
    totalBytes: 9_111,
    completedEntries: 0,
    totalEntries: 0,
    startedAtUtc: '2026-08-31T12:00:00.000Z',
    finishedAtUtc: null,
    result: null,
    typedError: null,
  });
}

function previewImportCompleted(
  jobId: string,
  importedRun: ImportedRunSummary,
): RunPackageImportJob {
  return runPackageImportJobSchema.parse({
    jobId,
    state: 'completed',
    phase: 'terminal',
    completedBytes: 9_111,
    totalBytes: 9_111,
    completedEntries: 14,
    totalEntries: 14,
    startedAtUtc: '2026-08-31T12:00:00.000Z',
    finishedAtUtc: '2026-08-31T12:00:01.000Z',
    result: { disposition: 'existing', importedRun },
    typedError: null,
  });
}

function previewImportCancelled(jobId: string): RunPackageImportJob {
  return runPackageImportJobSchema.parse({
    jobId,
    state: 'cancelled',
    phase: 'terminal',
    completedBytes: 4_096,
    totalBytes: 9_111,
    completedEntries: 0,
    totalEntries: 0,
    startedAtUtc: '2026-08-31T12:00:00.000Z',
    finishedAtUtc: '2026-08-31T12:00:00.500Z',
    result: null,
    typedError: {
      code: 'cancelled',
      message: 'Импорт отменён до фиксации в проекте.',
      retryable: true,
    },
  });
}

function previewImportedRunDetail(): ImportedRunDetail {
  return importedRunDetailSchema.parse({
    summary: {
      localImportId: '60cdaf47-78e8-48b5-abcb-a465b42d3191',
      packageId: '1932f123-462a-4712-a86d-4d1ff8b651bf',
      exportRevision: 1,
      outerPackageSha256: 'c73d028a0aa5f0b7aacce2f216005048973c4895705b847b4c762b1d0e433c43',
      runId: 'normal_final_rbd',
      packageKind: 'final',
      packageSchema: 'r130sh.run-package.v1',
      packageCreatedAtUtc: '2026-08-31T10:00:00.000Z',
      sourceSnapshotSha256: '821172a68c6a9ab1e2abe79e6172f6ca0fbdea54ce5e5c15e1727e8b29218a34',
      producerName: 'R130SH',
      producerVersion: 'm9a-test',
      producerBuildId: 'm9a-build',
      producerGitCommit: 'm9a-commit',
      outerSizeBytes: 9_111,
      importedAtUtc: '2026-08-31T12:00:00.000Z',
      validatorVersion: 'm03b.1',
      validationContractCommit: '01d30f36c3ea7484ef2e519ed4d4bd6f2d56bb63',
      structuralVerdict: 'passed',
      semanticVerdict: 'passed',
      sourceIntegrity: 'verified',
      sourceSpecimenId: 'specimen-m9a-001',
      localSpecimenId: null,
      bindingRevision: 1,
      mode: 'rbd',
      technicalStatus: 'completed',
      terminationReason: 'normal_done',
      specimenOutcome: 'passed',
      runValidity: 'valid',
      dataCompleteness: 'complete',
      importedExisting: false,
    },
    projection: {
      startedAtUtc: '2026-08-31T10:00:00.000Z',
      finishedAtUtc: '2026-08-31T10:00:00.000Z',
      resumeAvailable: false,
      partialReasons: [],
      customerFullName: 'Лабораторный заказчик',
      customerAddress: 'г. Москва',
      customerOrderReference: 'M9A-ORDER-001',
      wheelFullName: 'Рабочее колесо Р130Ш',
      wheelIdentifier: 'WHEEL-M9A-001',
      workingDiameterMm: '1300.0',
      sampleLabel: 'WHEEL-M9A-001',
      originalPlan: previewPlan(),
      effectivePlan: previewPlan(),
      environment: {
        status: 'inside',
        temperatureC: '22',
        humidityPct: '45',
        pressureKpa: '101.3',
        source: 'operator_entered',
        deviationCount: 0,
        confirmationActor: null,
        confirmationReason: null,
      },
      provenance: {
        producerName: 'R130SH',
        appVersion: 'm9a-test',
        buildId: 'm9a-build',
        gitCommit: 'm9a-commit',
        databaseSchemaVersion: 1,
        standName: 'Стенд Р130Ш',
        standSerialNumber: 'R130SH-M9A',
        timeSource: 'utc_wall_and_monotonic_run_clock',
      },
      measurementCount: 1,
      acceptedMeasurementCount: 1,
      eventCount: 5,
      inspectionCount: 2,
      attachmentCount: 0,
      amendmentCount: 0,
      creditingPolicy: 'rbd.v1',
      acceptedElapsedS: '4',
    },
    inventory: [
      {
        path: 'measurements.csv',
        mediaType: 'text/csv',
        sizeBytes: 2_460,
        sha256: 'a'.repeat(64),
        rowCount: 1,
        semanticCoverage: 'covered',
      },
      {
        path: 'run-summary.json',
        mediaType: 'application/json',
        sizeBytes: 1_338,
        sha256: 'b'.repeat(64),
        rowCount: null,
        semanticCoverage: 'covered',
      },
    ],
    semanticCoverage: [
      { area: 'manifest', status: 'covered', contractSource: 'r130sh-m9a-contract' },
      { area: 'measurements_csv', status: 'covered', contractSource: 'r130sh-m9a-contract' },
    ],
    validationFindings: [],
    enrichmentResolutions: [],
  });
}

function previewPlan(): ImportedRunDetail['projection']['originalPlan'] {
  return {
    planId: planIdSchema.parse('plan-normal_final_rbd'),
    planRevision: 1,
    mode: 'rbd',
    specimenId: specimenSourceIdSchema.parse('specimen-m9a-001'),
    wheelIdentifier: 'WHEEL-M9A-001',
    laboratoryCaseReference: 'M9A-LAB-001',
    customerOrderReference: 'M9A-ORDER-001',
    nominalRpm: '3000',
    targetCycles: 100,
    targetMaxRpm: null,
    lowerRpm: null,
    upperRpm: null,
    targetSteadyDurationS: '4',
    totalDurationS: '8',
    lowerPointPolicy: null,
    roundingPolicy: 'ceiling',
    requiredCyclesExact: '100',
    requiredSteadyDurationSExact: '4',
    requiredTotalDurationSExact: null,
    cycleDurationSExact: null,
    targetMaxRpmExact: null,
  };
}

function previewBinding(summary: ImportedRunSummary): SpecimenBinding {
  return {
    sourceSpecimenId: summary.sourceSpecimenId,
    localSpecimenId: summary.localSpecimenId,
    recordRevision: summary.bindingRevision,
    updatedByActor: summary.localSpecimenId === null ? null : 'local_user',
    reason: summary.localSpecimenId === null ? '' : 'Подтверждено в Browser preview',
    createdAtUtc: '2026-08-31T12:00:00.000Z',
    updatedAtUtc: '2026-08-31T12:00:00.000Z',
  };
}

function projectOverview(
  draft: {
    readonly name: string;
    readonly projectNumber: string;
    readonly description: string;
    readonly status: ProjectOverview['status'];
  },
  path: string,
): ProjectOverview {
  return {
    projectId: '019d2ca4-b4e6-7e18-8f5e-36ce99ab87da',
    path,
    ...draft,
    recordRevision: 1,
    createdAtUtc: '2026-08-25T15:00:00.000Z',
    updatedAtUtc: '2026-08-25T15:00:00.000Z',
    createdWithApplicationVersion: '0.1.0',
    schemaVersion: 1,
  };
}

function previewWheel(
  wheelModelId: string,
  draft: WheelModelDraft,
  recordRevision = 1,
  archivedAtUtc: string | null = null,
): WheelModel {
  return {
    wheelModelId,
    ...draft,
    recordRevision,
    archivedAtUtc,
    createdAtUtc: '2026-08-25T15:00:00.000Z',
    updatedAtUtc: '2026-08-26T12:00:00.000Z',
    warnings: [
      ...(draft.nominalDiameterMm === null ? (['wheel_nominal_diameter_missing'] as const) : []),
      ...(draft.nominalSpeedRpm === null ? (['wheel_nominal_speed_missing'] as const) : []),
    ],
  };
}

function previewSpecimen(
  specimenId: string,
  draft: SpecimenDraft,
  wheelModelName: string,
  recordRevision = 1,
  archivedAtUtc: string | null = null,
): Specimen {
  return {
    specimenId,
    ...draft,
    wheelModelName,
    recordRevision,
    archivedAtUtc,
    createdAtUtc: '2026-08-25T15:00:00.000Z',
    updatedAtUtc: '2026-08-26T12:00:00.000Z',
    warnings: draft.workingDiameterMm === null ? ['specimen_working_diameter_missing'] : [],
  };
}

function previewCaseDocument(command: CaseDocumentCreateCommand, withFile: boolean): CaseDocument {
  const integrityStatus = withFile ? 'verified' : 'not_attached';
  return {
    caseDocumentId: command.caseDocumentId,
    ...command.document,
    recordRevision: 1,
    archivedAtUtc: null,
    createdAtUtc: '2026-08-28T12:00:00.000Z',
    updatedAtUtc: '2026-08-28T12:00:00.000Z',
    file: withFile ? previewFile() : null,
    integrityStatus,
    wheelModelIds: command.wheelModelIds,
    specimenIds: command.specimenIds,
    warnings: documentWarnings(command.document, integrityStatus),
  };
}

function previewFile(): NonNullable<CaseDocument['file']> {
  return {
    originalFileName: 'ГОСТ-синтетический.pdf',
    mediaType: 'application/pdf',
    sizeBytes: 2_048,
    sha256: 'a'.repeat(64),
    attachedAtUtc: '2026-08-28T12:00:00.000Z',
  };
}

function documentWarnings(
  document: Pick<CaseDocument, 'documentKind' | 'designation' | 'revisionLabel'>,
  integrityStatus: CaseDocument['integrityStatus'],
): CaseDocument['warnings'] {
  const normative = [
    'technical_specification',
    'individual_test_method',
    'typical_test_method',
    'standard',
  ].includes(document.documentKind);
  return [
    ...(integrityStatus === 'not_attached' || integrityStatus === 'missing'
      ? (['case_document_file_missing'] as const)
      : []),
    ...(normative && document.designation === ''
      ? (['case_document_designation_missing'] as const)
      : []),
    ...(normative && document.revisionLabel === ''
      ? (['case_document_revision_missing'] as const)
      : []),
  ];
}

function entityResult<TResult>(value: TResult | undefined): DesktopResult<TResult> {
  return value === undefined ? notFound() : success(value);
}

function notFound<TResult>(): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'entity_not_found',
      message: 'Запись не найдена.',
      details: {},
      retryable: false,
    },
  };
}

function workerUnavailable<TResult>(): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'worker_unavailable',
      message: 'Синтетический worker недоступен.',
      details: {},
      retryable: true,
    },
  };
}

function operationInProgress<TResult>(): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'operation_in_progress',
      message: 'Синтетическая проверка ещё выполняется.',
      details: {},
      retryable: true,
    },
  };
}

function conflict<TResult>(): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'revision_conflict',
      message: 'Синтетический конфликт редакции.',
      details: {},
      retryable: false,
    },
  };
}

function setPreviewWheelArchived(
  items: Map<string, WheelModel>,
  id: string,
  expectedRevision: number,
  archived: boolean,
): DesktopResult<WheelModel> {
  const current = items.get(id);
  if (current === undefined) return notFound();
  if (current.recordRevision !== expectedRevision) return conflict();
  const updated = {
    ...current,
    recordRevision: expectedRevision + 1,
    archivedAtUtc: archived ? '2026-08-26T12:00:00.000Z' : null,
    updatedAtUtc: '2026-08-26T12:00:00.000Z',
  };
  items.set(id, updated);
  return success(updated);
}

function setPreviewSpecimenArchived(
  items: Map<string, Specimen>,
  id: string,
  expectedRevision: number,
  archived: boolean,
): DesktopResult<Specimen> {
  const current = items.get(id);
  if (current === undefined) return notFound();
  if (current.recordRevision !== expectedRevision) return conflict();
  const updated = {
    ...current,
    recordRevision: expectedRevision + 1,
    archivedAtUtc: archived ? '2026-08-26T12:00:00.000Z' : null,
    updatedAtUtc: '2026-08-26T12:00:00.000Z',
  };
  items.set(id, updated);
  return success(updated);
}

function setPreviewDocumentArchived(
  items: Map<string, CaseDocument>,
  id: string,
  expectedRevision: number,
  archived: boolean,
): DesktopResult<CaseDocument> {
  const current = items.get(id);
  if (current === undefined) return notFound();
  if (current.recordRevision !== expectedRevision) return conflict();
  const updated: CaseDocument = {
    ...current,
    recordRevision: expectedRevision + 1,
    archivedAtUtc: archived ? '2026-08-28T12:00:00.000Z' : null,
    updatedAtUtc: '2026-08-28T12:00:00.000Z',
  };
  items.set(id, updated);
  return success(updated);
}

function success<TResult>(result: TResult): DesktopResult<TResult> {
  return { ok: true, result };
}

function noProject<TResult>(): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'storage_error',
      message: 'Проект не открыт.',
      details: {},
      retryable: false,
    },
  };
}
