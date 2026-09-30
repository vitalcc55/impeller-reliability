import { createHash, randomUUID } from 'node:crypto';
import { mkdir, lstat, realpath, readdir, rmdir, open } from 'node:fs/promises';
import { join, resolve, dirname, basename, relative } from 'node:path';
import type {
  DesktopError,
  DesktopResult,
  MaterialCopyResult,
  MaterialCopyFileIdentity,
  MaterialCopyDiscardCommand,
  MaterialIdentity,
  MaterialOpenCommand,
  MaterialOpenedResult,
} from '@impeller-reliability/contracts';

const BYTE_LIMIT = 400 * 1024 * 1024;
const COPY_LIMIT = 64;
const FILE_LIMIT = 100 * 1024 * 1024;
const COPY_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const extensions = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'application/pdf': '.pdf',
} as const;

const CLAIM_NAME = /^lease-([1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})-([0-9a-f-]{36})$/;
function claimOwners(name: string): { ownerPid: number; workerPid: number | null } | null {
  const match = CLAIM_NAME.exec(name);
  if (match === null || !COPY_ID.test(match[3] ?? '')) return null;
  return { ownerPid: Number(match[1]), workerPid: Number(match[2]) || null };
}
function alive(pid: number | null): boolean {
  if (pid === null) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return !(error instanceof Error && 'code' in error && error.code === 'ESRCH');
  }
}

class CopyError extends Error {
  constructor(
    readonly code: DesktopError['code'],
    message: string,
  ) {
    super(message);
  }
}
const failure = <T>(code: DesktopError['code'], message: string): DesktopResult<T> => ({
  ok: false,
  error: { code, message, details: {}, retryable: false },
});

async function ordinaryDirectory(path: string): Promise<void> {
  const value = await lstat(path);
  if (
    !value.isDirectory() ||
    value.isSymbolicLink() ||
    relative(await realpath(path), resolve(path)) !== ''
  ) {
    throw new CopyError('file_integrity_mismatch', 'Каталог временных материалов был подменён.');
  }
}
async function ordinaryFile(path: string): Promise<void> {
  const value = await lstat(path);
  if (
    !value.isFile() ||
    value.isSymbolicLink() ||
    value.nlink !== 1 ||
    relative(await realpath(path), resolve(path)) !== ''
  ) {
    throw new CopyError(
      'file_integrity_mismatch',
      'Временная копия не является отдельным обычным файлом.',
    );
  }
}
function sameIdentity(left: MaterialIdentity, right: MaterialIdentity): boolean {
  return (
    left.kind === right.kind &&
    left.materialId === right.materialId &&
    left.origin.projectId === right.origin.projectId &&
    left.origin.localImportId === right.origin.localImportId &&
    left.origin.packageId === right.origin.packageId &&
    left.origin.runId === right.origin.runId &&
    left.origin.exportRevision === right.origin.exportRevision &&
    left.origin.outerPackageSha256 === right.origin.outerPackageSha256
  );
}

interface CopyRecord {
  readonly id: string;
  readonly name: string;
  readonly ownerPid: number;
  readonly workerPid: number | null;
  readonly byteLimit: number;
}
interface CopyState {
  readonly file: string | null;
  readonly bytes: number;
  readonly identity: MaterialCopyFileIdentity | null;
  readonly mediaType: MaterialCopyResult['mediaType'] | null;
  readonly identityName: string | null;
  readonly handed: boolean;
  readonly abandoned: boolean;
}
const RECORD_NAME =
  /^copy-([1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})-([1-9][0-9]{0,8})-([0-9a-f-]{36})$/;
