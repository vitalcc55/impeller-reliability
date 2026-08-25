import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { z } from 'zod';

const manifestSchema = z.object({
  schemaVersion: z.literal(1),
  executable: z.literal('impeller-reliability-worker.exe'),
  sha256: z.string().regex(/^[a-f0-9]{64}$/u),
});

export async function assertWorkerIntegrity(executablePath: string): Promise<void> {
  const manifestPath = join(dirname(executablePath), 'worker-manifest.json');
  const manifest = manifestSchema.parse(JSON.parse(await readFile(manifestPath, 'utf8')));
  const digest = createHash('sha256')
    .update(await readFile(executablePath))
    .digest('hex');
  if (digest !== manifest.sha256) throw new Error('worker_integrity_mismatch');
}
