import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

function runSelfTest(executablePath) {
  return new Promise((resolve, reject) => {
    const child = spawn(executablePath, ['--self-test'], {
      shell: false,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.once('error', reject);
    child.once('close', (code) => {
      if (code !== 0) {
        reject(new Error(`packaged_worker_self_test_failed:${String(code)}:${stderr}`));
        return;
      }
      const result = JSON.parse(stdout);
      if (result.passed !== true) reject(new Error('packaged_worker_self_test_returned_failure'));
      else resolve();
    });
  });
}

export default async function afterPackWorkerIntegrity(context) {
  if (context.electronPlatformName !== 'win32') return;
  const workerDirectory = join(context.appOutDir, 'resources', 'python-worker');
  const executablePath = join(workerDirectory, 'impeller-reliability-worker.exe');
  const manifest = JSON.parse(
    await readFile(join(workerDirectory, 'worker-manifest.json'), 'utf8'),
  );
  const digest = createHash('sha256')
    .update(await readFile(executablePath))
    .digest('hex');
  if (
    manifest.schemaVersion !== 1 ||
    manifest.executable !== 'impeller-reliability-worker.exe' ||
    manifest.sha256 !== digest
  ) {
    throw new Error('packaged_worker_integrity_mismatch');
  }
  await runSelfTest(executablePath);
}