const IDENTITY_NAME = /^identity-([0-9a-f]{32})-([0-9a-f]{16})-(jpg|png|pdf)$/;
function recordFromName(name: string): CopyRecord {
  const parts = RECORD_NAME.exec(name);
  const id = parts?.[4];
  const byteLimit = Number(parts?.[3]);
  if (id === undefined || !COPY_ID.test(id) || byteLimit < 1 || byteLimit > FILE_LIMIT)
    throw new CopyError(
      'file_integrity_mismatch',
      'Неизвестный объект временного каталога сохраняется.',
    );
  return {
    id,
    name,
    ownerPid: Number(parts?.[1]),
    workerPid: Number(parts?.[2]) || null,
    byteLimit,
  };
}
function mediaFromSuffix(suffix: string): MaterialCopyResult['mediaType'] {
  if (suffix === 'jpg') return 'image/jpeg';
  if (suffix === 'png') return 'image/png';
  if (suffix === 'pdf') return 'application/pdf';
  throw new CopyError('file_integrity_mismatch', 'Неизвестный формат временного материала.');
}

/** Main owns lifecycle; immutable empty state directories contain no primary facts. */
export class MaterialCopies {
  private initialized = false;
  private lease: string | null = null;
  private readonly operations = new Map<string, CopyRecord>();
  constructor(
    private root: string,
    private readonly discard: (command: MaterialCopyDiscardCommand) => Promise<void>,
    private readonly workerPid: () => number | null = () => null,
  ) {}
  private directory(record: CopyRecord): string {
    return join(this.root, record.name);
  }
  private current(copyId: string): CopyRecord {
    const record = this.operations.get(copyId);
    if (record === undefined)
      throw new CopyError('validation_error', 'Операция копирования неизвестна.');
    return record;
  }
  private async initialize(): Promise<void> {
    if (this.initialized) return;
    if (resolve(this.root).startsWith('\\\\'))
      throw new CopyError(
        'file_integrity_mismatch',
        'Временные копии требуют локального каталога.',
      );
    const parent = await lstat(dirname(this.root));
    if (!parent.isDirectory() || parent.isSymbolicLink())
      throw new CopyError('file_integrity_mismatch', 'Родительский каталог копий подменён.');
    this.root = join(await realpath(dirname(this.root)), basename(this.root));
    await ordinaryDirectory(dirname(this.root));
    await mkdir(this.root, { recursive: true });
    await ordinaryDirectory(this.root);
    const owner = join(this.root, 'owner');
    try {
      await mkdir(owner);
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error;
    }
    await ordinaryDirectory(owner);
    if ((await readdir(owner)).length !== 0)
      throw new CopyError('file_integrity_mismatch', 'Каталог принадлежит другому владельцу.');
    this.initialized = true;
  }
  private async acquire(): Promise<void> {
    if (this.lease !== null)
      throw new CopyError('operation_in_progress', 'Временные копии уже используются.');
    // Publish a unique empty claim directory before scanning live contenders.
    const name = `lease-${process.pid}-${this.workerPid() ?? 0}-${randomUUID()}`;
    const path = join(this.root, name);
    await mkdir(path);
    this.lease = path;
    try {
      for (const entry of await readdir(this.root)) {
        const owners = claimOwners(entry);
        if (owners === null || entry === name) continue;
        if (alive(owners.ownerPid) || alive(owners.workerPid))
          throw new CopyError(
            'operation_in_progress',
            'Другой экземпляр использует временные копии.',
          );
      }
    } catch (error) {
      await this.release();
      throw error;
    }
  }
  async release(): Promise<void> {
    const lease = this.lease;
    this.lease = null;
    if (lease === null) return;
    await ordinaryDirectory(lease);
    await rmdir(lease); // Never unlink a substituted file or nonempty directory.
  }
  private async state(record: CopyRecord): Promise<CopyState> {
    const directory = this.directory(record);
    await ordinaryDirectory(directory);
    let file: string | null = null;
    let bytes = 0;
    let identity: MaterialCopyFileIdentity | null = null;
    let mediaType: MaterialCopyResult['mediaType'] | null = null;
    let identityName: string | null = null;
    let handed = false;
    let abandoned = false;
    for (const entry of await readdir(directory)) {
      const path = join(directory, entry);
      const identityParts = IDENTITY_NAME.exec(entry);
      if (entry === 'handoff' || entry === 'abandoned' || identityParts !== null) {
        await ordinaryDirectory(path);
        if ((await readdir(path)).length !== 0)
          throw new CopyError(
            'file_integrity_mismatch',
            'Чужое содержимое каталога состояния сохраняется.',
          );
        if (entry === 'handoff') handed = true;
        else if (entry === 'abandoned') abandoned = true;
        else {
          if (
            identity !== null ||
            identityParts?.[1] === undefined ||
            identityParts[2] === undefined ||
            identityParts[3] === undefined
          )
            throw new CopyError('file_integrity_mismatch', 'Идентичность копии неоднозначна.');
          identity = { fileId: identityParts[1], volumeId: identityParts[2] };
          mediaType = mediaFromSuffix(identityParts[3]);
          identityName = entry;
        }
        continue;
      }
      if (
        !Object.values(extensions).some((suffix) => entry === `${record.id}${suffix}`) ||
        file !== null
      )
        throw new CopyError(
          'file_integrity_mismatch',
          'Неизвестные файлы временной операции сохраняются.',
        );
      await ordinaryFile(path);
      file = entry;
      bytes = (await lstat(path)).size;
    }
    if (file !== null && mediaType !== null && file !== `${record.id}${extensions[mediaType]}`)
      throw new CopyError(
        'file_integrity_mismatch',
        'Файл не соответствует сохранённой идентичности.',
      );
    return { file, bytes, identity, mediaType, identityName, handed, abandoned };
  }
  private async removeRecord(record: CopyRecord, state: CopyState): Promise<void> {
    const directory = this.directory(record);
    // Only a cancelled/failed operation enters removal. Persist that terminal
    // state before native disposal so a worker outage cannot retain a handoff.
    if (!state.abandoned) await mkdir(join(directory, 'abandoned'));
    if (state.handed) {
      await ordinaryDirectory(join(directory, 'handoff'));
      await rmdir(join(directory, 'handoff'));
    }
    if (state.file !== null) {
      if (state.identity === null || state.mediaType === null) {
        throw new CopyError(
          'file_integrity_mismatch',
          'Копия без подтверждённой файловой идентичности сохранена.',
        );
      }
      await this.discard({
        approvedDirectory: directory,
        copyId: record.id,
        mediaType: state.mediaType,
        fileIdentity: state.identity,
      });
    }
    // Only empty directories are removed in Main; byte deletion is by native identity.
    for (const entry of [state.identityName, 'abandoned']) {
      if (entry !== null) {
        await ordinaryDirectory(join(directory, entry));
        await rmdir(join(directory, entry));
      }
    }
    await ordinaryDirectory(directory);
    await rmdir(directory);
    this.operations.delete(record.id);
  }
  async prepare(
    copyId: string,
  ): Promise<{ readonly directory: string; readonly byteLimit: number }> {
    if (!COPY_ID.test(copyId))
      throw new CopyError('validation_error', 'Идентификатор операции недопустим.');
    await this.initialize();
    await this.acquire();
    try {
      let bytes = 0;
      let count = 0;
      for (const name of await readdir(this.root)) {
        if (name === 'owner' || join(this.root, name) === this.lease) continue;
        const owners = claimOwners(name);
        if (owners !== null) {
          if (!alive(owners.ownerPid) && !alive(owners.workerPid)) {
            await ordinaryDirectory(join(this.root, name));
            await rmdir(join(this.root, name));
          }
          continue;
        }
        const record = recordFromName(name);
        const state = await this.state(record);
        const live = alive(record.ownerPid) || alive(record.workerPid);
        if (
          (!state.handed || state.abandoned) &&
          (!live || state.abandoned) &&
          (state.file === null || state.identity !== null)
        ) {
          await this.removeRecord(record, state);
          continue;
        }
        count += 1;
        bytes +=
          state.handed || state.abandoned ? state.bytes : Math.max(state.bytes, record.byteLimit);
      }
      if (count >= COPY_LIMIT || bytes >= BYTE_LIMIT)
        throw new CopyError(
          'file_too_large',
          'Достигнут предел временных копий. Переданные или неоднозначные файлы не вытесняются автоматически.',
        );
      const byteLimit = Math.min(FILE_LIMIT, BYTE_LIMIT - bytes);
      const workerPid = this.workerPid();
      const name = `copy-${process.pid}-${workerPid ?? 0}-${byteLimit}-${copyId}`;
      const record = { id: copyId, name, ownerPid: process.pid, workerPid, byteLimit };
      await mkdir(this.directory(record));
      this.operations.set(copyId, record);
      return { directory: this.directory(record), byteLimit };
    } catch (error) {
      await this.release();
      throw error;
    }
  }
  async verify(copyId: string, result: MaterialCopyResult): Promise<void> {
    await ordinaryDirectory(this.root);
    const record = this.current(copyId);
    if (result.sizeBytes > record.byteLimit)
      throw new CopyError('file_too_large', 'Копия превышает выделенный резерв.');
    const directory = this.directory(record);
    const expected = join(directory, `${copyId}${extensions[result.mediaType]}`);
    if (
      resolve(result.absolutePath) !== expected ||
      basename(result.absolutePath) !== basename(expected)
    )
      throw new CopyError('file_integrity_mismatch', 'Путь копии вне разрешённой операции.');
    const state = await this.state(record);
    if (
      state.identity !== null &&
      (state.identity.fileId !== result.fileIdentity.fileId ||
        state.identity.volumeId !== result.fileIdentity.volumeId ||
        state.mediaType !== result.mediaType)
    )
      throw new CopyError('file_integrity_mismatch', 'Файловая идентичность копии изменилась.');
    if (state.identity === null) {
      const suffix = extensions[result.mediaType].slice(1);
      await mkdir(
        join(
          directory,
          `identity-${result.fileIdentity.fileId}-${result.fileIdentity.volumeId}-${suffix}`,
        ),
      );
      await this.state(record);
    }
    await ordinaryFile(expected);
    const before = await lstat(expected, { bigint: true });
    if (before.size !== BigInt(result.sizeBytes))
      throw new CopyError('file_integrity_mismatch', 'Размер копии изменился.');
    const descriptor = await open(expected, 'r');
    try {
      const opened = await descriptor.stat({ bigint: true });
      if (
        !opened.isFile() ||
        opened.nlink !== 1n ||
        opened.ino !== before.ino ||
        opened.dev !== before.dev
      )
        throw new CopyError('file_integrity_mismatch', 'Копия подменена при чтении.');
      const digest = createHash('sha256');
      const buffer = Buffer.alloc(1024 * 1024);
      let size = 0;
      for (;;) {
        const chunk = await descriptor.read(buffer, 0, buffer.length, null);
        if (chunk.bytesRead === 0) break;
        size += chunk.bytesRead;
        if (size > result.sizeBytes || size > record.byteLimit)
          throw new CopyError('file_integrity_mismatch', 'Копия изменилась во время проверки.');
        digest.update(buffer.subarray(0, chunk.bytesRead));
      }
      const after = await descriptor.stat({ bigint: true });
      await ordinaryFile(expected);
      const pathAfter = await lstat(expected, { bigint: true });
      if (
        size !== result.sizeBytes ||
        digest.digest('hex') !== result.sha256 ||
        after.size !== before.size ||
        after.mtimeNs !== before.mtimeNs ||
        pathAfter.ino !== before.ino ||
        pathAfter.dev !== before.dev ||
        pathAfter.mtimeNs !== before.mtimeNs
      )
        throw new CopyError('file_integrity_mismatch', 'Байты копии не соответствуют материалу.');
    } finally {
      await descriptor.close();
    }
  }
  async handoff(copyId: string): Promise<void> {
    const record = this.current(copyId);
    const state = await this.state(record);
    if (state.identity === null || state.file === null)
      throw new CopyError('file_integrity_mismatch', 'Передача непроверенной копии запрещена.');
    await mkdir(join(this.directory(record), 'handoff'));
  }
  async remove(copyId: string): Promise<void> {
    const record = this.current(copyId);
    await this.removeRecord(record, await this.state(record));
  }
}

