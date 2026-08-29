import type { DesktopResult } from '@impeller-reliability/contracts';

export async function showSystemDialog<TResult>(
  show: () => Promise<TResult>,
): Promise<DesktopResult<TResult>> {
  try {
    return { ok: true, result: await show() };
  } catch {
    return {
      ok: false,
      error: {
        code: 'storage_error',
        message: 'Системный диалог выбора файла недоступен.',
        details: {},
        retryable: false,
      },
    };
  }
}
