export type OptionalPositiveInteger =
  | { readonly kind: 'empty'; readonly value: null }
  | { readonly kind: 'valid'; readonly value: number }
  | { readonly kind: 'invalid' };

export function parseOptionalPositiveInteger(input: string): OptionalPositiveInteger {
  const normalized = input.trim();
  if (normalized === '') return { kind: 'empty', value: null };
  if (!/^[0-9]+$/u.test(normalized)) return { kind: 'invalid' };
  const value = Number(normalized);
  return Number.isSafeInteger(value) && value > 0 ? { kind: 'valid', value } : { kind: 'invalid' };
}

export function formatOptionalPositiveInteger(value: number | null): string {
  return value === null ? '' : String(value);
}
