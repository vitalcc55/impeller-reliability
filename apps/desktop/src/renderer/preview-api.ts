import type {
  CaseDocument,
  CaseDocumentCreateCommand,
  CaseDocumentSummary,
  DesktopResult,
  CustomerProfile,
  ImpellerApi,
  ProjectOverview,
  RecentProject,
  RunPackageValidationJob,
  RuntimeStatus,
  Specimen,
  SpecimenDraft,
  SpecimenSummary,
  WheelModel,
  WheelModelDraft,
  WheelModelSummary,
} from '@impeller-reliability/contracts';
import { runPackageValidationJobSchema } from '@impeller-reliability/contracts';

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
      validatorVersion: 'm03a.1',
      validationLevel: 'synthetic_contract_foundation',
      upstreamRepository: 'https://github.com/vitalcc55/R130SH',
      upstreamCommit: 'f02f6d954246a5ab6f57d33dac724ce03d7fb841',
      contractSchema: 'r130sh.run-package.v1',
      sourceFileName: 'synthetic-preview.r130run',
      outerPackageSha256: 'a'.repeat(64),
      outerSizeBytes: 12_288,
      packageId: '019d3c80-3d21-7a65-8e5a-111111111111',
      exportRevision: 1,
      runId: '019d3c80-3d21-7a65-8e5a-222222222222',
      packageKind: 'final',
      producer: {
        name: 'R130SH',
        version: 'synthetic-m03a',
        buildId: 'downstream_synthetic_contract_fixture',
        gitCommit: 'f02f6d954246a5ab6f57d33dac724ce03d7fb841',
      },
      entryCount: 15,
      declaredPayloadBytes: 8_192,
      validatedPayloadBytes: 8_192,
      structuralVerdict: 'passed',
      semanticVerdict: 'partial',
      semanticCoverage: [
        { area: 'manifest', status: 'covered', contractSource: 'manifest-example' },
        {
          area: 'measurements_csv',
          status: 'not_available',
          contractSource: 'upstream-contract-gap',
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
