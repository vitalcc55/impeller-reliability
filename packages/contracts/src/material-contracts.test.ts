import { describe, expect, it } from 'vitest';
import {
  materialOpenPayloadSchema,
  materialPagePayloadSchema,
  inspectionMaterialPageSchema,
  materialOpenedResultSchema,
  workerRequestSchema,
  parseWorkerResponse,
  materialCopyDiscardPayloadSchema,
} from './index';

const origin = {
  projectId: '14b8b422-f97f-4678-a0b4-f1418667c334',
  localImportId: 'c09f55bf-473d-4b5c-98ee-9789c1a5c32c',
  packageId: 'package',
  runId: 'run',
  exportRevision: 1,
  outerPackageSha256: 'a'.repeat(64),
};
const identity = { origin, kind: 'protocol', materialId: '1208925819614629174706176' };
const verification = {
  validatorVersion: 'm03b.3',
  validationContractCommit: 'b7792758b407ffc52d2fff051243056f63dbf18f',
  scope: 'source_material_metadata',
  semanticVerdict: 'passed',
  findingCounts: { error: 0, warning: 0, info: 0, total: 0, truncated: false },
  findings: [],
};
const item = {
  sourceIndex: 0,
  materialId: 'inspection',
  state: 'verified',
  data: {
    schemaVersion: 'r130sh.inspection.v1',
    inspectionId: 'inspection',
    runId: 'run',
    stage: 'pre_test',
    tripIndex: null,
    performedAtUtc: '2026-09-30T09:00:00+00:00',
    runElapsedS: '0',
    actor: { employeeId: 'employee', fullName: 'Source employee', position: 'Engineer' },
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
    comment: '',
    attachmentIds: [],
  },
  references: [],
  detail: null,
};
describe('source material typed boundary', () => {
  it('preserves false, zero, null and complete item data', () => {
    const page = inspectionMaterialPageSchema.parse({
      origin,
      verification,
      items: [item],
      nextCursor: null,
      pageBound: null,
    });
    expect(page.items[0]?.data?.findings.cracks).toBe(false);
    expect(page.items[0]?.data?.runElapsedS).toBe('0');
    expect(page.items[0]?.data?.tripIndex).toBeNull();
  });
  it('rejects inconsistent verified-null state and oversized UTF8 text', () => {
    const base = {
      origin,
      verification,
      items: [{ ...item, data: null }],
      nextCursor: null,
      pageBound: null,
    };
    expect(inspectionMaterialPageSchema.safeParse(base).success).toBe(false);
    expect(
      inspectionMaterialPageSchema.safeParse({
        ...base,
        items: [{ ...item, data: { ...item.data, comment: 'я'.repeat(8193) } }],
      }).success,
    ).toBe(false);
  });
  it.each(['path', 'absolutePath', 'memberPath', 'destination', 'url'])(
    'does not grant renderer %s capability',
    (key) => {
      expect(
        materialOpenPayloadSchema.safeParse({
          identity,
          operationId: origin.localImportId,
          [key]: 'C:\\outside.pdf',
        }).success,
      ).toBe(false);
    },
  );
  it('has bounded request pages and string identities without numeric rounding', () => {
    expect(materialPagePayloadSchema.parse({ origin }).limit).toBe(25);
    expect(materialPagePayloadSchema.safeParse({ origin, limit: 51 }).success).toBe(false);
    expect(
      materialOpenPayloadSchema.parse({ identity, operationId: origin.localImportId }).identity
        .materialId,
    ).toBe(identity.materialId);
    expect(
      materialOpenPayloadSchema.safeParse({
        identity: { ...identity, materialId: 2 ** 80 },
        operationId: origin.localImportId,
      }).success,
    ).toBe(false);
  });
  it('keeps path only on the internal worker response', () => {
    const request = {
      protocolVersion: 1,
      requestId: 'request',
      kind: 'request',
      revision: 1,
      deadlineMs: 30000,
      operation: 'importedRun.resolveMaterial',
      payload: {
        identity,
        outputDirectory: 'C:\\private',
        copyId: origin.localImportId,
        copyByteLimit: 1000,
      },
    };
    expect(workerRequestSchema.safeParse(request).success).toBe(true);
    const result = {
      identity,
      absolutePath: 'C:\\private\\copy.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 100,
      sha256: 'b'.repeat(64),
      fileIdentity: { fileId: 'a'.repeat(32), volumeId: 'b'.repeat(16) },
    };
    expect(
      parseWorkerResponse('importedRun.resolveMaterial', {
        protocolVersion: 1,
        requestId: 'request',
        kind: 'response',
        revision: 1,
        ok: true,
        result,
        evidence: {},
        warnings: [],
      }).ok,
    ).toBe(true);
    expect(materialOpenedResultSchema.safeParse({ ...result, opened: true }).success).toBe(false);
  });
  it('preserves native file identity bits on the private discard boundary', () => {
    const payload = {
      approvedDirectory: 'C:\\private',
      copyId: origin.localImportId,
      mediaType: 'application/pdf',
      fileIdentity: { fileId: 'f'.repeat(32), volumeId: 'f'.repeat(16) },
    };
    expect(materialCopyDiscardPayloadSchema.parse(payload).fileIdentity).toEqual(
      payload.fileIdentity,
    );
    expect(
      materialCopyDiscardPayloadSchema.safeParse({
        ...payload,
        fileIdentity: { ...payload.fileIdentity, fileId: 2 ** 80 },
      }).success,
    ).toBe(false);
    expect(
      materialCopyDiscardPayloadSchema.safeParse({
        ...payload,
        fileIdentity: { ...payload.fileIdentity, fileId: 'F'.repeat(32) },
      }).success,
    ).toBe(false);
    expect(
      materialOpenPayloadSchema.safeParse({
        identity,
        operationId: origin.localImportId,
        ...payload,
      }).success,
    ).toBe(false);
  });
});
