import { randomUUID } from 'node:crypto';
import { describe, expect, it, vi } from 'vitest';
import type {
  MaterialCopyReleaseRequest,
  MaterialOpenCommand,
} from '@impeller-reliability/contracts';
import { MaterialCopyConsent } from './material-copy-consent';
import type { MaterialSession } from './material-open';

function fixture() {
  const command: MaterialOpenCommand = {
    operationId: randomUUID(),
    identity: {
      kind: 'protocol',
      materialId: '2',
      origin: {
        projectId: randomUUID(),
        localImportId: randomUUID(),
        packageId: 'package',
        runId: 'run',
        exportRevision: 1,
        outerPackageSha256: 'a'.repeat(64),
      },
    },
  };
  let session: MaterialSession | null = { projectId: command.identity.origin.projectId, epoch: 1 };
  const initialSession = session;
  const controller = new AbortController();
  const publish = vi.fn<(request: MaterialCopyReleaseRequest) => void>();
  const consent = new MaterialCopyConsent(() => session);
  const result = consent.request(command, initialSession, controller.signal, 17, publish);
  const request = publish.mock.calls[0]?.[0];
  if (request === undefined) throw new Error('request_missing');
  return {
    consent,
    result,
    request,
    controller,
    setSession: (next: MaterialSession | null) => {
      session = next;
    },
  };
}
describe('operation-bound material copy consent', () => {
  it('accepts exactly one answer from the requesting renderer', async () => {
    const f = fixture();
    const answer = { ...f.request, decision: 'release' as const };
    expect(f.consent.answer(answer, 99)).toBe(false);
    expect(f.consent.answer({ ...answer, requestId: randomUUID() }, 17)).toBe(false);
    expect(
      f.consent.answer({ ...answer, identity: { ...answer.identity, materialId: 'foreign' } }, 17),
    ).toBe(false);
    expect(f.consent.answer(answer, 17)).toBe(true);
    expect(await f.result).toBe(true);
    expect(f.consent.answer(answer, 17)).toBe(false);
  });
  it.each(['keep', 'abort', 'epoch', 'project', 'closed'] as const)(
    'preserves copies after %s',
    async (reason) => {
      const f = fixture();
      if (reason === 'abort') f.controller.abort();
      if (reason === 'epoch')
        f.setSession({ projectId: f.request.identity.origin.projectId, epoch: 2 });
      if (reason === 'project') f.setSession({ projectId: randomUUID(), epoch: 1 });
      if (reason === 'closed') f.setSession(null);
      expect(
        f.consent.answer({ ...f.request, decision: reason === 'keep' ? 'keep' : 'release' }, 17),
      ).toBe(reason === 'keep');
      expect(await f.result).toBe(false);
      expect(f.consent.answer({ ...f.request, decision: 'release' }, 17)).toBe(false);
    },
  );
});
