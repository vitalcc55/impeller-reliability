import { createHash, randomUUID } from 'node:crypto';
import {
  mkdtemp,
  realpath,
  writeFile,
  readFile,
  readdir,
  rm,
  mkdir,
  unlink,
  open,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  DesktopResult,
  MaterialCopyResult,
  MaterialIdentity,
  MaterialCopyDiscardCommand,
} from '@impeller-reliability/contracts';
import { MaterialCopies, MaterialOpener, type MaterialSession } from './material-open';

const roots: string[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});
const identity: MaterialIdentity = {
  origin: {
    projectId: '14b8b422-f97f-4678-a0b4-f1418667c334',
    localImportId: 'c09f55bf-473d-4b5c-98ee-9789c1a5c32c',
    packageId: 'package',
    runId: 'run',
    exportRevision: 1,
    outerPackageSha256: 'a'.repeat(64),
  },
  kind: 'protocol',
  materialId: '7',
};
const content = Buffer.from('%PDF-1.4\ntransport testing only\n');
const fileIdentity = { fileId: '1'.padStart(32, '0'), volumeId: '1'.padStart(16, '0') };
const FILE_LIMIT = 100 * 1024 * 1024;
function copyName(id: string, pid = process.pid, worker = 0, limit = FILE_LIMIT): string {
  return `copy-${pid}-${worker}-${limit}-${id}`;
}
function identityName(): string {
  return `identity-${fileIdentity.fileId}-${fileIdentity.volumeId}-pdf`;
}
async function copyDirectory(cache: string, id: string): Promise<string> {
  const name = (await readdir(cache)).find(
    (entry) => entry.startsWith('copy-') && entry.endsWith(id),
  );
  if (name === undefined) throw new Error('copy_directory_missing');
  return join(cache, name);
}
async function fixture(shellResult: string | Error = '') {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'ir-material-open-')));
  roots.push(root);
  const cache = join(root, 'copies');
  let session: MaterialSession | null = { projectId: identity.origin.projectId, epoch: 1 };
  const shell = vi.fn((path: string) => {
    expect(path.startsWith(cache)).toBe(true);
    return shellResult instanceof Error
      ? Promise.reject(shellResult)
      : Promise.resolve(shellResult);
  });
  // Native identity comparison/deletion is exercised in Python; this external
  // side effect stays injectable in the real Main orchestration tests.
  const discard = vi.fn(async (command: MaterialCopyDiscardCommand) => {
    expect(command.fileIdentity).toEqual(fileIdentity);
    await unlink(join(command.approvedDirectory, `${command.copyId}.pdf`));
  });
  const resolve = vi.fn(
    async (
      selected: MaterialIdentity,
      directory: string,
      copyId: string,
      byteLimit: number,
    ): Promise<DesktopResult<MaterialCopyResult>> => {
      expect(content.length).toBeLessThanOrEqual(byteLimit);
      const path = join(directory, `${copyId}.pdf`);
      await writeFile(path, content, { flag: 'wx' });
      return {
        ok: true,
        result: {
          identity: selected,
          absolutePath: path,
          mediaType: 'application/pdf',
          sizeBytes: content.length,
          sha256: createHash('sha256').update(content).digest('hex'),
          fileIdentity,
        },
      };
    },
  );
  const copies = new MaterialCopies(cache, discard);
  const cleanupFailed = vi.fn();
  const opener = new MaterialOpener({
    copies,
    session: () => session,
    resolve,
    openPath: shell,
    cleanupFailed,
  });
  return {
    root,
    cache,
    copies,
    opener,
    shell,
    resolve,
    discard,
    cleanupFailed,
    setSession: (value: MaterialSession | null) => {
      session = value;
    },
  };
}
async function initializeCache(f: Awaited<ReturnType<typeof fixture>>): Promise<void> {
  const id = randomUUID();
  await f.copies.prepare(id);
  await f.copies.remove(id);
  await f.copies.release();
}
async function seedCopy(
  cache: string,
  id: string,
  options: {
    pid?: number;
    worker?: number;
    handed?: boolean;
    known?: boolean;
    bytes?: number;
  } = {},
): Promise<string> {
  const directory = join(cache, copyName(id, options.pid, options.worker));
  await mkdir(directory);
  if (options.known || options.handed) await mkdir(join(directory, identityName()));
  if (options.handed) await mkdir(join(directory, 'handoff'));
  const fd = await open(join(directory, `${id}.pdf`), 'wx');
  try {
    await fd.writeFile(content);
    if (options.bytes !== undefined) await fd.truncate(options.bytes);
  } finally {
    await fd.close();
  }
  return directory;
}

