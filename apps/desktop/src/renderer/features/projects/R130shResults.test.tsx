import { MantineProvider } from '@mantine/core';
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  CustomerProfile,
  DesktopResult,
  InspectionMaterialPage,
} from '@impeller-reliability/contracts';
import { createPreviewApi } from '../../preview-api';
import { R130shResults, type R130shResultsHandle } from './R130shResults';

const mounted: { root: Root; element: HTMLDivElement }[] = [];
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
});
afterEach(async () => {
  for (const { root, element } of mounted.splice(0)) {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    element.remove();
  }
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function timers() {
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
}
async function fixture(beforeMount?: (api: ReturnType<typeof createPreviewApi>) => void) {
  const api = createPreviewApi('ready');
  const opened = await api.project.open();
  if (!opened.ok) throw new Error('project_missing');
  beforeMount?.(api);
  const element = document.createElement('div');
  document.body.append(element);
  const root = createRoot(element);
  mounted.push({ root, element });
  const ref = createRef<R130shResultsHandle>();
  const dirty = vi.fn();
  const pending = vi.fn();
  const transition = vi.fn((hasDirty: boolean, action: () => void) => {
    if (!hasDirty) action();
  });
  await act(async () => {
    root.render(
      <MantineProvider>
        <R130shResults
          ref={ref}
          desktopApi={api}
          project={opened.result}
          disabled={false}
          onDirtyChange={dirty}
          onPendingChange={pending}
          requestTransition={transition}
        />
      </MantineProvider>,
    );
    await Promise.resolve();
  });
  await timers();
  await timers();
  async function click(text: string) {
    const button = [...element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === text,
    );
    if (button === undefined) throw new Error(`button_missing:${text}`);
    await act(async () => {
      button.click();
      await Promise.resolve();
    });
  }
  return { api, element, ref, dirty, pending, transition, click };
}
describe('materials in the existing results lifecycle', () => {
  it('reattaches an uncertain binding without a competing selection effect', async () => {
    const f = await fixture();
    const reason = f.element.querySelector('.binding-editor textarea');
    if (!(reason instanceof HTMLTextAreaElement)) throw new Error('reason_missing');
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
    if (descriptor?.set === undefined) throw new Error('setter_missing');
    const setter = descriptor.set.bind(reason);
    await act(async () => {
      setter('Проверка неопределённого сохранения');
      reason.dispatchEvent(new Event('input', { bubbles: true }));
      await Promise.resolve();
    });
    vi.spyOn(f.api.importedRun, 'bindSpecimen').mockRejectedValueOnce(new Error('transport_lost'));
    await f.click('Сохранить привязку');
    const original = f.api.importedRun.get.bind(f.api.importedRun);
    let release: () => void = () => {};
    const held = new Promise<void>((done) => {
      release = done;
    });
    const get = vi.spyOn(f.api.importedRun, 'get').mockImplementationOnce(async (id) => {
      await held;
      return original(id);
    });
    let restored: Promise<boolean> = Promise.resolve(false);
    await act(async () => {
      const handle = f.ref.current;
      if (handle === null) throw new Error('results_handle_missing');
      restored = handle.verifyAfterReattach().then(
        () => true,
        () => false,
      );
      await Promise.resolve();
    });
    await timers();
    await act(async () => {
      release();
      await held;
    });
    await act(async () => {
      expect(await restored).toBe(true);
    });
    expect(get).toHaveBeenCalledOnce();
    expect(f.element.querySelector('.binding-editor textarea')).toHaveProperty('value', '');
    expect(f.element.textContent).toContain('Сохранённое изменение восстановлено');
  });
  it('does not show a rejected old detail request after a newer selection', async () => {
    const f = await fixture();
    let reject: (reason: Error) => void = () => {};
    const held = new Promise<Awaited<ReturnType<typeof f.api.importedRun.get>>>((_done, fail) => {
      reject = fail;
    });
    vi.spyOn(f.api.importedRun, 'get').mockReturnValueOnce(held);
    const second = [...f.element.querySelectorAll('button')].find(
      (b) => b.textContent?.includes('synthetic-diagnostic-materials') && b.closest('li') !== null,
    );
    const first = [...f.element.querySelectorAll('button')].find(
      (b) => b.textContent?.includes('normal_final_rbd') && b.closest('li') !== null,
    );
    if (first === undefined || second === undefined) throw new Error('source_buttons_missing');
    await act(async () => {
      second.click();
      await Promise.resolve();
    });
    await timers();
    await act(async () => {
      first.click();
      await Promise.resolve();
    });
    await timers();
    await act(async () => {
      reject(new Error('old_transport_failure'));
      await Promise.resolve();
    });
    expect(f.element.querySelector('.r130sh-detail-header')?.textContent).toContain(
      'normal_final_rbd',
    );
    expect(f.element.querySelector('[role="alert"]')).toBeNull();
  });
  it('does not add a material read alongside the two-request detail hydration', async () => {
    let release: (value: DesktopResult<CustomerProfile | null>) => void = () => {};
    const held = new Promise<DesktopResult<CustomerProfile | null>>((done) => {
      release = done;
    });
    const f = await fixture((api) => {
      vi.spyOn(api.caseCustomer, 'get').mockReturnValueOnce(held);
    });
    const materials = vi.spyOn(f.api.importedRun, 'listInspectionPage');
    const button = [...f.element.querySelectorAll('button')].find(
      (b) => b.textContent === 'Проверить и показать материалы',
    );
    expect(button?.disabled).toBe(true);
    await f.click('Проверить и показать материалы');
    expect(materials).not.toHaveBeenCalled();
    await act(async () => {
      release({ ok: true, result: null });
      await held;
    });
    expect(button?.disabled).toBe(false);
    await f.click('Проверить и показать материалы');
    expect(materials).toHaveBeenCalledOnce();
  });
  it('drains an old material request before reading a newly selected import', async () => {
    const f = await fixture();
    const imports = await f.api.importedRun.list();
    const project = await f.api.project.getOverview();
    if (!imports.ok || !project.ok || imports.result[0] === undefined)
      throw new Error('source_missing');
    const source = imports.result[0];
    const oldPage = await f.api.importedRun.listInspectionPage({
      origin: {
        projectId: project.result.projectId,
        localImportId: source.localImportId,
        packageId: source.packageId,
        runId: source.runId,
        exportRevision: source.exportRevision,
        outerPackageSha256: source.outerPackageSha256,
      },
    });
    let release: (value: DesktopResult<InspectionMaterialPage>) => void = () => {};
    const held = new Promise<DesktopResult<InspectionMaterialPage>>((done) => {
      release = done;
    });
    vi.spyOn(f.api.importedRun, 'listInspectionPage').mockReturnValueOnce(held);
    const detail = vi.spyOn(f.api.importedRun, 'get');
    const photos = vi.spyOn(f.api.importedRun, 'listPhotoPage');
    await f.click('Проверить и показать материалы');
    const second = [...f.element.querySelectorAll('button')].find(
      (button) =>
        button.textContent?.includes('synthetic-diagnostic-materials') &&
        button.closest('li') !== null,
    );
    if (second === undefined) throw new Error('second_source_missing');
    await act(async () => {
      second.click();
      await Promise.resolve();
    });
    await timers();
    expect(detail).not.toHaveBeenCalled();
    await act(async () => {
      release(oldPage);
      await held;
    });
    expect(detail).toHaveBeenCalledOnce();
    expect(photos).not.toHaveBeenCalled();
    expect(f.element.querySelector('.r130sh-detail-header')?.textContent).toContain(
      'synthetic-diagnostic-materials',
    );
    expect(f.element.textContent).not.toContain('Синтетический протокол UI');
  });
  it('preserves an unsaved bind draft through material reads and reattach', async () => {
    const f = await fixture();
    const textarea = [...f.element.querySelectorAll('textarea')].find(
      (item) => item.closest('.binding-editor') !== null,
    );
    if (textarea === undefined) throw new Error('binding_field_missing');
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
    if (descriptor?.set === undefined) throw new Error('textarea_setter_missing');
    const setter = descriptor.set.bind(textarea);
    await act(async () => {
      setter('Незавершённое обоснование привязки');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      await Promise.resolve();
    });
    expect(f.dirty).toHaveBeenLastCalledWith(true);
    await f.click('Проверить и показать материалы');
    expect(textarea.value).toBe('Незавершённое обоснование привязки');
    expect(f.transition).not.toHaveBeenCalled();
    const detail = vi.spyOn(f.api.importedRun, 'get');
    await act(async () => {
      await f.ref.current?.verifyAfterReattach();
    });
    expect(detail).not.toHaveBeenCalled();
    expect(textarea.value).toBe('Незавершённое обоснование привязки');
    expect(f.element.textContent).toContain('После восстановления worker требуется новая проверка');
    expect(f.dirty).toHaveBeenLastCalledWith(true);
  });
  it('keeps workspace pending until the read is drained for close or restart', async () => {
    const f = await fixture();
    let release: () => void = () => {};
    const held = new Promise<void>((done) => {
      release = done;
    });
    const original = f.api.importedRun.listInspectionPage.bind(f.api.importedRun);
    vi.spyOn(f.api.importedRun, 'listInspectionPage').mockImplementationOnce(async (query) => {
      await held;
      return original(query);
    });
    const photos = vi.spyOn(f.api.importedRun, 'listPhotoPage');
    await f.click('Проверить и показать материалы');
    expect(f.pending).toHaveBeenLastCalledWith(true);
    let completed = false;
    const draining = f.ref.current?.waitForPendingSave().then(() => {
      completed = true;
    });
    expect(completed).toBe(false);
    await act(async () => {
      release();
      await draining;
    });
    expect(completed).toBe(true);
    expect(photos).not.toHaveBeenCalled();
    expect(f.pending).toHaveBeenLastCalledWith(false);
  });
});
