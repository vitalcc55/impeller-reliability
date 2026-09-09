import { describe, expect, it } from 'vitest';

import type { RuntimeStatus } from '@impeller-reliability/contracts';

import { createPreviewApi } from './preview-api';

describe('browser preview api', () => {
  it('provides deterministic ready state without Electron preload', async () => {
    const api = createPreviewApi('ready');
    const status = await api.system.getStatus();
    expect(status).toMatchObject({
      workerStatus: 'ready',
      sqliteStatus: 'ok',
      mode: 'development',
    });
    expect(status.message).toContain('без сохранения');
    await expect(api.system.ping()).resolves.toMatchObject({ workerStatus: 'ready' });
  });

  it('provides an actionable unavailable state', async () => {
    const api = createPreviewApi('unavailable');
    const status = await api.system.getStatus();
    expect(status).toMatchObject({
      workerStatus: 'unavailable',
      sqliteStatus: 'error',
    });
    expect(status.message).toContain('смоделирован');
    await expect(api.system.ping()).rejects.toThrow('preview_worker_unavailable');
    await expect(api.reliabilityExecution.listPage(crypto.randomUUID())).resolves.toMatchObject({
      ok: false,
      error: { code: 'worker_unavailable' },
    });
    await expect(api.reliabilityObservation.getVersion(crypto.randomUUID())).resolves.toMatchObject(
      { ok: false, error: { code: 'worker_unavailable' } },
    );
    await expect(api.reliabilityDataset.getVersion(crypto.randomUUID())).resolves.toMatchObject({
      ok: false,
      error: { code: 'worker_unavailable' },
    });
    const observed: RuntimeStatus[] = [];
    const unsubscribe = api.system.subscribeStatus((nextStatus) => observed.push(nextStatus));
    await expect(api.system.restart()).resolves.toMatchObject({ workerStatus: 'ready' });
    expect(observed).toHaveLength(1);
    unsubscribe();
  });

  it('advances validation polling even when the first active snapshot is unchanged', async () => {
    const api = createPreviewApi('ready');
    const jobId = '8ab377f2-cfd8-4983-86ea-25f5d0171bd7';

    const started = await api.runPackageValidation.selectAndStart({ jobId });
    expect(started).toMatchObject({ ok: true, result: { state: 'running' } });
    await expect(api.runPackageValidation.discard(jobId)).resolves.toMatchObject({
      ok: false,
      error: { code: 'operation_in_progress' },
    });
    await expect(
      api.runPackageValidation.selectAndStart({
        jobId: '40f4acbf-5f06-4d75-a65c-382141d785aa',
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'operation_in_progress' } });
    const firstPoll = await api.runPackageValidation.get(jobId);
    expect(firstPoll).toMatchObject({ ok: true, result: { state: 'running' } });
    const secondPoll = await api.runPackageValidation.get(jobId);
    expect(secondPoll).toMatchObject({
      ok: true,
      result: { state: 'completed', report: { structuralVerdict: 'passed' } },
    });
    await expect(api.runPackageValidation.cancel(jobId)).resolves.toMatchObject({
      ok: true,
      result: { state: 'completed' },
    });
    await api.system.restart();
    await expect(api.runPackageValidation.get(jobId)).resolves.toMatchObject({
      ok: false,
      error: { code: 'entity_not_found' },
    });
  });

  it('models import progress, persisted source reads and explicit specimen binding', async () => {
    const api = createPreviewApi('ready');
    await api.project.create({
      name: 'M03B preview',
      projectNumber: '',
      description: '',
      status: 'draft',
    });
    const jobId = 'ec7cc676-e40d-4ad7-b038-83e0035dc212';
    await expect(
      api.runPackageImport.selectAndStart({ jobId, allowDiagnosticPartial: false }),
    ).resolves.toMatchObject({ ok: true, result: { state: 'copying' } });
    await expect(api.runPackageImport.get(jobId)).resolves.toMatchObject({
      ok: true,
      result: { state: 'copying' },
    });
    const completed = await api.runPackageImport.get(jobId);
    expect(completed).toMatchObject({
      ok: true,
      result: { state: 'completed', result: { disposition: 'existing' } },
    });
    const replacementJobId = '31871fa4-2088-4f0d-bcb4-dd5454294edc';
    await expect(
      api.runPackageImport.selectAndStart({
        jobId: replacementJobId,
        allowDiagnosticPartial: false,
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'operation_in_progress' } });
    await expect(
      api.runPackageImport.selectAndStart({
        jobId: replacementJobId,
        replaceJobId: jobId,
        allowDiagnosticPartial: false,
      }),
    ).resolves.toMatchObject({ ok: true, result: { state: 'copying' } });
    const listed = await api.importedRun.list();
    expect(listed).toMatchObject({ ok: true, result: [{ sourceIntegrity: 'verified' }] });
    if (!listed.ok || listed.result[0] === undefined) throw new Error('missing preview import');
    const summary = listed.result[0];
    await expect(api.importedRun.get(summary.localImportId)).resolves.toMatchObject({
      ok: true,
      result: { summary: { runId: summary.runId } },
    });
    await expect(
      api.importedRun.bindSpecimen({
        sourceSpecimenId: summary.sourceSpecimenId,
        localSpecimenId: null,
        expectedRevision: summary.bindingRevision,
        actor: 'local_user',
        reason: 'Явно оставить без привязки',
      }),
    ).resolves.toMatchObject({ ok: true, result: { recordRevision: 1 } });
  });

  it('models the explicit M04B observation and immutable dataset flow', async () => {
    const api = createPreviewApi('ready');
    await api.project.create({ name: 'M04B', projectNumber: '', description: '', status: 'draft' });
    const wheelId = '28723636-fdd5-47bd-b0e2-e02c21f36f2e';
    const specimenId = 'fa50e13e-2944-4874-9cf7-b4747d57ae09';
    await api.wheelModel.create({
      wheelModelId: wheelId,
      fullName: 'Колесо M04B',
      designation: 'РБД-01',
      nominalDiameterMm: null,
      nominalSpeedRpm: null,
      bladeCount: null,
      geometryDescription: '',
      compositionDescription: '',
      materialDescription: '',
      notes: '',
    });
    await api.specimen.create({
      specimenId,
      wheelModelId: wheelId,
      identificationNumber: 'M04B-001',
      batchNumber: '',
      marking: '',
      manufacturedOn: null,
      receivedOn: null,
      workingDiameterMm: null,
      initialConditionNotes: '',
      notes: '',
    });
    const documentId = '31871fa4-2088-4f0d-bcb4-dd5454294edc';
    await api.caseDocument.create({
      caseDocumentId: documentId,
      document: {
        documentKind: 'typical_test_method',
        title: 'ПМИ Р130У',
        designation: 'ПМИ Р130У',
        revisionLabel: '01',
        documentDate: '2024-07-02',
        issuer: 'ЛИЦ ВВУ',
        notes: '',
      },
      wheelModelIds: [wheelId],
      specimenIds: [specimenId],
    });
    const imported = await api.importedRun.list();
    if (!imported.ok || imported.result[0] === undefined) throw new Error('missing preview import');
    await api.importedRun.bindSpecimen({
      sourceSpecimenId: imported.result[0].sourceSpecimenId,
      localSpecimenId: specimenId,
      expectedRevision: 1,
      actor: 'local_user',
      reason: 'Подтверждён образец',
    });
    const materialized = await api.reliabilityExecution.materialize(
      imported.result[0].localImportId,
    );
    if (!materialized.ok) throw new Error('materialize failed');
    const observationId = '8ab377f2-cfd8-4983-86ea-25f5d0171bd7';
    const observationVersionId = '40f4acbf-5f06-4d75-a65c-382141d785aa';
    const observation = await api.reliabilityObservation.createVersion({
      observationId,
      observationVersionId,
      executionId: materialized.result.executionId,
      expectedPreviousVersionId: null,
      classification: 'right_censored',
      endpointKind: 'right_bound',
      metricKind: 'rbd_steady_rotation_time',
      metricUnit: 'hours',
      metricOrigin: 'analyst_provided',
      lowerValue: '12.5',
      upperValue: null,
      originBasis: 'Начало установившегося вращения',
      endpointBasis: 'Граница наблюдения по журналу',
      documentId,
      documentLocator: 'Раздел 10',
      failureIds: [],
      actor: 'local_user',
      reason: 'Отказ не установлен до границы',
    });
    expect(observation).toMatchObject({ ok: true, result: { disposition: 'created' } });
    const datasetVersionId = 'd860e854-7774-4f30-9591-fc5c106150c2';
    const dataset = await api.reliabilityDataset.createVersion({
      datasetId: 'f2a1c140-6e31-4dd7-a2f0-f0586164f198',
      datasetVersionId,
      wheelModelId: wheelId,
      expectedPreviousVersionId: null,
      title: 'Выборка РБД',
      method: 'rbd',
      metricKind: 'rbd_steady_rotation_time',
      metricUnit: 'hours',
      populationBasis: 'Колёса одной модели',
      methodologyBasis: 'ПМИ Р130У',
      comparabilityBasis: 'Условия подтверждены инженером',
      decisions: [
        { observationVersionId, decision: 'included', reason: 'Известна правая граница' },
      ],
      actor: 'local_user',
      reason: 'Первая версия',
    });
    expect(dataset).toMatchObject({
      ok: true,
      result: { version: { members: [{ policyEligibility: 'eligible' }] } },
    });
    await expect(api.reliabilityDataset.getVersion(datasetVersionId)).resolves.toEqual(
      dataset.ok ? { ok: true, result: dataset.result.version } : dataset,
    );
    await expect(api.reliabilityExecution.listPage(wheelId)).resolves.toMatchObject({
      ok: true,
      result: { items: [{ currentClassification: 'right_censored' }] },
    });
    const invalidVersionId = 'ab2aa7bc-fbc1-49cf-b195-f012b3ae14ef';
    await expect(
      api.reliabilityObservation.createVersion({
        observationId,
        observationVersionId: invalidVersionId,
        executionId: materialized.result.executionId,
        expectedPreviousVersionId: observationVersionId,
        classification: 'invalid',
        endpointKind: 'unavailable',
        metricKind: null,
        metricUnit: null,
        metricOrigin: null,
        lowerValue: null,
        upperValue: null,
        originBasis: 'Не установлено',
        endpointBasis: 'Не установлено',
        documentId,
        documentLocator: 'Заключение',
        failureIds: [],
        actor: 'local_user',
        reason: 'Наблюдение недействительно',
      }),
    ).resolves.toMatchObject({ ok: true });
    await expect(
      api.reliabilityDataset.createVersion({
        datasetId: '69361fbe-bcce-4f84-81f6-2961bbb41e3a',
        datasetVersionId: '29158c9e-2a9d-4b96-8f0d-26af009504d9',
        wheelModelId: wheelId,
        expectedPreviousVersionId: null,
        title: 'Недопустимое включение',
        method: 'rbd',
        metricKind: 'rbd_steady_rotation_time',
        metricUnit: 'hours',
        populationBasis: 'Колёса одной модели',
        methodologyBasis: 'ПМИ Р130У',
        comparabilityBasis: 'Условия подтверждены',
        decisions: [
          { observationVersionId: invalidVersionId, decision: 'included', reason: 'Ошибка' },
        ],
        actor: 'local_user',
        reason: 'Проверка fail closed',
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'validation_error' } });
    await expect(
      api.reliabilityObservation.createVersion({
        observationId,
        observationVersionId: '1ac9701e-e91b-4142-a1dc-b54cf40bdcd2',
        executionId: materialized.result.executionId,
        expectedPreviousVersionId: invalidVersionId,
        classification: 'failure',
        endpointKind: 'exact',
        metricKind: null,
        metricUnit: null,
        metricOrigin: null,
        lowerValue: null,
        upperValue: null,
        originBasis: 'Начало известно',
        endpointBasis: 'Значение отсутствует',
        documentId,
        documentLocator: 'Заключение',
        failureIds: [],
        actor: 'local_user',
        reason: 'Некорректная точная граница',
      }),
    ).resolves.toMatchObject({ ok: false, error: { code: 'validation_error' } });
  });
});
