import type {
  CaseDocument,
  CaseDocumentCreateCommand,
  CaseDocumentSummary,
  DesktopResult,
  MaterialOrigin,
  MaterialPageQuery,
  CustomerProfile,
  ImpellerApi,
  ImportedRunDetail,
  ImportedRunSummary,
  PmnCalculationCreateCommand,
  PmnCalculationDetail,
  PmnPlanSource,
  RbdCalculationCreateCommand,
  RbdCalculationDetail,
  RbdPlanSource,
  RptCalculationCreateCommand,
  RptCalculationDetail,
  RptPlanSource,
  ReliabilityDatasetVersion,
  ReliabilityExecution,
  ReliabilityObservationVersion,
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
  materialPagePayloadSchema,
  materialReadPayloadSchema,
  materialInspectionPayloadSchema,
  materialOpenPayloadSchema,
  importedRunDetailSchema,
  pmnCalculationDetailSchema,
  pmnPlanSourceSchema,
  rbdCalculationDetailSchema,
  rbdPlanSourceSchema,
  rptCalculationDetailSchema,
  rptPlanSourceSchema,
  reliabilityDatasetVersionSchema,
  reliabilityExecutionSchema,
  reliabilityObservationVersionSchema,
  planIdSchema,
  runPackageImportJobSchema,
  runPackageValidationJobSchema,
  specimenSourceIdSchema,
} from '@impeller-reliability/contracts';
import { previewMaterials } from './preview-materials';
import { sameMaterialOrigin } from './features/projects/material-origin';
import {
  materialCopyReleaseDecisionSchema,
  type MaterialCopyReleaseRequest,
  type MaterialOpenedResult,
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

export function createPreviewApi(
  mode: PreviewMode,
  sample: 'rbd' | 'rpt' | 'pmn' = 'rbd',
  materialCopyCapacity = false,
): ImpellerApi {
  const copyReleaseListeners = new Set<(request: MaterialCopyReleaseRequest) => void>();
  let materialCopyPending: {
    readonly request: MaterialCopyReleaseRequest;
    readonly finish: (result: DesktopResult<MaterialOpenedResult>) => void;
  } | null = null;
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
  let importedRun = previewImportedRunDetail(sample);
  let reliabilityExecutions: readonly ReliabilityExecution[] = [];
  let reliabilityObservations: readonly ReliabilityObservationVersion[] = [];
  let reliabilityDatasets: readonly ReliabilityDatasetVersion[] = [];
  let rbdCalculations: readonly RbdCalculationDetail[] = [];
  let rptCalculations: readonly RptCalculationDetail[] = [];
  let pmnCalculations: readonly PmnCalculationDetail[] = [];
  const recentProject: RecentProject = {
    path: 'C:\\Проекты\\Надёжность рабочего колеса.irproj',
    name: 'Надёжность рабочего колеса',
    projectNumber: 'ИР-2026-001',
    lastOpenedAtUtc: '2026-08-25T15:00:00.000Z',
  };
  const listeners = new Set<(nextStatus: RuntimeStatus) => void>();
  const diagnosticImportId = '1f782baf-13e5-4cbe-89f6-f8dcd6c33355';
  let diagnosticResolutions: ImportedRunDetail['enrichmentResolutions'] = [];
  function diagnosticImport(): ImportedRunDetail {
    return importedRunDetailSchema.parse({
      ...importedRun,
      enrichmentResolutions: diagnosticResolutions,
      summary: {
        ...importedRun.summary,
        localImportId: diagnosticImportId,
        packageId: 'a745d23b-7314-4295-b59e-7599070b03fe',
        runId: 'synthetic-diagnostic-materials',
        exportRevision: 2,
        outerPackageSha256: 'd'.repeat(64),
        packageKind: 'diagnostic_partial',
        technicalStatus: 'interrupted',
        terminationReason: 'manual_abort',
        runValidity: 'invalid',
        dataCompleteness: 'partial',
        specimenOutcome: 'inconclusive',
      },
      projection: {
        ...importedRun.projection,
        attachmentCount: 5,
        resumeAvailable: true,
        partialReasons: ['Недоступная фотография в синтетическом примере'],
      },
    });
  }
  function materialOrigin(selected: MaterialOrigin): DesktopResult<MaterialOrigin> {
    if (status.workerStatus !== 'ready') return workerUnavailable();
    if (activeProject === null)
      return {
        ok: false,
        error: {
          code: 'cancelled',
          message: 'Материалы не относятся к открытой сессии дела.',
          details: {},
          retryable: false,
        },
      };
    const source = selected.localImportId === diagnosticImportId ? diagnosticImport() : importedRun;
    const expected = {
      projectId: activeProject.projectId,
      localImportId: source.summary.localImportId,
      packageId: source.summary.packageId,
      runId: source.summary.runId,
      exportRevision: source.summary.exportRevision,
      outerPackageSha256: source.summary.outerPackageSha256,
    };
    return sameMaterialOrigin(selected, expected)
      ? success(expected)
      : {
          ok: false,
          error: {
            code: 'file_integrity_mismatch',
            message: 'Редакция материала не соответствует выбранному синтетическому источнику.',
            details: {},
            retryable: false,
          },
        };
  }
  function materialPage<T>(
    items: readonly T[],
    query: MaterialPageQuery,
    kind: string,
  ): DesktopResult<{
    readonly items: T[];
    readonly nextCursor: string | null;
    readonly pageBound: 'item_limit' | null;
  }> {
    const prefix = `preview-${kind}-${query.origin.localImportId}-${query.origin.exportRevision}-${query.origin.outerPackageSha256}-`;
    const cursor = query.cursor;
    const suffix =
      cursor == null ? '0' : cursor.startsWith(prefix) ? cursor.slice(prefix.length) : '';
    const start = /^[0-9]+$/.test(suffix) ? Number(suffix) : -1;
    if (!Number.isSafeInteger(start) || start < 0 || start > items.length)
      return {
        ok: false,
        error: {
          code: 'validation_error',
          message: 'Страница не относится к выбранным материалам.',
          details: {},
          retryable: false,
        },
      };
    const end = Math.min(items.length, start + (query.limit ?? 25));
    return success({
      items: items.slice(start, end),
      nextCursor: end < items.length ? `${prefix}${end}` : null,
      pageBound: end < items.length ? 'item_limit' : null,
    });
  }
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
      list: () => Promise.resolve(success([importedRun.summary, diagnosticImport().summary])),
      get: (localImportId) =>
        Promise.resolve(
          localImportId === importedRun.summary.localImportId
            ? success(importedRun)
            : localImportId === diagnosticImportId
              ? success(diagnosticImport())
              : notFound(),
        ),
      listInspectionPage: (raw) => {
        const parsed = materialPagePayloadSchema.safeParse(raw);
        if (!parsed.success)
          return Promise.resolve(validationError('Запрос материалов не соответствует контракту.'));
        const checked = materialOrigin(parsed.data.origin);
        if (!checked.ok) return Promise.resolve(checked);
        const value = previewMaterials(
          checked.result,
          checked.result.localImportId === diagnosticImportId,
        ).inspections;
        const page = materialPage(value.items, parsed.data, 'inspection');
        return Promise.resolve(page.ok ? success({ ...value, ...page.result }) : page);
      },
      getInspection: (raw) => {
        const parsed = materialInspectionPayloadSchema.safeParse(raw);
        if (!parsed.success)
          return Promise.resolve(validationError('Запрос материалов не соответствует контракту.'));
        const checked = materialOrigin(parsed.data.origin);
        if (!checked.ok) return Promise.resolve(checked);
        const value = previewMaterials(
          checked.result,
          checked.result.localImportId === diagnosticImportId,
        ).inspections;
        const item = value.items.find(
          (candidate) => candidate.materialId === parsed.data.inspectionId,
        );
        return Promise.resolve(
          item === undefined
            ? notFound()
            : success({ origin: value.origin, verification: value.verification, item }),
        );
      },
      listPhotoPage: (raw) => {
        const parsed = materialPagePayloadSchema.safeParse(raw);
        if (!parsed.success)
          return Promise.resolve(validationError('Запрос материалов не соответствует контракту.'));
        const checked = materialOrigin(parsed.data.origin);
        if (!checked.ok) return Promise.resolve(checked);
        const value = previewMaterials(
          checked.result,
          checked.result.localImportId === diagnosticImportId,
        ).photos;
        const page = materialPage(value.items, parsed.data, 'photo');
        return Promise.resolve(page.ok ? success({ ...value, ...page.result }) : page);
      },
      getProtocol: (raw) => {
        const parsed = materialReadPayloadSchema.safeParse(raw);
        if (!parsed.success)
          return Promise.resolve(validationError('Запрос материалов не соответствует контракту.'));
        const checked = materialOrigin(parsed.data.origin);
        if (!checked.ok) return Promise.resolve(checked);
        return Promise.resolve(
          success(
            previewMaterials(checked.result, checked.result.localImportId === diagnosticImportId)
              .protocol,
          ),
        );
      },
      openMaterial: (raw) => {
        const parsed = materialOpenPayloadSchema.safeParse(raw);
        if (!parsed.success)
          return Promise.resolve(validationError('Недопустимый запрос открытия материала.'));
        const checked = materialOrigin(parsed.data.identity.origin);
        if (!checked.ok) return Promise.resolve(checked);
        const value = previewMaterials(
          checked.result,
          checked.result.localImportId === diagnosticImportId,
        );
        const item =
          parsed.data.identity.kind === 'protocol'
            ? value.protocol.item
            : value.photos.items.find(
                (photo) => photo.materialId === parsed.data.identity.materialId,
              );
        if (
          item === undefined ||
          item.materialId !== parsed.data.identity.materialId ||
          item.state !== 'verified'
        )
          return Promise.resolve(notFound());
        if (materialCopyCapacity) {
          if (materialCopyPending !== null)
            return Promise.resolve(validationError('Открытие уже выполняется.'));
          return new Promise<DesktopResult<MaterialOpenedResult>>((finish) => {
            const request = {
              requestId: crypto.randomUUID(),
              operationId: parsed.data.operationId,
              identity: parsed.data.identity,
            };
            materialCopyPending = { request, finish };
            for (const listener of copyReleaseListeners) listener(request);
          });
        }
        return Promise.resolve({
          ok: false,
          error: {
            code: 'material_open_failed',
            message: 'Системное открытие доступно в настольном приложении.',
            details: {},
            retryable: false,
          },
        });
      },
      subscribeCopyReleaseRequested: (listener) => {
        copyReleaseListeners.add(listener);
        return () => {
          copyReleaseListeners.delete(listener);
        };
      },
      respondCopyRelease: (raw) => {
        const parsed = materialCopyReleaseDecisionSchema.safeParse(raw);
        if (!parsed.success) return Promise.resolve(validationError('Недопустимое подтверждение.'));
        const pending = materialCopyPending;
        const command = parsed.data;
        if (
          pending === null ||
          pending.request.requestId !== command.requestId ||
          pending.request.operationId !== command.operationId ||
          pending.request.identity.kind !== command.identity.kind ||
          pending.request.identity.materialId !== command.identity.materialId ||
          !sameMaterialOrigin(pending.request.identity.origin, command.identity.origin)
        )
          return Promise.resolve(success({ accepted: false }));
        materialCopyPending = null;
        pending.finish({
          ok: false,
          error: {
            code: command.decision === 'keep' ? 'file_too_large' : 'material_open_failed',
            message:
              command.decision === 'keep'
                ? 'Синтетический preview: копии сохранены, предел не освобождён.'
                : 'Синтетическое подтверждение принято. Файлы не удалялись; системное открытие доступно в настольном приложении.',
            details: {},
            retryable: false,
          },
        });
        return Promise.resolve(success({ accepted: true }));
      },
      cancelMaterialOpen: (operationId) => {
        const pending = materialCopyPending;
        const cancelled = pending !== null && pending.request.operationId === operationId;
        if (cancelled) {
          materialCopyPending = null;
          pending.finish({
            ok: false,
            error: {
              code: 'cancelled',
              message: 'Синтетическое открытие отменено.',
              details: {},
              retryable: false,
            },
          });
        }
        return Promise.resolve({ ok: true, result: { cancelled } });
      },
      verifySource: (localImportId) =>
        Promise.resolve(
          localImportId === importedRun.summary.localImportId ||
            localImportId === diagnosticImportId
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
        if (
          command.localImportId !== importedRun.summary.localImportId &&
          command.localImportId !== diagnosticImportId
        )
          return Promise.resolve(notFound());
        const selected =
          command.localImportId === diagnosticImportId ? diagnosticImport() : importedRun;
        const updated = importedRunDetailSchema.parse({
          ...selected,
          enrichmentResolutions: [
            ...selected.enrichmentResolutions,
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
        if (command.localImportId === diagnosticImportId)
          diagnosticResolutions = updated.enrichmentResolutions;
        else importedRun = updated;
        return Promise.resolve(success(updated));
      },
    },
    reliabilityExecution: {
      materialize: (localImportId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (
          localImportId !== importedRun.summary.localImportId &&
          localImportId !== diagnosticImportId
        )
          return Promise.resolve(notFound());
        const selected = localImportId === diagnosticImportId ? diagnosticImport() : importedRun;
        if (selected.summary.localSpecimenId === null)
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
          executionId:
            localImportId === diagnosticImportId
              ? '4c7462d8-2222-4d19-8b8c-333333333333'
              : '4c7462d8-2222-4d19-8b8c-222222222222',
          localImportId,
          localSpecimenId: selected.summary.localSpecimenId,
          wheelModelId:
            specimens.get(selected.summary.localSpecimenId)?.wheelModelId ??
            '00000000-0000-4000-8000-000000000001',
          sourceSpecimenId: selected.summary.sourceSpecimenId,
          sourceRunId: selected.summary.runId,
          exportRevision: selected.summary.exportRevision,
          packageKind: selected.summary.packageKind,
          method: selected.summary.mode,
          lifecycleStatus:
            selected.summary.technicalStatus === 'completed' ? 'completed' : 'interrupted',
          plannedParametersSnapshot: {},
          resultSummary: {},
          sourceOuterPackageSha256: selected.summary.outerPackageSha256,
          materializedAtUtc: '2026-09-01T12:00:00.000Z',
          failureObservations: [],
        });
        reliabilityExecutions = [...reliabilityExecutions, execution];
        return Promise.resolve(success(execution));
      },
      listPage: (wheelModelId, cursor, limit) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const pageLimit = limit ?? 25;
        if (!Number.isInteger(pageLimit) || pageLimit < 1 || pageLimit > 50)
          return Promise.resolve(validationError('Размер страницы должен быть от 1 до 50.'));
        const items = reliabilityExecutions
          .filter((execution) => execution.wheelModelId === wheelModelId)
          .map((execution) => {
            const source =
              execution.localImportId === diagnosticImportId
                ? diagnosticImport().summary
                : importedRun.summary;
            const current = reliabilityObservations
              .filter((item) => item.executionId === execution.executionId)
              .sort((left, right) => right.versionNumber - left.versionNumber)[0];
            return {
              executionId: execution.executionId,
              localSpecimenId: execution.localSpecimenId,
              sourceSpecimenId: execution.sourceSpecimenId,
              sourceRunId: execution.sourceRunId,
              exportRevision: execution.exportRevision,
              packageKind: execution.packageKind,
              method: execution.method,
              lifecycleStatus: execution.lifecycleStatus,
              technicalStatus: source.technicalStatus,
              specimenOutcome: source.specimenOutcome,
              runValidity: source.runValidity,
              dataCompleteness: source.dataCompleteness,
              materializedAtUtc: execution.materializedAtUtc,
              failureObservationCount: execution.failureObservations.length,
              currentObservationVersionId: current?.observationVersionId ?? null,
              currentObservationVersionNumber: current?.versionNumber ?? null,
              currentClassification: current?.classification ?? null,
            };
          });
        const offset = cursor === null || cursor === undefined ? 0 : Number.parseInt(cursor, 10);
        if (!Number.isInteger(offset) || offset < 0 || String(offset) !== (cursor ?? '0'))
          return Promise.resolve(validationError('Cursor списка повреждён.'));
        const page = items.slice(offset, offset + pageLimit);
        return Promise.resolve(
          success({
            items: page,
            nextCursor: offset + pageLimit < items.length ? String(offset + pageLimit) : null,
          }),
        );
      },
      getDetail: (executionId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const execution = reliabilityExecutions.find((item) => item.executionId === executionId);
        return Promise.resolve(execution === undefined ? notFound() : success(execution));
      },
    },
    reliabilityObservation: {
      listVersions: (executionId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        return Promise.resolve(
          success(
            reliabilityObservations
              .filter((item) => item.executionId === executionId)
              .sort((left, right) => right.versionNumber - left.versionNumber)
              .slice(0, 50),
          ),
        );
      },
      getVersion: (observationVersionId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const version = reliabilityObservations.find(
          (item) => item.observationVersionId === observationVersionId,
        );
        return Promise.resolve(version === undefined ? notFound() : success(version));
      },
      createVersion: (command) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const allowed =
          command.classification === 'failure'
            ? ['exact', 'interval', 'unavailable']
            : command.classification === 'right_censored'
              ? ['right_bound']
              : command.classification === 'withdrawn'
                ? ['right_bound', 'unavailable']
                : ['unavailable'];
        if (!allowed.includes(command.endpointKind))
          return Promise.resolve(validationError('Классификация и граница несовместимы.'));
        if (
          (command.metricKind === null) !== (command.metricUnit === null) ||
          (command.metricKind === null) !== (command.metricOrigin === null) ||
          (command.metricKind === null) !== (command.lowerValue === null)
        )
          return Promise.resolve(
            validationError('Для числовой наработки требуется происхождение.'),
          );
        if (
          (command.endpointKind === 'unavailable' &&
            (command.metricKind !== null || command.upperValue !== null)) ||
          (command.endpointKind === 'interval' &&
            (command.lowerValue === null || command.upperValue === null)) ||
          (!['interval', 'unavailable'].includes(command.endpointKind) &&
            (command.lowerValue === null || command.upperValue !== null))
        )
          return Promise.resolve(
            validationError('Числовые границы не соответствуют форме endpoint.'),
          );
        const execution = reliabilityExecutions.find(
          (item) => item.executionId === command.executionId,
        );
        if (execution === undefined) return Promise.resolve(notFound());
        const expectedMetric =
          execution.method === 'rbd'
            ? ['rbd_steady_rotation_time', 'hours']
            : execution.method === 'rpt'
              ? ['rpt_start_stop_cycles', 'count']
              : null;
        if (
          command.metricKind !== null &&
          (expectedMetric === null ||
            command.metricKind !== expectedMetric[0] ||
            command.metricUnit !== expectedMetric[1])
        )
          return Promise.resolve(validationError('Наработка не соответствует методу исполнения.'));
        const existing = reliabilityObservations.find(
          (item) => item.observationVersionId === command.observationVersionId,
        );
        if (existing !== undefined)
          return Promise.resolve(success({ disposition: 'existing' as const, version: existing }));
        const document = documents.get(command.documentId);
        if (document === undefined) return Promise.resolve(notFound());
        if (document.archivedAtUtc !== null)
          return Promise.resolve(
            validationError('Архивный документ нельзя выбрать для нового решения.'),
          );
        const hasApplicability =
          document.wheelModelIds.length > 0 || document.specimenIds.length > 0;
        if (
          hasApplicability &&
          !document.wheelModelIds.includes(execution.wheelModelId) &&
          !document.specimenIds.includes(execution.localSpecimenId)
        )
          return Promise.resolve(validationError('Документ не относится к выбранному исполнению.'));
        const versions = reliabilityObservations.filter(
          (item) => item.observationId === command.observationId,
        );
        const head = versions.sort((left, right) => right.versionNumber - left.versionNumber)[0];
        if ((head?.observationVersionId ?? null) !== command.expectedPreviousVersionId)
          return Promise.resolve(revisionConflict(head?.observationVersionId ?? null));
        const version = reliabilityObservationVersionSchema.parse({
          observationId: command.observationId,
          observationVersionId: command.observationVersionId,
          executionId: command.executionId,
          versionNumber: versions.length + 1,
          previousVersionId: command.expectedPreviousVersionId,
          classification: command.classification,
          endpointKind: command.endpointKind,
          metricKind: command.metricKind,
          metricUnit: command.metricUnit,
          metricOrigin: command.metricOrigin,
          lowerValue: command.lowerValue,
          upperValue: command.upperValue,
          originBasis: command.originBasis,
          endpointBasis: command.endpointBasis,
          documentSnapshot: {
            documentId: document.caseDocumentId,
            documentKind: document.documentKind,
            title: document.title,
            designation: document.designation,
            revisionLabel: document.revisionLabel,
            recordRevision: document.recordRevision,
            managedFileSha256: document.file?.sha256 ?? null,
          },
          documentLocator: command.documentLocator,
          failureIds: command.failureIds,
          actor: command.actor,
          decisionReason: command.reason,
          createdAtUtc: '2026-09-09T12:00:00.000Z',
          contentSha256: 'b'.repeat(64),
        });
        reliabilityObservations = [...reliabilityObservations, version];
        return Promise.resolve(success({ disposition: 'created' as const, version }));
      },
    },
    reliabilityDataset: {
      listPage: (wheelModelId, cursor, limit) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const pageLimit = limit ?? 25;
        if (!Number.isInteger(pageLimit) || pageLimit < 1 || pageLimit > 50)
          return Promise.resolve(validationError('Размер страницы должен быть от 1 до 50.'));
        const latest = new Map<string, ReliabilityDatasetVersion>();
        for (const item of reliabilityDatasets.filter(
          (entry) => entry.wheelModelId === wheelModelId,
        )) {
          const current = latest.get(item.datasetId);
          if (current === undefined || item.versionNumber > current.versionNumber)
            latest.set(item.datasetId, item);
        }
        const items = [...latest.values()].map((item) => ({
          datasetId: item.datasetId,
          wheelModelId: item.wheelModelId,
          latestVersionId: item.datasetVersionId,
          latestVersionNumber: item.versionNumber,
          title: item.title,
          method: item.method,
          metricKind: item.metricKind,
          metricUnit: item.metricUnit,
          includedCount: item.members.filter((member) => member.decision === 'included').length,
          excludedCount: item.members.filter((member) => member.decision === 'excluded').length,
          createdAtUtc: item.createdAtUtc,
        }));
        const offset = cursor === null || cursor === undefined ? 0 : Number.parseInt(cursor, 10);
        if (!Number.isInteger(offset) || offset < 0 || String(offset) !== (cursor ?? '0'))
          return Promise.resolve(validationError('Cursor списка повреждён.'));
        return Promise.resolve(
          success({
            items: items.slice(offset, offset + pageLimit),
            nextCursor: offset + pageLimit < items.length ? String(offset + pageLimit) : null,
          }),
        );
      },
      getVersion: (datasetVersionId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const version = reliabilityDatasets.find(
          (item) => item.datasetVersionId === datasetVersionId,
        );
        return Promise.resolve(version === undefined ? notFound() : success(version));
      },
      createVersion: (command) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        const expectedMetric =
          command.method === 'rbd'
            ? { kind: 'rbd_steady_rotation_time', unit: 'hours' }
            : { kind: 'rpt_start_stop_cycles', unit: 'count' };
        if (
          command.metricKind !== expectedMetric.kind ||
          command.metricUnit !== expectedMetric.unit
        )
          return Promise.resolve(
            validationError('Показатель и единица наработки не соответствуют методу выборки.'),
          );
        const existing = reliabilityDatasets.find(
          (item) => item.datasetVersionId === command.datasetVersionId,
        );
        if (existing !== undefined)
          return Promise.resolve(success({ disposition: 'existing' as const, version: existing }));
        const previous = reliabilityDatasets.filter((item) => item.datasetId === command.datasetId);
        const head = previous.sort((left, right) => right.versionNumber - left.versionNumber)[0];
        if ((head?.datasetVersionId ?? null) !== command.expectedPreviousVersionId)
          return Promise.resolve(revisionConflict(head?.datasetVersionId ?? null));
        const members = command.decisions.flatMap((decision) => {
          const observation = reliabilityObservations.find(
            (item) => item.observationVersionId === decision.observationVersionId,
          );
          const execution = reliabilityExecutions.find(
            (item) => item.executionId === observation?.executionId,
          );
          if (observation === undefined || execution === undefined) return [];
          const eligible =
            importedRun.summary.packageKind === 'final' &&
            execution.method === command.method &&
            observation.metricKind === command.metricKind &&
            observation.metricUnit === command.metricUnit &&
            ((observation.classification === 'failure' && observation.endpointKind === 'exact') ||
              (observation.classification === 'right_censored' &&
                observation.endpointKind === 'right_bound'));
          return [
            {
              observationVersionId: observation.observationVersionId,
              executionId: execution.executionId,
              localSpecimenId: execution.localSpecimenId,
              sourceRunId: importedRun.summary.runId,
              policyEligibility: eligible ? ('eligible' as const) : ('ineligible' as const),
              policyReason: eligible
                ? 'Наблюдение соответствует политике life_metric_exact_v1.'
                : 'Наблюдение не имеет совместимой точной границы.',
              decision: decision.decision,
              inclusionReason: decision.reason,
            },
          ];
        });
        if (members.length !== command.decisions.length) return Promise.resolve(notFound());
        if (
          members.some(
            (item) => item.decision === 'included' && item.policyEligibility === 'ineligible',
          )
        )
          return Promise.resolve(
            validationError('Непригодное наблюдение нельзя включить в эту выборку.'),
          );
        const version = reliabilityDatasetVersionSchema.parse({
          datasetId: command.datasetId,
          datasetVersionId: command.datasetVersionId,
          wheelModelId: command.wheelModelId,
          versionNumber: previous.length + 1,
          previousVersionId: command.expectedPreviousVersionId,
          policyId: 'life_metric_exact_v1',
          title: command.title,
          method: command.method,
          metricKind: command.metricKind,
          metricUnit: command.metricUnit,
          populationBasis: command.populationBasis,
          methodologyBasis: command.methodologyBasis,
          comparabilityBasis: command.comparabilityBasis,
          members,
          actor: command.actor,
          decisionReason: command.reason,
          createdAtUtc: '2026-09-09T12:05:00.000Z',
          contentSha256: 'c'.repeat(64),
        });
        reliabilityDatasets = [...reliabilityDatasets, version];
        return Promise.resolve(success({ disposition: 'created' as const, version }));
      },
    },
    rbdCalculation: {
      getSourceInputs: (executionId, selection) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const execution = reliabilityExecutions.find((item) => item.executionId === executionId);
        if (execution === undefined || execution.method !== 'rbd')
          return Promise.resolve(notFound());
        return Promise.resolve(success(previewRbdPlanSource(execution, importedRun, selection)));
      },
      create: (command) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const existing = rbdCalculations.find(
          (item) =>
            item.calculationSnapshot.calculationSnapshotId === command.calculationSnapshotId,
        );
        if (existing !== undefined)
          return Promise.resolve(success({ disposition: 'existing' as const, detail: existing }));
        const execution = reliabilityExecutions.find(
          (item) => item.executionId === command.executionId,
        );
        if (execution === undefined || execution.method !== 'rbd')
          return Promise.resolve(notFound());
        const requiredFields = new Set([
          'nominal_rpm',
          'base_cycles',
          'reserve_factor',
          'acceleration_duration_s',
          'deceleration_duration_s',
        ]);
        if (
          command.selections.length !== 5 ||
          command.selections.some(
            (item) => !requiredFields.delete(item.field) || item.origin !== 'source',
          ) ||
          command.failureEvidence !== null
        )
          return Promise.resolve(
            validationError(
              'Synthetic preview поддерживает только пять исходных значений без T_OTK.',
            ),
          );
        const source = previewRbdPlanSource(execution, importedRun, command.planSelection);
        const detail = previewRbdCalculationDetail(command, execution, source);
        rbdCalculations = [detail, ...rbdCalculations];
        return Promise.resolve(success({ disposition: 'created' as const, detail }));
      },
      listPage: (wheelModelId, cursor, limit) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const pageLimit = limit ?? 25;
        if (!Number.isInteger(pageLimit) || pageLimit < 1 || pageLimit > 50)
          return Promise.resolve(validationError('Размер страницы должен быть от 1 до 50.'));
        const offset = cursor === null || cursor === undefined ? 0 : Number.parseInt(cursor, 10);
        if (!Number.isInteger(offset) || offset < 0 || String(offset) !== (cursor ?? '0'))
          return Promise.resolve(validationError('Cursor списка повреждён.'));
        const matching = rbdCalculations.filter(
          (item) => item.inputSnapshot.wheelModelId === wheelModelId,
        );
        const items = matching.slice(offset, offset + pageLimit).map((item) => ({
          calculationSnapshotId: item.calculationSnapshot.calculationSnapshotId,
          analysisInputSnapshotId: item.inputSnapshot.analysisInputSnapshotId,
          executionId: item.inputSnapshot.executionId,
          wheelModelId: item.inputSnapshot.wheelModelId,
          planSelection: item.inputSnapshot.planSelection,
          requiredCycles:
            item.calculationSnapshot.resultSnapshot.required_cycles_exact.decimal ?? '1500.3',
          failureStatus: item.calculationSnapshot.resultSnapshot.failure_result.status,
          createdAtUtc: item.calculationSnapshot.createdAtUtc,
        }));
        return Promise.resolve(
          success({
            items,
            nextCursor: offset + pageLimit < matching.length ? String(offset + pageLimit) : null,
          }),
        );
      },
      getDetail: (calculationSnapshotId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const detail = rbdCalculations.find(
          (item) => item.calculationSnapshot.calculationSnapshotId === calculationSnapshotId,
        );
        return Promise.resolve(detail === undefined ? notFound() : success(detail));
      },
    },
    rptCalculation: {
      getSourceInputs: (executionId, selection) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const execution = reliabilityExecutions.find((item) => item.executionId === executionId);
        if (execution === undefined || execution.method !== 'rpt')
          return Promise.resolve(notFound());
        return Promise.resolve(success(previewRptPlanSource(execution, importedRun, selection)));
      },
      create: (command) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const existing = rptCalculations.find(
          (item) =>
            item.calculationSnapshot.calculationSnapshotId === command.calculationSnapshotId,
        );
        if (existing !== undefined)
          return Promise.resolve(success({ disposition: 'existing' as const, detail: existing }));
        const execution = reliabilityExecutions.find(
          (item) => item.executionId === command.executionId,
        );
        if (execution === undefined || execution.method !== 'rpt')
          return Promise.resolve(notFound());
        const requiredFields = new Set([
          'nominal_rpm',
          'design_cycles',
          'reserve_factor',
          'acceleration_duration_s',
          'steady_duration_s',
          'deceleration_duration_s',
        ]);
        if (
          command.selections.length !== 6 ||
          command.selections.some(
            (item) => !requiredFields.delete(item.field) || item.origin !== 'source',
          ) ||
          command.failureEvidence !== null
        )
          return Promise.resolve(
            validationError('Synthetic preview поддерживает шесть исходных значений без T_ОТК.'),
          );
        const source = previewRptPlanSource(execution, importedRun, command.planSelection);
        const detail = previewRptCalculationDetail(command, source);
        rptCalculations = [detail, ...rptCalculations];
        return Promise.resolve(success({ disposition: 'created' as const, detail }));
      },
      listPage: (wheelModelId, cursor, limit) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const pageLimit = limit ?? 25;
        if (!Number.isInteger(pageLimit) || pageLimit < 1 || pageLimit > 50)
          return Promise.resolve(validationError('Размер страницы должен быть от 1 до 50.'));
        const offset = cursor === null || cursor === undefined ? 0 : Number.parseInt(cursor, 10);
        if (!Number.isInteger(offset) || offset < 0 || String(offset) !== (cursor ?? '0'))
          return Promise.resolve(validationError('Cursor списка повреждён.'));
        const matching = rptCalculations.filter((item) =>
          reliabilityExecutions.some(
            (execution) =>
              execution.executionId === item.inputSnapshot.executionId &&
              execution.wheelModelId === wheelModelId,
          ),
        );
        return Promise.resolve(
          success({
            items: matching.slice(offset, offset + pageLimit).map((item) => ({
              calculationSnapshotId: item.calculationSnapshot.calculationSnapshotId,
              analysisInputSnapshotId: item.inputSnapshot.analysisInputSnapshotId,
              executionId: item.inputSnapshot.executionId,
              wheelModelId,
              requiredCycles:
                item.calculationSnapshot.resultSnapshot.required_cycles_exact.decimal ?? '3',
              failureStatus: item.calculationSnapshot.resultSnapshot.failure_result.status,
              createdAtUtc: item.calculationSnapshot.createdAtUtc,
            })),
            nextCursor: offset + pageLimit < matching.length ? String(offset + pageLimit) : null,
          }),
        );
      },
      getDetail: (calculationSnapshotId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const detail = rptCalculations.find(
          (item) => item.calculationSnapshot.calculationSnapshotId === calculationSnapshotId,
        );
        return Promise.resolve(detail === undefined ? notFound() : success(detail));
      },
    },
    pmnCalculation: {
      getSourceInputs: (executionId, selection) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const execution = reliabilityExecutions.find((item) => item.executionId === executionId);
        if (execution === undefined || execution.method !== 'pmn')
          return Promise.resolve(notFound());
        return Promise.resolve(success(previewPmnPlanSource(execution, importedRun, selection)));
      },
      create: (command) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const existing = pmnCalculations.find(
          (item) =>
            item.calculationSnapshot.calculationSnapshotId === command.calculationSnapshotId,
        );
        if (existing !== undefined)
          return Promise.resolve(success({ disposition: 'existing' as const, detail: existing }));
        const execution = reliabilityExecutions.find(
          (item) => item.executionId === command.executionId,
        );
        if (execution === undefined || execution.method !== 'pmn')
          return Promise.resolve(notFound());
        const requiredFields = new Set([
          'nominal_rpm',
          'speed_factor',
          'target_cycles',
          'acceleration_duration_s',
          'steady_duration_s',
          'deceleration_duration_s',
        ]);
        if (
          command.selections.length !== 6 ||
          command.selections.some(
            (item) => !requiredFields.delete(item.field) || item.origin !== 'source',
          ) ||
          command.failureEvidence !== null
        )
          return Promise.resolve(
            validationError('Synthetic preview поддерживает шесть исходных значений без T_ОТК.'),
          );
        const source = previewPmnPlanSource(execution, importedRun, command.planSelection);
        const detail = previewPmnCalculationDetail(command, source);
        pmnCalculations = [detail, ...pmnCalculations];
        return Promise.resolve(success({ disposition: 'created' as const, detail }));
      },
      listPage: (wheelModelId, cursor, limit) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const pageLimit = limit ?? 25;
        if (!Number.isInteger(pageLimit) || pageLimit < 1 || pageLimit > 50)
          return Promise.resolve(validationError('Размер страницы должен быть от 1 до 50.'));
        const offset = cursor === null || cursor === undefined ? 0 : Number.parseInt(cursor, 10);
        if (!Number.isInteger(offset) || offset < 0 || String(offset) !== (cursor ?? '0'))
          return Promise.resolve(validationError('Cursor списка повреждён.'));
        const matching = pmnCalculations.filter((item) =>
          reliabilityExecutions.some(
            (execution) =>
              execution.executionId === item.inputSnapshot.executionId &&
              execution.wheelModelId === wheelModelId,
          ),
        );
        return Promise.resolve(
          success({
            items: matching.slice(offset, offset + pageLimit).map((item) => ({
              calculationSnapshotId: item.calculationSnapshot.calculationSnapshotId,
              analysisInputSnapshotId: item.inputSnapshot.analysisInputSnapshotId,
              executionId: item.inputSnapshot.executionId,
              wheelModelId,
              targetCycles: '2',
              failureStatus: item.calculationSnapshot.resultSnapshot.failure_result.status,
              createdAtUtc: item.calculationSnapshot.createdAtUtc,
            })),
            nextCursor: offset + pageLimit < matching.length ? String(offset + pageLimit) : null,
          }),
        );
      },
      getDetail: (calculationSnapshotId) => {
        if (status.workerStatus !== 'ready') return Promise.resolve(workerUnavailable());
        if (activeProject === null) return Promise.resolve(noProject());
        const detail = pmnCalculations.find(
          (item) => item.calculationSnapshot.calculationSnapshotId === calculationSnapshotId,
        );
        return Promise.resolve(detail === undefined ? notFound() : success(detail));
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
      validatorVersion: 'm03b.3',
      validationLevel: 'producer_m9a_contract',
      upstreamRepository: 'https://github.com/vitalcc55/R130SH',
      upstreamCommit: 'b7792758b407ffc52d2fff051243056f63dbf18f',
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

function previewImportedRunDetail(sample: 'rbd' | 'rpt' | 'pmn' = 'rbd'): ImportedRunDetail {
  return importedRunDetailSchema.parse({
    summary: {
      localImportId: '60cdaf47-78e8-48b5-abcb-a465b42d3191',
      packageId: '1932f123-462a-4712-a86d-4d1ff8b651bf',
      exportRevision: 1,
      outerPackageSha256: 'c73d028a0aa5f0b7aacce2f216005048973c4895705b847b4c762b1d0e433c43',
      runId:
        sample === 'rpt'
          ? 'normal_final_rpt'
          : sample === 'pmn'
            ? 'normal_final_pmn'
            : 'normal_final_rbd',
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
      validatorVersion: 'm03b.2',
      validationContractCommit: '09097561a6a58b1663a6912357a3c8d1daf7f28c',
      structuralVerdict: 'passed',
      semanticVerdict: 'passed',
      sourceIntegrity: 'verified',
      sourceSpecimenId: 'specimen-m9a-001',
      localSpecimenId: null,
      bindingRevision: 1,
      mode: sample,
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
      originalPlan: previewPlan(sample),
      effectivePlan: previewPlan(sample),
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
      attachmentCount: 2,
      amendmentCount: 0,
      creditingPolicy: sample === 'rpt' ? 'rpt.v1' : sample === 'pmn' ? 'pmn.v1' : 'rbd.v1',
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

function previewPlan(
  sample: 'rbd' | 'rpt' | 'pmn' = 'rbd',
): ImportedRunDetail['projection']['originalPlan'] {
  return {
    planId: planIdSchema.parse(
      sample === 'rpt'
        ? 'plan-normal_final_rpt'
        : sample === 'pmn'
          ? 'plan-normal_final_pmn'
          : 'plan-normal_final_rbd',
    ),
    planRevision: 1,
    mode: sample,
    specimenId: specimenSourceIdSchema.parse('specimen-m9a-001'),
    wheelIdentifier: 'WHEEL-M9A-001',
    laboratoryCaseReference: 'M9A-LAB-001',
    customerOrderReference: 'M9A-ORDER-001',
    nominalRpm: '1500',
    targetCycles: sample === 'rpt' ? 3 : sample === 'pmn' ? 2 : 1501,
    targetMaxRpm: sample === 'pmn' ? '1650' : null,
    lowerRpm: sample === 'rpt' ? '15' : null,
    upperRpm: sample === 'rpt' ? '1500' : null,
    targetSteadyDurationS: sample === 'rpt' ? null : '60.04',
    totalDurationS: sample === 'rpt' ? '12' : sample === 'pmn' ? '10' : '70.04',
    lowerPointPolicy: sample === 'rpt' ? 'one_percent' : null,
    roundingPolicy: 'ceiling',
    requiredCyclesExact: sample === 'rpt' ? '3' : '1500.3',
    requiredSteadyDurationSExact: sample === 'rpt' ? null : '60.012',
    requiredTotalDurationSExact: sample === 'rpt' ? '12' : null,
    cycleDurationSExact: sample === 'rpt' ? '4' : sample === 'pmn' ? '5' : null,
    targetMaxRpmExact: sample === 'pmn' ? '1650' : null,
  };
}

function previewPmnPlanSource(
  execution: ReliabilityExecution,
  imported: ImportedRunDetail,
  selection: 'original' | 'effective',
): PmnPlanSource {
  const plan =
    selection === 'original' ? imported.projection.originalPlan : imported.projection.effectivePlan;
  return pmnPlanSourceSchema.parse({
    executionId: execution.executionId,
    localImportId: imported.summary.localImportId,
    packageId: imported.summary.packageId,
    runId: imported.summary.runId,
    exportRevision: imported.summary.exportRevision,
    outerPackageSha256: imported.summary.outerPackageSha256,
    sourceSnapshotSha256: imported.summary.sourceSnapshotSha256,
    producerName: imported.summary.producerName,
    producerVersion: imported.summary.producerVersion,
    producerBuildId: imported.summary.producerBuildId,
    producerGitCommit: imported.summary.producerGitCommit,
    planSelection: selection,
    payloadPath: selection === 'original' ? 'plan/original.json' : 'plan/effective.json',
    payloadSha256: 'a'.repeat(64),
    planId: plan.planId,
    planRevision: plan.planRevision,
    sourceValues: {
      nominalRpm: '1500',
      speedFactor: '1.1',
      targetCycles: '2',
      accelerationDurationS: '2',
      steadyDurationS: '1',
      decelerationDurationS: '2',
    },
    methodicalRequirements: {
      targetMaxRpmExact: '1650',
      cycleDurationSExact: '5',
      totalDurationSExact: '10',
    },
    executionTargets: {
      targetMaxRpm: '1650',
      targetCycles: '2',
      cycleDurationS: '5',
      totalDurationS: '10',
    },
  });
}

function previewPmnCalculationDetail(
  command: PmnCalculationCreateCommand,
  source: PmnPlanSource,
): PmnCalculationDetail {
  // This fixed fixture demonstrates the UI shape; it does not execute engineering formulas.
  const rational = (
    numerator: string,
    denominator: string,
    decimal: string | null,
    decimalPreview: string,
  ) => ({
    numerator,
    denominator,
    decimal,
    decimal_preview: decimalPreview,
  });
  const zero = rational('0', '1', '0', '0');
  const two = rational('2', '1', '2', '2');
  const three = rational('3', '1', '3', '3');
  const five = rational('5', '1', '5', '5');
  const ten = rational('10', '1', '10', '10');
  const rpm = rational('1650', '1', '1650', '1650');
  const fields = [
    'nominal_rpm',
    'speed_factor',
    'target_cycles',
    'acceleration_duration_s',
    'steady_duration_s',
    'deceleration_duration_s',
  ] as const;
  const sourceValues = {
    nominal_rpm: source.sourceValues.nominalRpm,
    speed_factor: source.sourceValues.speedFactor,
    target_cycles: source.sourceValues.targetCycles,
    acceleration_duration_s: source.sourceValues.accelerationDurationS,
    steady_duration_s: source.sourceValues.steadyDurationS,
    deceleration_duration_s: source.sourceValues.decelerationDurationS,
  };
  const fieldUnits = {
    nominal_rpm: 'rpm',
    speed_factor: '1',
    target_cycles: 'cycle',
    acceleration_duration_s: 's',
    steady_duration_s: 's',
    deceleration_duration_s: 's',
  };
  const createdAtUtc = '2026-09-29T12:00:00.000Z';
  const operationSha256 = 'b'.repeat(64);
  const inputContentSha256 = 'c'.repeat(64);
  return pmnCalculationDetailSchema.parse({
    inputSnapshot: {
      analysisInputSnapshotId: command.analysisInputSnapshotId,
      executionId: command.executionId,
      inputSnapshot: {
        schemaVersion: 1,
        operation: {
          schemaVersion: 1,
          ...command,
          selections: command.selections.map((item) => ({
            field: item.field,
            origin: item.origin,
            manual_value: null,
            basis: '',
            evidence: null,
          })),
          failureEvidence: null,
          algorithmId: 'pmn_reference',
          algorithmVersion: '1.0.0',
          numericPolicy: 'exact_fraction_v1',
        },
        source: {
          executionId: source.executionId,
          localImportId: source.localImportId,
          packageId: source.packageId,
          runId: source.runId,
          exportRevision: source.exportRevision,
          outerPackageSha256: source.outerPackageSha256,
          sourceSnapshotSha256: source.sourceSnapshotSha256,
          producer: {
            name: source.producerName,
            version: source.producerVersion,
            buildId: source.producerBuildId,
            gitCommit: source.producerGitCommit,
          },
          planSelection: source.planSelection,
          payloadPath: source.payloadPath,
          payloadSha256: source.payloadSha256,
          planId: source.planId,
          planRevision: source.planRevision,
          sourceValues,
          methodicalRequirements: {
            target_max_rpm_exact: source.methodicalRequirements.targetMaxRpmExact,
            cycle_duration_s_exact: source.methodicalRequirements.cycleDurationSExact,
            total_duration_s_exact: source.methodicalRequirements.totalDurationSExact,
          },
          executionTargets: {
            target_max_rpm: source.executionTargets.targetMaxRpm,
            target_cycles: source.executionTargets.targetCycles,
            cycle_duration_s: source.executionTargets.cycleDurationS,
            total_duration_s: source.executionTargets.totalDurationS,
          },
        },
        fieldSelections: fields.map((field) => ({
          field,
          unit: fieldUnits[field],
          origin: 'source',
          value: sourceValues[field],
          rawSourceValue: sourceValues[field],
          sourceReference: `${source.payloadPath}#/${source.planSelection === 'effective' ? 'effective_plan/effective_plan/' : ''}source_values/${field}`,
          basis: '',
          document: null,
        })),
        failureEvidence: null,
      },
      contentSha256: inputContentSha256,
      operationSha256,
      actor: command.actor,
      decisionReason: command.reason,
      createdAtUtc,
    },
    calculationSnapshot: {
      calculationSnapshotId: command.calculationSnapshotId,
      analysisInputSnapshotId: command.analysisInputSnapshotId,
      executionId: command.executionId,
      algorithmId: 'pmn_reference',
      algorithmVersion: '1.0.0',
      numericPolicy: 'exact_fraction_v1',
      resultSnapshot: {
        algorithm_id: 'pmn_reference',
        algorithm_version: '1.0.0',
        numeric_policy: 'exact_fraction_v1',
        maximum_rpm: rpm,
        cycle_duration_s_exact: five,
        total_duration_s_exact: ten,
        total_duration_min_exact: rational('1', '6', null, '0.166666666666…'),
        total_duration_h_exact: rational('1', '360', null, '0.002777777777…'),
        failure_result: {
          status: 'not_applicable',
          cycles_to_failure: null,
          reason_code: 'failure_duration_unavailable',
        },
        phases: [
          { phase: 'acceleration', start_s: zero, end_s: two, start_rpm: zero, end_rpm: rpm },
          { phase: 'steady_rotation', start_s: two, end_s: three, start_rpm: rpm, end_rpm: rpm },
          { phase: 'deceleration', start_s: three, end_s: five, start_rpm: rpm, end_rpm: zero },
        ],
        diagram_points: [
          { boundary: 'cycle_start', x: 0, y: 100 },
          { boundary: 'acceleration_end', x: 400, y: 0 },
          { boundary: 'steady_end', x: 600, y: 0 },
          { boundary: 'cycle_end', x: 1000, y: 100 },
          { boundary: 'repeat_acceleration_end', x: 1400, y: 0 },
          { boundary: 'repeat_steady_end', x: 1600, y: 0 },
          { boundary: 'repeat_cycle_end', x: 2000, y: 100 },
        ],
        formula_references: [
          'ПМИ Р130У, редакция 01, 2024, страница 15, формула 8',
          'ПМИ Р130У, редакция 01, 2024, страница 15, формула 9',
          'ПМИ Р130У, редакция 01, 2024, страница 16, формула 10',
          'ПМИ Р130У, редакция 01, 2024, страница 15, таблица 5',
        ],
      },
      inputContentSha256,
      operationSha256,
      contentSha256: 'd'.repeat(64),
      createdAtUtc,
    },
  });
}

function previewRptPlanSource(
  execution: ReliabilityExecution,
  imported: ImportedRunDetail,
  selection: 'original' | 'effective',
): RptPlanSource {
  const plan =
    selection === 'original' ? imported.projection.originalPlan : imported.projection.effectivePlan;
  return rptPlanSourceSchema.parse({
    executionId: execution.executionId,
    localImportId: imported.summary.localImportId,
    packageId: imported.summary.packageId,
    runId: imported.summary.runId,
    exportRevision: imported.summary.exportRevision,
    outerPackageSha256: imported.summary.outerPackageSha256,
    sourceSnapshotSha256: imported.summary.sourceSnapshotSha256,
    producerName: imported.summary.producerName,
    producerVersion: imported.summary.producerVersion,
    producerBuildId: imported.summary.producerBuildId,
    producerGitCommit: imported.summary.producerGitCommit,
    planSelection: selection,
    payloadPath: selection === 'original' ? 'plan/original.json' : 'plan/effective.json',
    payloadSha256: 'a'.repeat(64),
    planId: plan.planId,
    planRevision: plan.planRevision,
    sourceValues: {
      nominalRpm: '1500',
      designCycles: '2',
      reserveFactor: '1.5',
      accelerationDurationS: '2',
      steadyDurationS: '0',
      decelerationDurationS: '2',
      lowerPointPolicy: 'one_percent',
      explicitLowerRpm: null,
    },
    methodicalRequirements: {
      requiredCyclesExact: '3',
      cycleDurationSExact: '4',
      requiredTotalDurationSExact: '12',
    },
    executionTargets: {
      targetCycles: '3',
      upperRpm: '1500',
      lowerRpm: '15',
      cycleDurationS: '4',
      totalDurationS: '12',
      lowerPointPolicy: 'one_percent',
      roundingPolicy: 'ceiling',
    },
  });
}

function previewRptCalculationDetail(
  command: RptCalculationCreateCommand,
  source: RptPlanSource,
): RptCalculationDetail {
  // This fixture is deliberately fixed; Browser preview does not calculate engineering results.
  const rational = (numerator: string, denominator: string, decimal: string) => ({
    numerator,
    denominator,
    decimal,
    decimal_preview: decimal,
  });
  const zero = rational('0', '1', '0');
  const two = rational('2', '1', '2');
  const four = rational('4', '1', '4');
  const fifteen = rational('15', '1', '15');
  const rpm = rational('1500', '1', '1500');
  const fieldValues = {
    nominal_rpm: '1500',
    design_cycles: '2',
    reserve_factor: '1.5',
    acceleration_duration_s: '2',
    steady_duration_s: '0',
    deceleration_duration_s: '2',
  };
  const fieldUnits = {
    nominal_rpm: 'rpm',
    design_cycles: 'cycle',
    reserve_factor: '1',
    acceleration_duration_s: 's',
    steady_duration_s: 's',
    deceleration_duration_s: 's',
  };
  const fields = [
    'nominal_rpm',
    'design_cycles',
    'reserve_factor',
    'acceleration_duration_s',
    'steady_duration_s',
    'deceleration_duration_s',
  ] as const;
  const sourceValues = {
    nominal_rpm: source.sourceValues.nominalRpm,
    design_cycles: source.sourceValues.designCycles,
    reserve_factor: source.sourceValues.reserveFactor,
    acceleration_duration_s: source.sourceValues.accelerationDurationS,
    steady_duration_s: source.sourceValues.steadyDurationS,
    deceleration_duration_s: source.sourceValues.decelerationDurationS,
    lower_point_policy: source.sourceValues.lowerPointPolicy,
    explicit_lower_rpm: source.sourceValues.explicitLowerRpm,
  };
  const createdAtUtc = '2026-09-09T12:00:00.000Z';
  const operationSha256 = 'b'.repeat(64);
  const inputContentSha256 = 'c'.repeat(64);
  return rptCalculationDetailSchema.parse({
    inputSnapshot: {
      analysisInputSnapshotId: command.analysisInputSnapshotId,
      executionId: command.executionId,
      inputSnapshot: {
        schemaVersion: 1,
        operation: {
          schemaVersion: 1,
          ...command,
          selections: command.selections.map((item) => ({
            field: item.field,
            origin: item.origin,
            manual_value: null,
            basis: '',
            evidence: null,
          })),
          failureEvidence: null,
          algorithmId: 'rpt_reference',
          algorithmVersion: '1.0.0',
          numericPolicy: 'exact_fraction_v1',
        },
        source: {
          executionId: source.executionId,
          localImportId: source.localImportId,
          packageId: source.packageId,
          runId: source.runId,
          exportRevision: source.exportRevision,
          outerPackageSha256: source.outerPackageSha256,
          sourceSnapshotSha256: source.sourceSnapshotSha256,
          producer: {
            name: source.producerName,
            version: source.producerVersion,
            buildId: source.producerBuildId,
            gitCommit: source.producerGitCommit,
          },
          planSelection: source.planSelection,
          payloadPath: source.payloadPath,
          payloadSha256: source.payloadSha256,
          planId: source.planId,
          planRevision: source.planRevision,
          sourceValues,
          methodicalRequirements: {
            required_cycles_exact: source.methodicalRequirements.requiredCyclesExact,
            cycle_duration_s_exact: source.methodicalRequirements.cycleDurationSExact,
            required_total_duration_s_exact:
              source.methodicalRequirements.requiredTotalDurationSExact,
          },
          executionTargets: {
            target_cycles: source.executionTargets.targetCycles,
            upper_rpm: source.executionTargets.upperRpm,
            lower_rpm: source.executionTargets.lowerRpm,
            cycle_duration_s: source.executionTargets.cycleDurationS,
            total_duration_s: source.executionTargets.totalDurationS,
            lower_point_policy: source.executionTargets.lowerPointPolicy,
            rounding_policy: source.executionTargets.roundingPolicy,
          },
        },
        fieldSelections: fields.map((field) => ({
          field,
          unit: fieldUnits[field],
          origin: 'source',
          value: fieldValues[field],
          rawSourceValue: fieldValues[field],
          sourceReference: `${source.payloadPath}#/${source.planSelection === 'effective' ? 'effective_plan/effective_plan/' : ''}source_values/${field}`,
          basis: '',
          document: null,
        })),
        failureEvidence: null,
      },
      contentSha256: inputContentSha256,
      operationSha256,
      actor: command.actor,
      decisionReason: command.reason,
      createdAtUtc,
    },
    calculationSnapshot: {
      calculationSnapshotId: command.calculationSnapshotId,
      analysisInputSnapshotId: command.analysisInputSnapshotId,
      executionId: command.executionId,
      algorithmId: 'rpt_reference',
      algorithmVersion: '1.0.0',
      numericPolicy: 'exact_fraction_v1',
      resultSnapshot: {
        algorithm_id: 'rpt_reference',
        algorithm_version: '1.0.0',
        numeric_policy: 'exact_fraction_v1',
        maximum_rpm: rpm,
        minimum_rpm: fifteen,
        required_cycles_exact: rational('3', '1', '3'),
        cycle_duration_s_exact: four,
        total_duration_s_exact: rational('12', '1', '12'),
        total_duration_h_exact: {
          numerator: '1',
          denominator: '300',
          decimal: null,
          decimal_preview: '0.0033333333333333333333333333333333333333333333333333',
        },
        failure_result: {
          status: 'not_applicable',
          cycles_to_failure: null,
          reason_code: 'failure_duration_unavailable',
        },
        lower_point_comparison: {
          source_policy: 'one_percent',
          target_policy: 'one_percent',
          source_explicit_lower_rpm: null,
          target_lower_rpm: '15',
          status: 'matches_typical_formula',
        },
        phases: [
          { phase: 'acceleration', start_s: zero, end_s: two, start_rpm: fifteen, end_rpm: rpm },
          { phase: 'steady_rotation', start_s: two, end_s: two, start_rpm: rpm, end_rpm: rpm },
          { phase: 'deceleration', start_s: two, end_s: four, start_rpm: rpm, end_rpm: fifteen },
        ],
        diagram_points: [
          { boundary: 'cycle_start', x: 0, y: 100 },
          { boundary: 'acceleration_end', x: 500, y: 0 },
          { boundary: 'steady_end', x: 500, y: 0 },
          { boundary: 'cycle_end', x: 1000, y: 100 },
          { boundary: 'repeat_acceleration_end', x: 1500, y: 0 },
          { boundary: 'repeat_steady_end', x: 1500, y: 0 },
          { boundary: 'repeat_cycle_end', x: 2000, y: 100 },
        ],
        formula_references: [
          'ПМИ Р130У, редакция 01, 2024, страница 14, формула 4',
          'ПМИ Р130У, редакция 01, 2024, страница 14, формула 5',
          'ПМИ Р130У, редакция 01, 2024, страница 14, формула 6',
          'ПМИ Р130У, редакция 01, 2024, страница 14, формула 7',
          'ПМИ Р130У, редакция 01, 2024, страница 13, таблица 4',
        ],
      },
      inputContentSha256,
      operationSha256,
      contentSha256: 'd'.repeat(64),
      createdAtUtc,
    },
  });
}

function previewRbdPlanSource(
  execution: ReliabilityExecution,
  imported: ImportedRunDetail,
  selection: 'original' | 'effective',
): RbdPlanSource {
  const selectedPlan =
    selection === 'original' ? imported.projection.originalPlan : imported.projection.effectivePlan;
  return rbdPlanSourceSchema.parse({
    executionId: execution.executionId,
    localImportId: imported.summary.localImportId,
    packageId: imported.summary.packageId,
    runId: imported.summary.runId,
    exportRevision: imported.summary.exportRevision,
    outerPackageSha256: imported.summary.outerPackageSha256,
    sourceSnapshotSha256: imported.summary.sourceSnapshotSha256,
    producerName: imported.summary.producerName,
    producerVersion: imported.summary.producerVersion,
    producerBuildId: imported.summary.producerBuildId,
    producerGitCommit: imported.summary.producerGitCommit,
    planSelection: selection,
    payloadPath: selection === 'original' ? 'plan/original.json' : 'plan/effective.json',
    payloadSha256: 'a'.repeat(64),
    planId: selectedPlan.planId,
    planRevision: selectedPlan.planRevision,
    sourceValues: {
      nominalRpm: '1500',
      baseCycles: '1000',
      reserveFactor: '1.5003',
      accelerationDurationS: '5',
      decelerationDurationS: '5',
    },
    methodicalRequirements: {
      requiredCyclesExact: '1500.3',
      requiredSteadyDurationSExact: '60.012',
    },
    executionTargets: {
      targetCycles: '1501',
      targetSteadyDurationS: '60.04',
      totalDurationS: '70.04',
      roundingPolicy: 'ceiling',
    },
  });
}

function previewRbdCalculationDetail(
  command: RbdCalculationCreateCommand,
  execution: ReliabilityExecution,
  source: RbdPlanSource,
): RbdCalculationDetail {
  // Synthetic fixture values only: this adapter never implements the engineering formulas.
  const rational = (numerator: string, denominator: string, decimal: string) => ({
    numerator,
    denominator,
    decimal,
    decimal_preview: decimal,
  });
  const zero = rational('0', '1', '0');
  const five = rational('5', '1', '5');
  const rpm = rational('1500', '1', '1500');
  const steadyEnd = rational('16253', '250', '65.012');
  const cycleEnd = rational('17503', '250', '70.012');
  const sourceFieldValues = {
    nominal_rpm: source.sourceValues.nominalRpm,
    base_cycles: source.sourceValues.baseCycles,
    reserve_factor: source.sourceValues.reserveFactor,
    acceleration_duration_s: source.sourceValues.accelerationDurationS,
    deceleration_duration_s: source.sourceValues.decelerationDurationS,
  };
  const fieldUnits = {
    nominal_rpm: 'rpm',
    base_cycles: 'cycle',
    reserve_factor: '1',
    acceleration_duration_s: 's',
    deceleration_duration_s: 's',
  };
  const orderedFields = [
    'base_cycles',
    'reserve_factor',
    'nominal_rpm',
    'acceleration_duration_s',
    'deceleration_duration_s',
  ] as const;
  const fieldSelections = orderedFields.map((field) => {
    const selected = command.selections.find((item) => item.field === field);
    if (selected === undefined) throw new Error('preview_rbd_selection_missing');
    const rawSourceValue = sourceFieldValues[field];
    return {
      field,
      unit: fieldUnits[field],
      origin: selected.origin,
      value: selected.origin === 'manual' ? (selected.manualValue ?? '') : (rawSourceValue ?? ''),
      rawSourceValue,
      sourceReference: `${source.payloadPath}#/${source.planSelection === 'effective' ? 'effective_plan/effective_plan/' : ''}source_values/${field}`,
      basis: selected.origin === 'manual' ? selected.basis : '',
      evidence: null,
    };
  });
  return rbdCalculationDetailSchema.parse({
    inputSnapshot: {
      analysisInputSnapshotId: command.analysisInputSnapshotId,
      executionId: execution.executionId,
      localImportId: source.localImportId,
      wheelModelId: execution.wheelModelId,
      localSpecimenId: execution.localSpecimenId,
      sourceSpecimenId: execution.sourceSpecimenId,
      sourceRunId: source.runId,
      exportRevision: source.exportRevision,
      planSelection: source.planSelection,
      planId: source.planId,
      planRevision: source.planRevision,
      planPayloadPath: source.payloadPath,
      planPayloadSha256: source.payloadSha256,
      sourceOuterPackageSha256: source.outerPackageSha256,
      sourceSnapshotSha256: source.sourceSnapshotSha256,
      operationSha256: 'b'.repeat(64),
      inputSnapshot: {
        schemaVersion: 2,
        operation: {
          schemaVersion: 1,
          analysisInputSnapshotId: command.analysisInputSnapshotId,
          calculationSnapshotId: command.calculationSnapshotId,
          executionId: execution.executionId,
          planSelection: source.planSelection,
          selections: command.selections.map((item) => ({
            field: item.field,
            origin: item.origin,
            manual_value: item.origin === 'manual' ? item.manualValue : null,
            basis: item.origin === 'manual' ? item.basis : '',
            evidence: null,
          })),
          failureEvidence: null,
          actor: command.actor,
          reason: command.reason,
          algorithmId: 'rbd_reference',
          algorithmVersion: '1.0.0',
          numericPolicy: 'exact_fraction_v1',
        },
        source: {
          executionId: execution.executionId,
          localImportId: source.localImportId,
          packageId: source.packageId,
          runId: source.runId,
          exportRevision: source.exportRevision,
          outerPackageSha256: source.outerPackageSha256,
          sourceSnapshotSha256: source.sourceSnapshotSha256,
          producer: {
            name: source.producerName,
            version: source.producerVersion,
            buildId: source.producerBuildId,
            gitCommit: source.producerGitCommit,
          },
          planSelection: source.planSelection,
          payloadPath: source.payloadPath,
          payloadSha256: source.payloadSha256,
          planId: source.planId,
          planRevision: source.planRevision,
          methodicalRequirements: {
            required_cycles_exact: source.methodicalRequirements.requiredCyclesExact,
            required_steady_duration_s_exact:
              source.methodicalRequirements.requiredSteadyDurationSExact,
          },
          executionTargets: {
            target_cycles: source.executionTargets.targetCycles,
            target_steady_duration_s: source.executionTargets.targetSteadyDurationS,
            total_duration_s: source.executionTargets.totalDurationS,
            rounding_policy: source.executionTargets.roundingPolicy,
          },
        },
        fieldSelections,
        failureEvidence: null,
      },
      contentSha256: 'c'.repeat(64),
      actor: command.actor,
      decisionReason: command.reason,
      createdAtUtc: '2026-09-09T12:05:00.000Z',
    },
    calculationSnapshot: {
      calculationSnapshotId: command.calculationSnapshotId,
      analysisInputSnapshotId: command.analysisInputSnapshotId,
      executionId: execution.executionId,
      wheelModelId: execution.wheelModelId,
      algorithmId: 'rbd_reference',
      algorithmVersion: '1.0.0',
      numericPolicy: 'exact_fraction_v1',
      resultSnapshot: {
        algorithm_id: 'rbd_reference',
        algorithm_version: '1.0.0',
        numeric_policy: 'exact_fraction_v1',
        maximum_rpm: rpm,
        required_cycles_exact: rational('15003', '10', '1500.3'),
        steady_duration_s_exact: rational('15003', '250', '60.012'),
        cycle_duration_s_exact: cycleEnd,
        total_duration_s_exact: cycleEnd,
        failure_result: {
          status: 'not_applicable',
          cycles_to_failure: null,
          reason_code: 'failure_duration_unavailable',
        },
        phases: [
          { phase: 'acceleration', start_s: zero, end_s: five, start_rpm: zero, end_rpm: rpm },
          {
            phase: 'steady_rotation',
            start_s: five,
            end_s: steadyEnd,
            start_rpm: rpm,
            end_rpm: rpm,
          },
          {
            phase: 'deceleration',
            start_s: steadyEnd,
            end_s: cycleEnd,
            start_rpm: rpm,
            end_rpm: zero,
          },
        ],
        diagram_points: [
          { boundary: 'start', x: 0, y: 100 },
          { boundary: 'acceleration_end', x: 150, y: 0 },
          { boundary: 'steady_end', x: 850, y: 0 },
          { boundary: 'cycle_end', x: 1000, y: 100 },
        ],
        formula_references: ['ПМИ, формула 1', 'ПМИ, формула 2', 'ПМИ, формула 3'],
      },
      inputContentSha256: 'c'.repeat(64),
      operationSha256: 'b'.repeat(64),
      contentSha256: 'd'.repeat(64),
      createdAtUtc: '2026-09-09T12:05:00.000Z',
    },
  });
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

function validationError<TResult>(message: string): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'validation_error',
      message,
      details: {},
      retryable: false,
    },
  };
}

function revisionConflict<TResult>(actualVersionId: string | null): DesktopResult<TResult> {
  return {
    ok: false,
    error: {
      code: 'revision_conflict',
      message: 'Сохранённая версия изменилась; черновик не применён.',
      details: { actualVersionId },
      retryable: false,
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