export interface MaterialSession {
  readonly projectId: string;
  readonly epoch: number;
}
export interface MaterialOpenDependencies {
  readonly copies: MaterialCopies;
  readonly session: () => MaterialSession | null;
  readonly resolve: (
    identity: MaterialIdentity,
    directory: string,
    copyId: string,
    byteLimit: number,
  ) => Promise<DesktopResult<MaterialCopyResult>>;
  readonly openPath: (path: string) => Promise<string>;
  readonly cleanupFailed: (error: unknown) => void;
}
export class MaterialOpener {
  private readonly drainWaiters = new Set<() => void>();
  drain(): Promise<void> {
    return this.pending === null
      ? Promise.resolve()
      : new Promise<void>((resolve) => this.drainWaiters.add(resolve));
  }
  private pending: { readonly id: string; cancelled: boolean; shellInvoked: boolean } | null = null;
  constructor(private readonly dependencies: MaterialOpenDependencies) {}
  cancel(operationId: string): boolean {
    if (this.pending?.id !== operationId || this.pending.shellInvoked) return false;
    this.pending.cancelled = true;
    return true;
  }
  invalidate(): void {
    if (this.pending !== null && !this.pending.shellInvoked) this.pending.cancelled = true;
  }
  async open(command: MaterialOpenCommand): Promise<DesktopResult<MaterialOpenedResult>> {
    if (this.pending !== null)
      return failure('operation_in_progress', 'Открытие другого материала ещё выполняется.');
    const session = this.dependencies.session();
    if (session === null || session.projectId !== command.identity.origin.projectId)
      return failure('cancelled', 'Материал не относится к активной сессии дела.');
    const token = { id: command.operationId, cancelled: false, shellInvoked: false };
    this.pending = token;
    let prepared = false;
    let knownShellFailure = false;
    const current = () => {
      const now = this.dependencies.session();
      return (
        !token.cancelled &&
        now !== null &&
        now.projectId === session.projectId &&
        now.epoch === session.epoch
      );
    };
    try {
      const target = await this.dependencies.copies.prepare(command.operationId);
      prepared = true;
      if (!current()) return failure('cancelled', 'Открытие материала отменено при смене сессии.');
      const resolved = await this.dependencies.resolve(
        command.identity,
        target.directory,
        command.operationId,
        target.byteLimit,
      );
      if (!resolved.ok) return resolved;
      if (!sameIdentity(resolved.result.identity, command.identity))
        throw new CopyError('file_integrity_mismatch', 'Worker вернул материал другой редакции.');
      await this.dependencies.copies.verify(command.operationId, resolved.result);
      if (!current()) return failure('cancelled', 'Открытие материала отменено при смене сессии.');
      await this.dependencies.copies.handoff(command.operationId);
      await this.dependencies.copies.verify(command.operationId, resolved.result);
      if (!current())
        return failure('cancelled', 'Открытие материала отменено до передачи системной программе.');
      token.shellInvoked = true;
      const shellError = await this.dependencies.openPath(resolved.result.absolutePath);
      if (shellError !== '') {
        knownShellFailure = true;
        return failure(
          'material_open_failed',
          'Системная программа не смогла открыть первичный материал.',
        );
      }
      return {
        ok: true,
        result: {
          identity: command.identity,
          opened: true,
          sha256: resolved.result.sha256,
          sizeBytes: resolved.result.sizeBytes,
        },
      };
    } catch (error) {
      if (error instanceof CopyError) return failure(error.code, error.message);
      return failure(
        token.shellInvoked ? 'material_open_failed' : 'storage_error',
        token.shellInvoked
          ? 'Запрос системного открытия завершился ошибкой.'
          : 'Не удалось подготовить проверенную временную копию.',
      );
    } finally {
      if (prepared && (!token.shellInvoked || knownShellFailure)) {
        try {
          await this.dependencies.copies.remove(command.operationId);
        } catch (error) {
          this.dependencies.cleanupFailed(error);
        }
      }
      try {
        await this.dependencies.copies.release();
      } catch (error) {
        this.dependencies.cleanupFailed(error);
      }
      if (this.pending === token) this.pending = null;
      for (const done of this.drainWaiters) done();
      this.drainWaiters.clear();
    }
  }
}
