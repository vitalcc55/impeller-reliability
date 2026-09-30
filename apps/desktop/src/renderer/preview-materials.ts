import {
  inspectionMaterialPageSchema,
  photoMaterialPageSchema,
  protocolMaterialDetailSchema,
  type MaterialOrigin,
} from '@impeller-reliability/contracts';

// These facts describe only the DEV UI; they are not producer acceptance artifacts.
export function previewMaterials(origin: MaterialOrigin, diagnostic: boolean) {
  const verification = {
    validatorVersion: 'm03b.3',
    validationContractCommit: 'b7792758b407ffc52d2fff051243056f63dbf18f',
    scope: 'source_material_metadata',
    semanticVerdict: 'passed',
    findingCounts: { error: 0, warning: 1, info: 0, total: 1, truncated: false },
    findings: [
      {
        code: 'synthetic_reference_warning',
        severity: 'warning',
        location: 'inspections.json',
        message: 'Синтетический пример неразрешённой справочной ссылки.',
        contractSource: 'DEV UI sample',
      },
    ],
  };
  const actor = {
    employeeId: 'synthetic-employee',
    fullName: 'Синтетический сотрудник',
    position: 'Пример для проверки интерфейса',
  };
  const photoId = '6f116b6d-af8a-4a59-971a-677278be71cb';
  const runPhotoId = '54d8c30c-1a91-43ea-b439-dd9be42755b7';
  const absentPhotoId = 'c29b7824-4cd3-4621-acb2-52353bca45bf';
  const ambiguousPhotoId = 'ad6a90d1-c082-401d-ad78-69c474034b63';
  const inspectionData = {
    schemaVersion: 'r130sh.inspection.v1',
    inspectionId: 'synthetic-pre-test',
    runId: origin.runId,
    stage: 'pre_test',
    tripIndex: null,
    performedAtUtc: '2026-09-30T09:00:00+00:00',
    runElapsedS: '0',
    actor,
    findings: {
      cracks: false,
      chips: false,
      deformation: false,
      partialDestruction: false,
      totalDestruction: false,
      balancingElementsState: 'not_assessed',
      otherFindings: '',
    },
    inspectionOutcome: 'inconclusive',
    comment: '<script>это исходный текст, а не HTML</script>',
    attachmentIds: [photoId],
  };
  const photoData = {
    attachmentId: photoId,
    runId: origin.runId,
    inspectionId: inspectionData.inspectionId,
    mediaType: 'image/jpeg',
    size: 632,
    sha256: '1'.repeat(64),
    widthPx: '2',
    heightPx: '2',
    actor: { ...actor, legacy: false },
    attachedAtUtc: '2026-09-30T09:00:00+00:00',
    availability: 'available',
    unavailableReason: null,
  };
  const inspections = inspectionMaterialPageSchema.parse({
    origin,
    verification,
    nextCursor: null,
    pageBound: null,
    items: [
      {
        sourceIndex: 0,
        materialId: inspectionData.inspectionId,
        state: 'verified',
        data: inspectionData,
        references: [{ kind: 'photo', materialId: photoId, status: 'resolved' }],
        detail: null,
      },
      {
        sourceIndex: 1,
        materialId: 'synthetic-vibration-pause',
        state: 'verified',
        data: {
          ...inspectionData,
          inspectionId: 'synthetic-vibration-pause',
          stage: 'vibration_pause',
          tripIndex: '1',
          runElapsedS: '12.5',
          comment: 'Синтетический осмотр после превышения вибропорога.',
          attachmentIds: ['unresolved-photo', ambiguousPhotoId],
        },
        references: [
          { kind: 'photo', materialId: 'unresolved-photo', status: 'unresolved' },
          {
            kind: 'photo',
            materialId: ambiguousPhotoId,
            status: diagnostic ? 'ambiguous' : 'unresolved',
          },
        ],
        detail: null,
      },
    ],
  });
  const photos = photoMaterialPageSchema.parse({
    origin,
    verification,
    nextCursor: null,
    pageBound: null,
    items: [
      {
        sourceIndex: 0,
        materialId: photoId,
        state: 'verified',
        data: photoData,
        references: [
          { kind: 'inspection', materialId: inspectionData.inspectionId, status: 'resolved' },
        ],
        detail: null,
      },
      {
        sourceIndex: 1,
        materialId: runPhotoId,
        state: 'verified',
        data: {
          ...photoData,
          attachmentId: runPhotoId,
          inspectionId: null,
          mediaType: 'image/png',
          size: 75,
          sha256: '2'.repeat(64),
        },
        references: [],
        detail: null,
      },
      ...(diagnostic
        ? [
            {
              sourceIndex: 2,
              materialId: absentPhotoId,
              state: 'unavailable',
              data: {
                ...photoData,
                attachmentId: absentPhotoId,
                inspectionId: null,
                availability: 'unavailable',
                unavailableReason: 'Управляемый файл отсутствовал при диагностическом экспорте.',
              },
              references: [],
              detail: 'Байты фотографии не включены.',
            },
            {
              sourceIndex: 3,
              materialId: ambiguousPhotoId,
              state: 'ambiguous',
              data: { ...photoData, attachmentId: ambiguousPhotoId },
              references: [],
              detail:
                'Идентификатор повторяется в исходном индексе; автоматический выбор запрещён.',
            },
            {
              sourceIndex: 4,
              materialId: 'oversized-record',
              state: 'too_large',
              data: null,
              references: [],
              detail:
                'Исходная запись превышает 64 KiB. Содержимое не обрезано и не показано как проверенное.',
            },
          ]
        : []),
    ],
  });
  const protocol = protocolMaterialDetailSchema.parse({
    origin,
    verification,
    item: diagnostic
      ? {
          sourceIndex: 0,
          materialId: null,
          state: 'not_included',
          data: null,
          references: [],
          detail: null,
        }
      : {
          sourceIndex: 0,
          materialId: '9007199254740995',
          state: 'verified',
          references: [{ kind: 'photo', materialId: photoId, status: 'resolved' }],
          detail: null,
          data: {
            schemaVersion: 'r130sh.protocol-release.v1',
            runId: origin.runId,
            releaseId: '9007199254740995',
            revisionNumber: '9007199254740993',
            protocolNumber: 'Синтетический протокол UI',
            templateVersion: 'synthetic-ui.v1',
            contentSha256: '3'.repeat(64),
            releasedAtUtc: '2026-09-30T10:00:00+00:00',
            releasedByActor: { ...actor, legacy: false, sourceJson: JSON.stringify(actor) },
            photoIds: [photoId],
            pdfSizeBytes: 1488,
          },
        },
  });
  return { inspections, photos, protocol };
}
