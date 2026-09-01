import { mkdir, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  RUN_PACKAGE_VALIDATION_BUDGET_MS,
  runPackageImportStart,
  runPackageValidationStart,
  selectRunPackageSource,
} from './run-package-source';

const testRoot = join(tmpdir(), 'impeller-r130run-source-test');

afterEach(async () => {
  await rm(testRoot, { recursive: true, force: true });
});

describe('run-package source selection', () => {
  it.each([new Error('native dialog failed'), 'non-error rejection'])(
    'maps a rejected system dialog to storage_error without a worker request',
    async (rejection) => {
      const request = vi.fn();
      const showOpenDialog = vi.fn().mockRejectedValue(rejection);

      const result = await runPackageValidationStart(
        { jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212' },
        () =>
          selectRunPackageSource({
            automatedCancelled: false,
            automatedPath: null,
            showOpenDialog,
          }),
        request,
      );

      expect(result).toEqual({
        ok: false,
        error: {
          code: 'storage_error',
          message: 'Системный диалог выбора файла недоступен.',
          details: {},
          retryable: false,
        },
      });
      expect(showOpenDialog).toHaveBeenCalledWith(
        expect.objectContaining({
          filters: [{ name: 'Пакет результата R130SH', extensions: ['r130run'] }],
        }),
      );
      expect(request).not.toHaveBeenCalled();
    },
  );

  it('keeps cancellation distinct and does not call worker', async () => {
    const request = vi.fn();
    const result = await runPackageValidationStart(
      { jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212' },
      () =>
        selectRunPackageSource({
          automatedCancelled: false,
          automatedPath: null,
          showOpenDialog: vi.fn().mockResolvedValue({ canceled: true, filePaths: [] }),
        }),
      request,
    );

    expect(result.ok).toBe(false);
    expect(!result.ok && result.error.code).toBe('cancelled');
    expect(request).not.toHaveBeenCalled();
  });

  it('injects approved path and fixed budget only in the internal worker request', async () => {
    await mkdir(testRoot, { recursive: true });
    const sourcePath = join(testRoot, 'candidate.r130run');
    await writeFile(sourcePath, 'PK synthetic');
    const request = vi.fn().mockResolvedValue({
      ok: false,
      error: { code: 'validation_error', message: 'fixture', details: {}, retryable: false },
    });

    await runPackageValidationStart(
      {
        jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
        replaceJobId: '31871fa4-2088-4f0d-bcb4-dd5454294edc',
      },
      () =>
        selectRunPackageSource({
          automatedCancelled: false,
          automatedPath: sourcePath,
          showOpenDialog: vi.fn(),
        }),
      request,
    );

    expect(request).toHaveBeenCalledWith({
      jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
      replaceJobId: '31871fa4-2088-4f0d-bcb4-dd5454294edc',
      sourcePath,
      validationBudgetMs: RUN_PACKAGE_VALIDATION_BUDGET_MS,
    });
  });

  it('injects an approved source path only into the internal import request', async () => {
    await mkdir(testRoot, { recursive: true });
    const sourcePath = join(testRoot, 'producer-golden.r130run');
    await writeFile(sourcePath, 'PK producer golden');
    const request = vi.fn().mockResolvedValue({
      ok: false,
      error: { code: 'validation_error', message: 'fixture', details: {}, retryable: false },
    });

    await runPackageImportStart(
      {
        jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
        replaceJobId: '31871fa4-2088-4f0d-bcb4-dd5454294edc',
        allowDiagnosticPartial: false,
      },
      () =>
        selectRunPackageSource({
          automatedCancelled: false,
          automatedPath: sourcePath,
          showOpenDialog: vi.fn(),
          buttonLabel: 'Импортировать результат',
        }),
      request,
    );

    expect(request).toHaveBeenCalledWith({
      jobId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
      replaceJobId: '31871fa4-2088-4f0d-bcb4-dd5454294edc',
      sourcePath,
      allowDiagnosticPartial: false,
    });
  });
});
