import type { OpenDialogOptions, OpenDialogReturnValue } from 'electron';
import { lstat } from 'node:fs/promises';
import { extname, resolve } from 'node:path';

import type {
  CaseDocument,
  CaseDocumentAttachFileCommand,
  CaseDocumentCreateCommand,
  DesktopError,
  DesktopResult,
} from '@impeller-reliability/contracts';

import { showSystemDialog } from './system-dialog';

const MAX_CASE_DOCUMENT_BYTES = 100 * 1024 * 1024;
const CASE_DOCUMENT_EXTENSIONS = [
  'pdf',
  'docx',
  'xlsx',
  'csv',
  'json',
  'txt',
  'png',
  'jpg',
  'jpeg',
] as const;

type ShowOpenDialog = (options: OpenDialogOptions) => Promise<OpenDialogReturnValue>;

interface CaseDocumentSourceSelectionOptions {
  readonly automatedCancelled: boolean;
  readonly automatedPath: string | null;
  readonly showOpenDialog: ShowOpenDialog;
}

export async function selectCaseDocumentSource(
  options: CaseDocumentSourceSelectionOptions,
): Promise<DesktopResult<string>> {
  if (options.automatedCancelled) return cancelledResult();
  let selectedPath: string;
  if (options.automatedPath !== null) {
    selectedPath = options.automatedPath;
  } else {
    const dialogResult = await showSystemDialog(() =>
      options.showOpenDialog({
        title: 'Выбрать документ аналитического дела',
        buttonLabel: 'Прикрепить управляемую копию',
        properties: ['openFile'],
        filters: [
          {
            name: 'Документы дела',
            extensions: [...CASE_DOCUMENT_EXTENSIONS],
          },
        ],
      }),
    );
    if (!dialogResult.ok) return { ok: false, error: dialogResult.error };
    const selection: OpenDialogReturnValue = dialogResult.result;
    if (selection.canceled || selection.filePaths[0] === undefined) return cancelledResult();
    selectedPath = selection.filePaths[0];
  }

  const extension = extname(selectedPath).slice(1).toLowerCase();
  if (!CASE_DOCUMENT_EXTENSIONS.some((allowed) => allowed === extension)) {
    return failureResult('unsupported_file_type', 'Этот тип файла не поддерживается.');
  }
  try {
    const selectedStat = await lstat(selectedPath);
    if (!selectedStat.isFile() || selectedStat.isSymbolicLink()) {
      return failureResult('unsupported_file_type', 'Можно выбрать только обычный файл.');
    }
    if (selectedStat.size > MAX_CASE_DOCUMENT_BYTES) {
      return failureResult('file_too_large', 'Размер файла превышает 100 МиБ.');
    }
    if (selectedStat.size <= 0) {
      return failureResult('unsupported_file_type', 'Пустой файл не поддерживается.');
    }
  } catch {
    return failureResult('storage_error', 'Выбранный файл недоступен.');
  }
  return { ok: true, result: resolve(selectedPath) };
}

export function runCaseDocumentCreateWithFile(
  command: CaseDocumentCreateCommand,
  selectSource: () => Promise<DesktopResult<string>>,
  request: (
    payload: CaseDocumentCreateCommand & { readonly sourcePath: string },
  ) => Promise<DesktopResult<CaseDocument>>,
): Promise<DesktopResult<CaseDocument>> {
  return runWithSelectedCaseDocumentSource(selectSource, (sourcePath) =>
    request({ ...command, sourcePath }),
  );
}

export function runCaseDocumentAttachFile(
  command: CaseDocumentAttachFileCommand,
  selectSource: () => Promise<DesktopResult<string>>,
  request: (
    payload: CaseDocumentAttachFileCommand & { readonly sourcePath: string },
  ) => Promise<DesktopResult<CaseDocument>>,
): Promise<DesktopResult<CaseDocument>> {
  return runWithSelectedCaseDocumentSource(selectSource, (sourcePath) =>
    request({ ...command, sourcePath }),
  );
}

async function runWithSelectedCaseDocumentSource<TResult>(
  selectSource: () => Promise<DesktopResult<string>>,
  request: (sourcePath: string) => Promise<DesktopResult<TResult>>,
): Promise<DesktopResult<TResult>> {
  const selected = await selectSource();
  if (!selected.ok) return { ok: false, error: selected.error };
  return request(selected.result);
}

function cancelledResult<TResult>(): DesktopResult<TResult> {
  return failureResult('cancelled', 'Операция отменена пользователем.');
}

function failureResult<TResult>(
  code: DesktopError['code'],
  message: string,
): DesktopResult<TResult> {
  return { ok: false, error: { code, message, details: {}, retryable: false } };
}
