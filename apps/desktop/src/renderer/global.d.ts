import type { ImpellerApi } from '@impeller-reliability/contracts';

declare global {
  interface Window {
    readonly impeller?: ImpellerApi;
  }
}

export {};