describe('verified material OS handoff', () => {
  it('calls the real injected shell branch and retains successful handoff', async () => {
    const f = await fixture();
    const operationId = randomUUID();
    expect(await f.opener.open({ identity, operationId })).toMatchObject({
      ok: true,
      result: { opened: true, identity },
    });
    expect(f.shell).toHaveBeenCalledOnce();
    const directory = await copyDirectory(f.cache, operationId);
    expect(await readFile(join(directory, `${operationId}.pdf`))).toEqual(content);
    expect(await readdir(directory)).toContain('handoff');
    f.setSession(null);
    f.opener.invalidate();
    await f.opener.drain();
    expect(await readFile(join(directory, `${operationId}.pdf`))).toEqual(content);
    expect(f.discard).not.toHaveBeenCalled();
    expect(f.cleanupFailed).not.toHaveBeenCalled();
  });
  it('handles shell error including no application with native discard and no success', async () => {
    const f = await fixture('No application is associated');
    expect(await f.opener.open({ identity, operationId: randomUUID() })).toMatchObject({
      ok: false,
      error: { code: 'material_open_failed' },
    });
    expect(f.shell).toHaveBeenCalledOnce();
    expect(f.discard).toHaveBeenCalledOnce();
    expect(await readdir(f.cache)).toEqual(['owner']);
  });
  it('retains an ambiguous rejected shell attempt', async () => {
    const f = await fixture(new Error('shell rejected'));
    const id = randomUUID();
    expect(await f.opener.open({ identity, operationId: id })).toMatchObject({
      ok: false,
      error: { code: 'material_open_failed' },
    });
    expect(f.shell).toHaveBeenCalledOnce();
    expect(f.discard).not.toHaveBeenCalled();
    expect(await readFile(join(await copyDirectory(f.cache, id), `${id}.pdf`))).toEqual(content);
  });
  it('does not report success or finish drain before the OS call settles', async () => {
    const f = await fixture();
    let release: (result: string) => void = () => {};
    let entered: () => void = () => {};
    const shellResult = new Promise<string>((done) => {
      release = done;
    });
    const invoked = new Promise<void>((done) => {
      entered = done;
    });
    f.shell.mockImplementation(() => {
      entered();
      return shellResult;
    });
    const id = randomUUID();
    let settled = false;
    let drained = false;
    const pending = f.opener.open({ identity, operationId: id }).then((result) => {
      settled = true;
      return result;
    });
    await invoked;
    const draining = f.opener.drain().then(() => {
      drained = true;
    });
    expect(settled).toBe(false);
    expect(drained).toBe(false);
    expect(f.opener.cancel(id)).toBe(false);
    release('');
    expect(await pending).toMatchObject({ ok: true, result: { opened: true } });
    await draining;
    expect(drained).toBe(true);
    expect(f.discard).not.toHaveBeenCalled();
  });
  it.each(['session', 'cancel', 'close'] as const)(
    'does not open a late copy after %s and discards by identity',
    async (reason) => {
      const f = await fixture();
      const id = randomUUID();
      let release: () => void = () => {};
      let started: () => void = () => {};
      const barrier = new Promise<void>((done) => {
        release = done;
      });
      const entered = new Promise<void>((done) => {
        started = done;
      });
      const original = f.resolve.getMockImplementation();
      if (original === undefined) throw new Error('resolver_missing');
      f.resolve.mockImplementation(async (...args) => {
        started();
        await barrier;
        return original(...args);
      });
      const pending = f.opener.open({ identity, operationId: id });
      await entered;
      if (reason === 'session') f.setSession({ projectId: identity.origin.projectId, epoch: 2 });
      else if (reason === 'close') {
        f.setSession(null);
        f.opener.invalidate();
      } else expect(f.opener.cancel(id)).toBe(true);
      release();
      expect(await pending).toMatchObject({ ok: false, error: { code: 'cancelled' } });
      expect(f.shell).not.toHaveBeenCalled();
      expect(f.discard).toHaveBeenCalledOnce();
      expect(await readdir(f.cache)).toEqual(['owner']);
    },
  );
  it('rejects modified bytes and records native identity before cleanup', async () => {
    const f = await fixture();
    const original = f.resolve.getMockImplementation();
    if (original === undefined) throw new Error('resolver_missing');
    f.resolve.mockImplementation(async (...args) => {
      const result = await original(...args);
      if (result.ok) await writeFile(result.result.absolutePath, 'tampered');
      return result;
    });
    expect(await f.opener.open({ identity, operationId: randomUUID() })).toMatchObject({
      ok: false,
      error: { code: 'file_integrity_mismatch' },
    });
    expect(f.shell).not.toHaveBeenCalled();
    expect(f.discard).toHaveBeenCalledOnce();
    expect(f.cleanupFailed).not.toHaveBeenCalled();
  });
  it('checks bytes again after handoff state publication', async () => {
    const f = await fixture();
    const original = f.copies.handoff.bind(f.copies);
    vi.spyOn(f.copies, 'handoff').mockImplementation(async (id) => {
      await original(id);
      await writeFile(
        join(await copyDirectory(f.cache, id), `${id}.pdf`),
        'tampered during state publication',
      );
    });
    expect(await f.opener.open({ identity, operationId: randomUUID() })).toMatchObject({
      ok: false,
      error: { code: 'file_integrity_mismatch' },
    });
    expect(f.shell).not.toHaveBeenCalled();
    expect(f.discard).toHaveBeenCalledOnce();
  });
  it('rejects a response above the actual remaining reservation', async () => {
    const f = await fixture();
    await initializeCache(f);
    await seedCopy(f.cache, randomUUID(), { handed: true, bytes: 400 * 1024 * 1024 - 10 });
    const id = randomUUID();
    const target = await f.copies.prepare(id);
    expect(target.byteLimit).toBe(10);
    const result: MaterialCopyResult = {
      identity,
      absolutePath: join(target.directory, `${id}.pdf`),
      mediaType: 'application/pdf',
      sizeBytes: content.length,
      sha256: 'a'.repeat(64),
      fileIdentity,
    };
    await expect(f.copies.verify(id, result)).rejects.toMatchObject({ code: 'file_too_large' });
    await f.copies.remove(id);
    await f.copies.release();
  });
  it('preserves live neighbor and surviving worker claims', async () => {
    const f = await fixture();
    const id = randomUUID();
    const target = await f.copies.prepare(id);
    await expect(
      new MaterialCopies(f.cache, f.discard).prepare(randomUUID()),
    ).rejects.toMatchObject({ code: 'operation_in_progress' });
    expect(await readdir(target.directory)).toEqual([]);
    await f.copies.remove(id);
    await f.copies.release();
    const claim = `lease-1073741823-${process.pid}-${randomUUID()}`;
    await mkdir(join(f.cache, claim));
    await expect(
      new MaterialCopies(f.cache, f.discard).prepare(randomUUID()),
    ).rejects.toMatchObject({ code: 'operation_in_progress' });
    expect(await readdir(join(f.cache, claim))).toEqual([]);
  });
  it('allows at most one simultaneous contender after a dead claim', async () => {
    const f = await fixture();
    await initializeCache(f);
    await mkdir(join(f.cache, `lease-1073741823-0-${randomUUID()}`));
    const contenders = Array.from({ length: 4 }, () => new MaterialCopies(f.cache, f.discard));
    const ids = contenders.map(() => randomUUID());
    const results = await Promise.allSettled(
      contenders.map((owner, i) => owner.prepare(ids[i] ?? '')),
    );
    expect(results.filter((result) => result.status === 'fulfilled').length).toBeLessThanOrEqual(1);
    for (const [i, result] of results.entries()) {
      if (result.status === 'rejected')
        expect(result.reason).toMatchObject({ code: 'operation_in_progress' });
      else {
        const owner = contenders[i];
        const id = ids[i];
        if (owner === undefined || id === undefined) throw new Error('contender_missing');
        await owner.remove(id);
        await owner.release();
      }
    }
  });
  it('recovers known crash copy by native identity while preserving unknown and handed copies', async () => {
    const f = await fixture();
    await initializeCache(f);
    const known = randomUUID();
    const knownPath = await seedCopy(f.cache, known, { pid: 1073741823, known: true });
    const unknown = randomUUID();
    const unknownPath = await seedCopy(f.cache, unknown, { pid: 1073741823 });
    const handed = randomUUID();
    const handedPath = await seedCopy(f.cache, handed, { pid: 1073741823, handed: true });
    const next = randomUUID();
    await f.copies.prepare(next);
    expect(await readdir(f.cache)).not.toContain(copyName(known, 1073741823));
    expect(f.discard).toHaveBeenCalledWith(
      expect.objectContaining({ approvedDirectory: knownPath, copyId: known }),
    );
    expect(await readFile(join(unknownPath, `${unknown}.pdf`))).toEqual(content);
    expect(await readFile(join(handedPath, `${handed}.pdf`))).toEqual(content);
    await f.copies.remove(next);
    await f.copies.release();
  });
  it('preserves unknown files and nonempty state directories', async () => {
    const f = await fixture();
    const id = randomUUID();
    const target = await f.copies.prepare(id);
    await writeFile(join(target.directory, 'copy.json'), 'foreign marker');
    await expect(f.copies.remove(id)).rejects.toMatchObject({ code: 'file_integrity_mismatch' });
    expect(await readFile(join(target.directory, 'copy.json'), 'utf8')).toBe('foreign marker');
    expect(f.discard).not.toHaveBeenCalled();
    await f.copies.release();
    await expect(
      new MaterialCopies(f.cache, f.discard).prepare(randomUUID()),
    ).rejects.toMatchObject({ code: 'file_integrity_mismatch' });
    expect(await readFile(join(target.directory, 'copy.json'), 'utf8')).toBe('foreign marker');
  });
  it('retains known bytes if native discard becomes unavailable', async () => {
    const f = await fixture('no app');
    f.discard.mockRejectedValueOnce(new Error('worker unavailable'));
    const id = randomUUID();
    expect(await f.opener.open({ identity, operationId: id })).toMatchObject({
      ok: false,
      error: { code: 'material_open_failed' },
    });
    expect(f.cleanupFailed).toHaveBeenCalledOnce();
    expect(await readFile(join(await copyDirectory(f.cache, id), `${id}.pdf`))).toEqual(content);
    const failedDirectory = await copyDirectory(f.cache, id);
    const next = randomUUID();
    await f.copies.prepare(next);
    expect(f.discard).toHaveBeenCalledTimes(2);
    expect(await readdir(f.cache)).not.toContain(copyName(id));
    expect(f.discard).toHaveBeenLastCalledWith(
      expect.objectContaining({ approvedDirectory: failedDirectory, copyId: id }),
    );
    await f.copies.remove(next);
    await f.copies.release();
  });
  it('recovers terminal cleanup intent even if a crash left the handoff directory', async () => {
    const f = await fixture();
    await initializeCache(f);
    const id = randomUUID();
    const directory = await seedCopy(f.cache, id, { handed: true });
    await mkdir(join(directory, 'abandoned'));
    const next = randomUUID();
    await f.copies.prepare(next);
    expect(f.discard).toHaveBeenCalledOnce();
    expect(await readdir(f.cache)).not.toContain(copyName(id));
    await f.copies.remove(next);
    await f.copies.release();
  });
  it('enforces reserved bytes and the count without evicting handoffs', async () => {
    const f = await fixture();
    for (let i = 0; i < 4; i += 1) {
      await f.copies.prepare(randomUUID());
      await f.copies.release();
    }
    await expect(f.copies.prepare(randomUUID())).rejects.toMatchObject({ code: 'file_too_large' });
    const g = await fixture();
    await initializeCache(g);
    for (let i = 0; i < 64; i += 1) await seedCopy(g.cache, randomUUID(), { handed: true });
    await expect(g.copies.prepare(randomUUID())).rejects.toMatchObject({ code: 'file_too_large' });
    expect((await readdir(g.cache)).filter((name) => name.startsWith('copy-'))).toHaveLength(64);
  });
});
