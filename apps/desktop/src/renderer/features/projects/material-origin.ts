import type { MaterialOrigin } from '@impeller-reliability/contracts';

export function sameMaterialOrigin(left: MaterialOrigin, right: MaterialOrigin): boolean {
  return (
    left.projectId === right.projectId &&
    left.localImportId === right.localImportId &&
    left.packageId === right.packageId &&
    left.runId === right.runId &&
    left.exportRevision === right.exportRevision &&
    left.outerPackageSha256 === right.outerPackageSha256
  );
}
