import { randomUUID } from 'node:crypto';
import type {
  MaterialCopyReleaseDecision,
  MaterialCopyReleaseRequest,
  MaterialOpenCommand,
} from '@impeller-reliability/contracts';
import { sameMaterialIdentity, type MaterialSession } from './material-open';

/** One transient Renderer decision belongs to one Main operation/session/sender. */
export class MaterialCopyConsent {
  private pending: {
    readonly request: MaterialCopyReleaseRequest;
    readonly senderId: number;
    readonly session: MaterialSession;
    readonly signal: AbortSignal;
    readonly finish: (release: boolean) => void;
  } | null = null;
  constructor(private readonly session: () => MaterialSession | null) {}
  request(
    command: MaterialOpenCommand,
    session: MaterialSession,
    signal: AbortSignal,
    senderId: number,
    publish: (request: MaterialCopyReleaseRequest) => void,
  ): Promise<boolean> {
    if (signal.aborted || this.pending !== null) return Promise.resolve(false);
    return new Promise<boolean>((resolve, reject) => {
      const request = {
        requestId: randomUUID(),
        operationId: command.operationId,
        identity: command.identity,
      };
      const finish = (release: boolean) => {
        signal.removeEventListener('abort', abort);
        this.pending = null;
        resolve(release);
      };
      const abort = () => finish(false);
      this.pending = { request, session, signal, senderId, finish };
      signal.addEventListener('abort', abort, { once: true });
      try {
        publish(request);
      } catch (error) {
        signal.removeEventListener('abort', abort);
        this.pending = null;
        reject(error instanceof Error ? error : new Error('material_copy_consent_delivery_failed'));
      }
    });
  }
  answer(command: MaterialCopyReleaseDecision, senderId: number): boolean {
    const pending = this.pending;
    if (
      pending === null ||
      senderId !== pending.senderId ||
      command.requestId !== pending.request.requestId ||
      command.operationId !== pending.request.operationId ||
      !sameMaterialIdentity(command.identity, pending.request.identity)
    )
      return false;
    const current = this.session();
    const eligible =
      !pending.signal.aborted &&
      current !== null &&
      current.projectId === pending.session.projectId &&
      current.epoch === pending.session.epoch;
    pending.finish(eligible && command.decision === 'release');
    return eligible;
  }
}
