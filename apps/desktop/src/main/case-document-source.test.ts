import { describe, expect, it, vi, type Mock } from 'vitest';

import {
  runCaseDocumentAttachFile,
  runCaseDocumentCreateWithFile,
  selectCaseDocumentSource,
} from './case-document-source';

type SourceSelector = () => ReturnType<typeof selectCaseDocumentSource>;
type OperationRunner = (selectSource: SourceSelector, request: Mock) => Promise<unknown>;

const operations: readonly { readonly name: string; readonly run: OperationRunner }[] = [
  {
    name: 'createWithFile',
    run: (selectSource, request) =>
      runCaseDocumentCreateWithFile(
        {
          caseDocumentId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
          document: {
            documentKind: 'standard',
            title: 'ГОСТ',
            designation: '',
            revisionLabel: '',
            documentDate: null,
            issuer: '',
            notes: '',
          },
          wheelModelIds: [],
          specimenIds: [],
        },
        selectSource,
        request,
      ),
  },
  {
    name: 'attachFile',
    run: (selectSource, request) =>
      runCaseDocumentAttachFile(
        {
          caseDocumentId: 'ec7cc676-e40d-4ad7-b038-83e0035dc212',
          expectedRevision: 1,
        },
        selectSource,
        request,
      ),
  },
];

describe('case document source selection', () => {
  for (const operation of operations) {
    it.each([new Error('native dialog failed'), 'non-error rejection'])(
      `${operation.name} maps a rejected system dialog to storage_error without a worker request`,
      async (rejection) => {
        const showOpenDialog = vi.fn().mockRejectedValue(rejection);
        const request = vi.fn();

        const result = await operation.run(
          () =>
            selectCaseDocumentSource({
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
        expect(showOpenDialog).toHaveBeenCalledOnce();
        expect(showOpenDialog).toHaveBeenCalledWith(
          expect.objectContaining({
            properties: ['openFile'],
            filters: [
              expect.objectContaining({
                extensions: ['pdf', 'docx', 'xlsx', 'csv', 'json', 'txt', 'png', 'jpg', 'jpeg'],
              }),
            ],
          }),
        );
        expect(request).not.toHaveBeenCalled();
      },
    );

    it(`${operation.name} keeps a resolved user cancellation distinct from a dialog failure`, async () => {
      const request = vi.fn();

      const result = await operation.run(
        () =>
          selectCaseDocumentSource({
            automatedCancelled: false,
            automatedPath: null,
            showOpenDialog: vi.fn().mockResolvedValue({ canceled: true, filePaths: [] }),
          }),
        request,
      );

      expect(result).toEqual({
        ok: false,
        error: {
          code: 'cancelled',
          message: 'Операция отменена пользователем.',
          details: {},
          retryable: false,
        },
      });
      expect(request).not.toHaveBeenCalled();
    });
  }
});
