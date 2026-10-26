import { execFile, spawn } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { promisify } from 'node:util';
import { describe, expect, it } from 'vitest';

const execute = promisify(execFile);
const policy = resolve('apps/desktop/resources/installer-policy.ps1');
const powershell = join(
  process.env['SystemRoot'] ?? 'C:\\Windows',
  'System32/WindowsPowerShell/v1.0/powershell.exe',
);

async function resultCode(args: readonly string[]): Promise<number> {
  try {
    await execute(
      powershell,
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', policy, ...args],
      { timeout: 15_000 },
    );
    return 0;
  } catch (error: unknown) {
    if (error instanceof Error && 'code' in error && typeof error.code === 'number')
      return error.code;
    throw error;
  }
}

describe('installer safety policy', () => {
  it.each([
    ['1.9.0', '1.10.0', 0],
    ['1.10.0', '1.10.0', 0],
    ['1.10.0', '1.9.0', 30],
    ['broken', '1.10.0', 20],
  ] as const)('checks installed %s against incoming %s', async (installed, incoming, expected) => {
    expect(
      await resultCode([
        '-Mode',
        'Version',
        '-InstalledVersion',
        installed,
        '-IncomingVersion',
        incoming,
      ]),
    ).toBe(expected);
  });

  it.each([
    {
      description: 'blocks a worker from its installation without stopping either process',
      ownWorkerPresent: true,
      expectedCode: 10,
    },
    {
      description: 'permits replacement after its worker exits while a neighbouring worker remains',
      ownWorkerPresent: false,
      expectedCode: 0,
    },
  ])('$description', async ({ ownWorkerPresent, expectedCode }) => {
    const root = await mkdtemp(join(tmpdir(), 'impeller-installer-'));
    const installation = join(root, 'Installed');
    const neighbour = `${installation}-other`;
    await mkdir(installation);
    await mkdir(neighbour);
    const ownExe = join(installation, 'impeller-reliability-worker.exe');
    const otherExe = join(neighbour, 'impeller-reliability-worker.exe');
    const commandHost = join(process.env['SystemRoot'] ?? 'C:\\Windows', 'System32/cmd.exe');
    // The observer depends on the image path, not worker protocol. A small idle
    // Windows process proves the same boundary without copying two Node runtimes.
    await copyFile(commandHost, ownExe);
    await copyFile(commandHost, otherExe);
    const owned = spawn(ownExe, ['/d', '/q', '/k'], { windowsHide: true });
    const other = spawn(otherExe, ['/d', '/q', '/k'], { windowsHide: true });
    const ownedExit = new Promise<void>((done) => owned.once('exit', () => done()));
    const otherExit = new Promise<void>((done) => other.once('exit', () => done()));
    try {
      await Promise.all([
        new Promise<void>((done) => owned.once('spawn', done)),
        new Promise<void>((done) => other.once('spawn', done)),
      ]);
      if (!ownWorkerPresent) {
        owned.kill();
        await ownedExit;
      }
      expect(
        await resultCode([
          '-Mode',
          'Processes',
          '-InstallationDirectory',
          installation,
          '-InstallerProcessId',
          String(process.pid),
        ]),
      ).toBe(expectedCode);
      if (ownWorkerPresent) expect(owned.exitCode).toBeNull();
      expect(other.exitCode).toBeNull();
    } finally {
      owned.kill();
      other.kill();
      await Promise.all([ownedExit, otherExit]);
      await rm(root, { recursive: true, force: true });
    }
  });
});
