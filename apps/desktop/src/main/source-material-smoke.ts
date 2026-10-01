import { createHash, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import type {
  InspectionMaterialPage,
  PhotoMaterialPage,
  ProtocolMaterialDetail,
  MaterialOrigin,
  MaterialIdentity,
  MaterialCopyResult,
} from '@impeller-reliability/contracts';
import type { MaterialCopies } from './material-open';
import type { WorkerClient } from './worker-client';

interface MaterialView {
  readonly inspections: InspectionMaterialPage;
  readonly photos: PhotoMaterialPage;
  readonly protocol: ProtocolMaterialDetail;
}
interface CopiedMaterial {
  readonly kind: MaterialIdentity['kind'];
  readonly materialId: string;
  readonly mediaType: MaterialCopyResult['mediaType'];
  readonly sha256: string;
  readonly sizeBytes: number;
}
export interface SourceMaterialSmokeEvidence {
  readonly origin: MaterialOrigin;
  readonly protocolReleaseId: string;
  readonly protocolRevision: string;
  readonly copiedMaterials: readonly CopiedMaterial[];
  readonly sqliteUnchanged: boolean;
}

async function closeSmokeProject(worker: WorkerClient): Promise<void> {
  const response = await worker.request('project.close', {});
  if (!response.ok) throw new Error(`material_smoke_close:${response.error.code}`);
}

// Resolve and verify the actual bundled-worker copy without launching a viewer.
// OS handoff/error behavior belongs to MaterialOpener and its separate tests.
export async function runSourceMaterialSmoke(
  worker: WorkerClient,
  copies: MaterialCopies,
  projectPath: string,
  sourcePath: string,
  expectedArchiveSha256: string,
  applicationInstanceId: string,
): Promise<SourceMaterialSmokeEvidence> {
  const opened = await worker.request('project.open', { path: projectPath, applicationInstanceId });
  if (!opened.ok) throw new Error(`material_smoke_open:${opened.error.code}`);
  const jobId = randomUUID();
  let imported = await worker.request('runPackageImport.start', {
    jobId,
    sourcePath,
    allowDiagnosticPartial: false,
  });
  const importDeadline = performance.now() + 180_000;
  while (
    imported.ok &&
    !['completed', 'failed', 'cancelled'].includes(imported.result.state) &&
    performance.now() < importDeadline
  ) {
    await new Promise<void>((done) => setTimeout(done, 25));
    imported = await worker.request('runPackageImport.get', { jobId });
  }
  if (!imported.ok || imported.result.state !== 'completed' || imported.result.result === null)
    throw new Error('material_smoke_import_incomplete');
  const source = imported.result.result.importedRun;
  if (source.localSpecimenId !== null || source.outerPackageSha256 !== expectedArchiveSha256)
    throw new Error('material_smoke_source_identity');
  const discarded = await worker.request('runPackageImport.discard', { jobId });
  if (!discarded.ok) throw new Error(`material_smoke_discard:${discarded.error.code}`);
  const origin: MaterialOrigin = {
    projectId: opened.result.projectId,
    localImportId: source.localImportId,
    packageId: source.packageId,
    runId: source.runId,
    exportRevision: source.exportRevision,
    outerPackageSha256: source.outerPackageSha256,
  };
  await closeSmokeProject(worker);
  const database = join(projectPath, 'project.sqlite');
  const before = await readFile(database);
  let first: MaterialView | null = null;
  const copiedMaterials: CopiedMaterial[] = [];
  for (let pass = 0; pass < 2; pass += 1) {
    const reopened = await worker.request('project.open', {
      path: projectPath,
      applicationInstanceId,
    });
    if (!reopened.ok || reopened.result.projectId !== origin.projectId)
      throw new Error('material_smoke_reopen_identity');
    try {
      const inspections = await worker.request('importedRun.listInspectionPage', {
        origin,
        cursor: null,
        limit: 25,
      });
      const photos = await worker.request('importedRun.listPhotoPage', {
        origin,
        cursor: null,
        limit: 25,
      });
      const protocol = await worker.request('importedRun.getProtocol', { origin });
      if (!inspections.ok || !photos.ok || !protocol.ok)
        throw new Error('material_smoke_read_failed');
      const view: MaterialView = {
        inspections: inspections.result,
        photos: photos.result,
        protocol: protocol.result,
      };
      if (
        ![view.inspections.origin, view.photos.origin, view.protocol.origin].every((value) =>
          isDeepStrictEqual(value, origin),
        )
      )
        throw new Error('material_smoke_read_origin');
      if (first !== null) {
        if (!isDeepStrictEqual(view, first)) throw new Error('material_smoke_reopen_changed');
        continue;
      }
      first = view;
      const preTest = view.inspections.items.find((item) => item.data?.stage === 'pre_test');
      if (
        preTest?.data?.runElapsedS !== '0' ||
        preTest.data.findings.cracks !== false ||
        preTest.materialId === null
      )
        throw new Error('material_smoke_inspection_fields');
      const inspection = await worker.request('importedRun.getInspection', {
        origin,
        inspectionId: preTest.materialId,
      });
      if (!inspection.ok || !isDeepStrictEqual(inspection.result.item, preTest))
        throw new Error('material_smoke_inspection_detail');
      if (
        view.photos.items.length !== 2 ||
        !view.photos.items.some((item) => item.data?.inspectionId === null) ||
        !view.photos.items.some((item) => item.data?.inspectionId === preTest.materialId)
      )
        throw new Error('material_smoke_photo_relationships');
      const release = view.protocol.item;
      if (release.state !== 'verified' || release.data === null || release.materialId === null)
        throw new Error('material_smoke_protocol_missing');
      const expected: {
        readonly identity: MaterialIdentity;
        readonly sha256: string;
        readonly sizeBytes: number;
        readonly mediaType: MaterialCopyResult['mediaType'];
      }[] = [];
      for (const photo of view.photos.items) {
        if (
          photo.state !== 'verified' ||
          photo.data?.availability !== 'available' ||
          photo.materialId === null
        )
          throw new Error('material_smoke_photo_unavailable');
        expected.push({
          identity: { origin, kind: 'photo', materialId: photo.materialId },
          sha256: photo.data.sha256,
          sizeBytes: photo.data.size,
          mediaType: photo.data.mediaType,
        });
      }
      expected.push({
        identity: { origin, kind: 'protocol', materialId: release.materialId },
        sha256: release.data.contentSha256,
        sizeBytes: release.data.pdfSizeBytes,
        mediaType: 'application/pdf',
      });
      for (const material of expected) {
        const copyId = randomUUID();
        const target = await copies.prepare(copyId);
        try {
          const resolved = await worker.request('importedRun.resolveMaterial', {
            identity: material.identity,
            outputDirectory: target.directory,
            copyId,
            copyByteLimit: target.byteLimit,
          });
          if (!resolved.ok) throw new Error(`material_smoke_copy:${resolved.error.code}`);
          if (
            !isDeepStrictEqual(resolved.result.identity, material.identity) ||
            resolved.result.mediaType !== material.mediaType ||
            resolved.result.sha256 !== material.sha256 ||
            resolved.result.sizeBytes !== material.sizeBytes
          )
            throw new Error('material_smoke_copy_identity');
          await copies.verify(copyId, resolved.result);
          const content = await readFile(resolved.result.absolutePath);
          if (
            content.length !== material.sizeBytes ||
            createHash('sha256').update(content).digest('hex') !== material.sha256
          )
            throw new Error('material_smoke_copy_bytes');
          copiedMaterials.push({
            kind: material.identity.kind,
            materialId: material.identity.materialId,
            mediaType: material.mediaType,
            sizeBytes: content.length,
            sha256: material.sha256,
          });
        } finally {
          try {
            await copies.remove(copyId);
          } finally {
            await copies.release();
          }
        }
      }
    } finally {
      await closeSmokeProject(worker);
    }
  }
  if (first?.protocol.item.data == null) throw new Error('material_smoke_evidence_missing');
  if (!(await readFile(database)).equals(before)) throw new Error('material_smoke_project_changed');
  return {
    origin,
    protocolReleaseId: first.protocol.item.data.releaseId,
    protocolRevision: first.protocol.item.data.revisionNumber,
    copiedMaterials,
    sqliteUnchanged: true,
  };
}
