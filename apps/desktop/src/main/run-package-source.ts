import type { OpenDialogOptions, OpenDialogReturnValue } from 'electron';
import { lstat } from 'node:fs/promises';
import { extname, resolve } from 'node:path';

import type {
  DesktopError,
  DesktopResult,
  RunPackageValidationJob,
  RunPackageValidationStartCommand,
  RunPackageImportJob,
  RunPackageImportStartCommand,
} from '@impeller-reliability/contracts';

import { showSystemDialog } from './system-dialog';

export const RUN_PACKAGE_VALIDATION_BUDGET_MS = 1_800_000;
const MAX_RUN_PACKAGE_BYTES = 8 * 1024 * 1024 * 1024;
type ShowOpenDialog = (options: OpenDialogOptions) => Promise<OpenDialogReturnValue>;

interface RunPackageSourceSelectionOptions {
  readonly automatedCancelled: boolean;
  readonly automatedPath: string | null;
  readonly showOpenDialog: ShowOpenDialog;
  readonly buttonLabel?: string;
}

export async function selectRunPackageSource(
  options: RunPackageSourceSelectionOptions,
): Promise<DesktopResult<string>> {
  if (options.automatedCancelled) return cancelledResult();
  let selectedPath: string;
  if (options.automatedPath !== null) {
    selectedPath = options.automatedPath;
  } else {
    const dialogResult = await showSystemDialog(() =>
      options.showOpenDialog({
        title: 'Выбрать пакет результата R130SH',
        buttonLabel: options.buttonLabel ?? 'Проверить пакет',
        properties: ['openFile'],
        filters: [{ name: 'Пакет результата R130SH', extensions: ['r130run'] }],
      }),
    );
    if (!dialogResult.ok) return { ok: false, error: dialogResult.error };
    const selection = dialogResult.result;
    if (selection.canceled || selection.filePaths[0] === undefined) return cancelledResult();
    selectedPath = selection.filePaths[0];
  }
  if (extname(selectedPath).toLowerCase() !== '.r130run') {
    return failureResult('unsupported_file_type', 'Можно выбрать только пакет *.r130run.');
  }
  try {
    const selectedStat = await lstat(selectedPath);
    if (!selectedStat.isFile() || selectedStat.isSymbolicLink()) {
      return failureResult('unsupported_file_type', 'Можно выбрать только обычный файл.');
    }
    if (selectedStat.size <= 0 || selectedStat.size > MAX_RUN_PACKAGE_BYTES) {
      return failureResult(
        'file_too_large',
        'Размер пакета находится вне технического предела проверки.',
      );
    }
  } catch {
    return failureResult('storage_error', 'Выбранный пакет недоступен.');
  }
  return { ok: true, result: resolve(selectedPath) };
}

export async function runPackageValidationStart(
  command: RunPackageValidationStartCommand,
  selectSource: () => Promise<DesktopResult<string>>,
  request: (payload: {
    readonly jobId: string;
    readonly replaceJobId?: string;
    readonly sourcePath: string;
    readonly validationBudgetMs: number;
  }) => Promise<DesktopResult<RunPackageValidationJob>>,
): Promise<DesktopResult<RunPackageValidationJob>> {
  const selected = await selectSource();
  if (!selected.ok) return { ok: false, error: selected.error };
  return request({
    jobId: command.jobId,
    ...(command.replaceJobId === undefined ? {} : { replaceJobId: command.replaceJobId }),
    sourcePath: selected.result,
    validationBudgetMs: RUN_PACKAGE_VALIDATION_BUDGET_MS,
  });
}

export async function runPackageImportStart(
  command: RunPackageImportStartCommand,
  selectSource: () => Promise<DesktopResult<string>>,
  request: (payload: {
    readonly jobId: string;
    readonly replaceJobId?: string;
    readonly sourcePath: string;
    readonly allowDiagnosticPartial: boolean;
  }) => Promise<DesktopResult<RunPackageImportJob>>,
): Promise<DesktopResult<RunPackageImportJob>> {
  const selected = await selectSource();
  if (!selected.ok) return { ok: false, error: selected.error };
  return request({
    jobId: command.jobId,
    ...(command.replaceJobId === undefined ? {} : { replaceJobId: command.replaceJobId }),
    sourcePath: selected.result,
    allowDiagnosticPartial: command.allowDiagnosticPartial,
  });
}

function cancelledResult<TResult>(): DesktopResult<TResult> {
  return failureResult('cancelled', 'Выбор пакета отменён пользователем.');
}

function failureResult<TResult>(
  code: DesktopError['code'],
  message: string,
): DesktopResult<TResult> {
  return { ok: false, error: { code, message, details: {}, retryable: false } };
}
