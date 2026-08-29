import { describe, expect, it, vi } from 'vitest';

import { showSystemDialog } from './system-dialog';

describe('system dialog boundary', () => {
  it.each([new Error('dialog failed'), 'non-error rejection'])(
    'normalizes a rejected Electron dialog without exposing details',
    async (rejection) => {
      const rejectedDialog = vi.fn().mockRejectedValue(rejection);
      await expect(showSystemDialog(rejectedDialog)).resolves.toEqual({
        ok: false,
        error: {
          code: 'storage_error',
          message: 'Системный диалог выбора файла недоступен.',
          details: {},
          retryable: false,
        },
      });
    },
  );

  it('preserves a resolved dialog result for operation-specific cancellation handling', async () => {
    await expect(showSystemDialog(() => Promise.resolve({ canceled: true }))).resolves.toEqual({
      ok: true,
      result: { canceled: true },
    });
  });
});
