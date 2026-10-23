import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import type { WorkerErrorResponse } from '@impeller-reliability/contracts';

import type { WorkerClient } from './worker-client';
import { RecentProjectsStore } from './recent-projects';

async function result<T>(
  response: Promise<{ readonly ok: true; readonly result: T } | WorkerErrorResponse>,
): Promise<T> {
  const value = await response;
  if (!value.ok) throw new Error(`smoke_readback:${value.error.code}`);
  return value.result;
}

async function immutableFiles(
  projectPath: string,
): Promise<readonly { readonly path: string; readonly sha256: string }[]> {
  const files: { path: string; sha256: string }[] = [];
  async function visit(relative: string): Promise<void> {
    for (const entry of await readdir(join(projectPath, relative), { withFileTypes: true })) {
      const path = join(relative, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile())
        files.push({
          path,
          sha256: createHash('sha256')
            .update(await readFile(join(projectPath, path)))
            .digest('hex'),
        });
      else throw new Error('smoke_readback_unexpected_link');
    }
  }
  await visit('assets');
  await visit('imports');
  files.push({
    path: 'project-manifest.json',
    sha256: createHash('sha256')
      .update(await readFile(join(projectPath, 'project-manifest.json')))
      .digest('hex'),
  });
  return files.sort((left, right) => left.path.localeCompare(right.path));
}

// Both releases query the real worker. Readback never recreates/imports/calculates
// data, and compares complete saved snapshots rather than just smoke booleans.
export async function captureSmokePersistence(
  worker: WorkerClient,
  projectPath: string,
  applicationInstanceId: string,
  userDataPath: string,
  seed: boolean,
) {
  const recent = new RecentProjectsStore(join(userDataPath, 'state', 'recent-projects.json'));
  const recentBeforeOpen = await recent.list();
  const project = await result(
    worker.request('project.open', { path: projectPath, applicationInstanceId }),
  );
  try {
    if (seed) await recent.touch(project);
    const customer = await result(worker.request('caseCustomer.get', {}));
    const wheels = await result(worker.request('wheelModel.list', { includeArchived: true }));
    const specimens = await result(worker.request('specimen.list', { includeArchived: true }));
    const documents = await result(
      worker.request('caseDocument.list', { includeArchived: true, documentKind: null }),
    );
    const wheelDetails = [];
    const specimenDetails = [];
    const documentDetails = [];
    const calculations = [];
    for (const wheel of wheels.items) {
      wheelDetails.push(
        await result(worker.request('wheelModel.get', { wheelModelId: wheel.wheelModelId })),
      );
      const query = { wheelModelId: wheel.wheelModelId, cursor: null, limit: 25 };
      const rbd = await result(worker.request('rbdCalculation.listPage', query));
      const rpt = await result(worker.request('rptCalculation.listPage', query));
      const pmn = await result(worker.request('pmnCalculation.listPage', query));
      if (rbd.nextCursor !== null || rpt.nextCursor !== null || pmn.nextCursor !== null)
        throw new Error('smoke_readback_incomplete_history');
      const rbdDetails = [];
      const rptDetails = [];
      const pmnDetails = [];
      for (const item of rbd.items)
        rbdDetails.push(
          await result(
            worker.request('rbdCalculation.getDetail', {
              calculationSnapshotId: item.calculationSnapshotId,
            }),
          ),
        );
      for (const item of rpt.items)
        rptDetails.push(
          await result(
            worker.request('rptCalculation.getDetail', {
              calculationSnapshotId: item.calculationSnapshotId,
            }),
          ),
        );
      for (const item of pmn.items)
        pmnDetails.push(
          await result(
            worker.request('pmnCalculation.getDetail', {
              calculationSnapshotId: item.calculationSnapshotId,
            }),
          ),
        );
      calculations.push({
        wheelModelId: wheel.wheelModelId,
        rbd,
        rpt,
        pmn,
        rbdDetails,
        rptDetails,
        pmnDetails,
      });
    }
    for (const specimen of specimens.items)
      specimenDetails.push(
        await result(worker.request('specimen.get', { specimenId: specimen.specimenId })),
      );
    for (const document of documents.items)
      documentDetails.push(
        await result(
          worker.request('caseDocument.get', { caseDocumentId: document.caseDocumentId }),
        ),
      );
    const imports = await result(worker.request('importedRun.list', {}));
    const importDetails = [];
    for (const item of imports.items) {
      await result(
        worker.request('importedRun.verifySource', { localImportId: item.localImportId }),
      );
      importDetails.push(
        await result(worker.request('importedRun.get', { localImportId: item.localImportId })),
      );
    }
    const material = importDetails.find(
      (item) =>
        item.summary.outerPackageSha256 ===
        process.env['IMPELLER_AUTOMATED_MATERIAL_PACKAGE_SHA256'],
    )?.summary;
    if (
      material === undefined ||
      customer.customer === null ||
      wheelDetails.length !== 1 ||
      specimenDetails.length !== 1 ||
      documentDetails.length < 2 ||
      importDetails.length !== 4 ||
      calculations.some(
        (item) =>
          item.rbd.items.length === 0 || item.rpt.items.length === 0 || item.pmn.items.length === 0,
      )
    )
      throw new Error('smoke_readback_missing_representative_data');
    const origin = {
      projectId: project.projectId,
      localImportId: material.localImportId,
      packageId: material.packageId,
      runId: material.runId,
      exportRevision: material.exportRevision,
      outerPackageSha256: material.outerPackageSha256,
    };
    await result(worker.request('project.close', {}));
    return {
      project,
      customer,
      wheelDetails,
      specimenDetails,
      documentDetails,
      importDetails,
      calculations,
      origin,
      recent: seed ? await recent.list() : recentBeforeOpen,
      userDataPath,
      immutableFiles: await immutableFiles(projectPath),
    };
  } finally {
    await result(worker.request('project.close', {}));
  }
}
