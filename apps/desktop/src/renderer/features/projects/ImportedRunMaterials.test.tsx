import { MantineProvider } from '@mantine/core';
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  DesktopResult,
  ImpellerApi,
  InspectionMaterialPage,
  MaterialOrigin,
} from '@impeller-reliability/contracts';
import { createPreviewApi } from '../../preview-api';
import { ImportedRunMaterials, type ImportedRunMaterialsHandle } from './ImportedRunMaterials';

const mounted: { root: Root; element: HTMLDivElement }[] = [];
beforeEach(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true));
afterEach(async () => {
  for (const { root, element } of mounted.splice(0)) {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    element.remove();
  }
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function fixture() {
  const api = createPreviewApi('ready');
  const project = await api.project.open();
  const imports = await api.importedRun.list();
  if (!project.ok || !imports.ok || imports.result.length !== 2)
    throw new Error('preview_fixture_missing');
  const origins: MaterialOrigin[] = imports.result.map((item) => ({
    projectId: project.result.projectId,
    localImportId: item.localImportId,
    packageId: item.packageId,
    runId: item.runId,
    exportRevision: item.exportRevision,
    outerPackageSha256: item.outerPackageSha256,
  }));
  const first = origins[0];
  const second = origins[1];
  if (first === undefined || second === undefined) throw new Error('origin_missing');
  return { api, first, second };
}
async function mount(api: ImpellerApi, origin: MaterialOrigin) {
  const element = document.createElement('div');
  document.body.append(element);
  const root = createRoot(element);
  mounted.push({ root, element });
  const ref = createRef<ImportedRunMaterialsHandle>();
  const work: Promise<void>[] = [];
  async function render(nextOrigin = origin, disabled = false, refreshRevision = 0) {
    await act(async () => {
      root.render(
        <MantineProvider>
          <ImportedRunMaterials
            ref={ref}
            api={api}
            origin={nextOrigin}
            disabled={disabled}
            refreshRevision={refreshRevision}
            onWork={(operation) => {
              work.push(operation);
            }}
          />
        </MantineProvider>,
      );
      await Promise.resolve();
    });
  }
  async function click(text: string, settle = true) {
    const button = [...element.querySelectorAll('button')].find(
      (candidate) => candidate.textContent?.trim() === text,
    );
    if (button === undefined) throw new Error(`button_missing:${text}`);
    await act(async () => {
      button.click();
      await Promise.resolve();
    });
    if (settle)
      await act(async () => {
        await Promise.all(work.splice(0));
      });
  }
  await render();
  return { element, ref, work, render, click };
}

describe('source-only material UI', () => {
  it('shows exact zero/false/null, source text and decimal protocol identities', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы');
    await ui.click('Показать осмотр synthetic-pre-test');
    const pairs = [...ui.element.querySelectorAll('dl > div')].map((row) => [
      row.querySelector('dt')?.textContent,
      row.querySelector('dd')?.textContent,
    ]);
    expect(pairs).toContainEqual(['Время от начала, с', '0']);
    expect(pairs).toContainEqual(['Трещины', 'Нет (false)']);
    expect(pairs).toContainEqual(['Номер превышения вибропорога', 'Неприменимо (null)']);
    expect(ui.element.textContent).toContain('Не оценены (not_assessed)');
    expect(ui.element.textContent).toContain('<script>это исходный текст, а не HTML</script>');
    expect(ui.element.querySelector('script')).toBeNull();
    expect(ui.element.textContent).toContain('9007199254740995');
    expect(ui.element.textContent).toContain('9007199254740993');
    expect(ui.element.textContent).toContain('Для всего запуска');
  });
  it('has explicit absent, unavailable, ambiguous and oversized states with no open action', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.second);
    await ui.click('Проверить и показать материалы');
    expect(ui.element.textContent).toContain('Протокол не включён в эту редакцию пакета');
    expect(ui.element.textContent).toContain('Недоступен по источнику');
    expect(ui.element.textContent).toContain('Идентичность неоднозначна');
    expect(ui.element.textContent).toContain('Превышен предел записи');
    const actions = [...ui.element.querySelectorAll('button')].map((button) => button.textContent);
    expect(actions.some((text) => text?.includes('c29b7824'))).toBe(false);
    expect(actions.some((text) => text?.includes('ad6a90d1'))).toBe(false);
    expect(actions).not.toContain('Открыть исходный PDF');
  });
  it('keeps pending through OS completion and sends only the chosen identity', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы');
    let release: () => void = () => {};
    const barrier = new Promise<void>((done) => {
      release = done;
    });
    const open = vi
      .spyOn(f.api.importedRun, 'openMaterial')
      .mockImplementation(async ({ identity }) => {
        await barrier;
        return {
          ok: true,
          result: { identity, opened: true, sizeBytes: 1488, sha256: '3'.repeat(64) },
        };
      });
    await ui.click('Открыть исходный PDF', false);
    expect(ui.element.getAttribute('aria-busy')).toBeNull();
    expect(ui.element.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(ui.element.textContent).not.toContain('Открытие передано системной программе.');
    expect(open.mock.calls[0]?.[0]).toMatchObject({
      identity: { origin: f.first, kind: 'protocol', materialId: '9007199254740995' },
    });
    release();
    await act(async () => {
      await Promise.all(ui.work.splice(0));
    });
    expect(ui.element.textContent).toContain('Открытие передано системной программе.');
  });
  it('reports shell failure and cancels by UUID without clearing pending early', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы');
    let release: () => void = () => {};
    const barrier = new Promise<void>((done) => {
      release = done;
    });
    const original = f.api.importedRun.openMaterial.bind(f.api.importedRun);
    const open = vi.spyOn(f.api.importedRun, 'openMaterial').mockImplementation(async (command) => {
      await barrier;
      return original(command);
    });
    const cancel = vi
      .spyOn(f.api.importedRun, 'cancelMaterialOpen')
      .mockResolvedValue({ ok: true, result: { cancelled: true } });
    await ui.click('Открыть исходный PDF', false);
    await ui.click('Отменить открытие', false);
    expect(cancel).toHaveBeenCalledWith(open.mock.calls[0]?.[0].operationId);
    expect(ui.element.querySelector('[aria-busy="true"]')).not.toBeNull();
    release();
    await act(async () => {
      await Promise.all(ui.work.splice(0));
    });
    expect(ui.element.querySelector('[role="alert"]')?.textContent).toContain(
      'material_open_failed',
    );
    expect(ui.element.textContent).not.toContain('Открытие передано системной программе.');
  });
  it('keeps ambiguous inspections visible without offering a rejected detail action', async () => {
    const f = await fixture();
    const page = await f.api.importedRun.listInspectionPage({ origin: f.first });
    if (!page.ok || page.result.items[0] === undefined) throw new Error('inspection_missing');
    vi.spyOn(f.api.importedRun, 'listInspectionPage').mockResolvedValueOnce({
      ok: true,
      result: { ...page.result, items: [{ ...page.result.items[0], state: 'ambiguous' }] },
    });
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы');
    expect(ui.element.textContent).toContain('Идентичность неоднозначна');
    expect(
      [...ui.element.querySelectorAll('button')].some((button) =>
        button.textContent?.includes('Показать осмотр'),
      ),
    ).toBe(false);
  });
  it('shows completed cancellation as status and drains the open operation first', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы');
    let release: () => void = () => {};
    const barrier = new Promise<void>((done) => {
      release = done;
    });
    vi.spyOn(f.api.importedRun, 'openMaterial').mockImplementation(async () => {
      await barrier;
      return {
        ok: false,
        error: {
          code: 'cancelled',
          message: 'Отменено до передачи ОС.',
          details: {},
          retryable: false,
        },
      };
    });
    vi.spyOn(f.api.importedRun, 'cancelMaterialOpen').mockResolvedValue({
      ok: true,
      result: { cancelled: true },
    });
    await ui.click('Открыть исходный PDF', false);
    await ui.click('Отменить открытие', false);
    expect(ui.element.querySelector('[aria-busy="true"]')).not.toBeNull();
    release();
    await act(async () => {
      await Promise.all(ui.work.splice(0));
    });
    expect(ui.element.querySelector('[aria-busy="true"]')).toBeNull();
    expect(ui.element.querySelector('[role="alert"]')).toBeNull();
    expect(ui.element.textContent).toContain('Открытие материала отменено.');
    expect(ui.element.textContent).not.toContain('Открытие передано системной программе.');
  });
  it('discards a late old-origin response and does not start its remaining reads', async () => {
    const f = await fixture();
    const oldPage = await f.api.importedRun.listInspectionPage({ origin: f.first });
    let release: (result: DesktopResult<InspectionMaterialPage>) => void = () => {};
    const barrier = new Promise<DesktopResult<InspectionMaterialPage>>((done) => {
      release = done;
    });
    vi.spyOn(f.api.importedRun, 'listInspectionPage').mockReturnValueOnce(barrier);
    const photos = vi.spyOn(f.api.importedRun, 'listPhotoPage');
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы', false);
    await ui.render(f.second);
    release(oldPage);
    await act(async () => {
      await Promise.all(ui.work.splice(0));
    });
    expect(photos).not.toHaveBeenCalled();
    expect(ui.element.textContent).not.toContain('9007199254740995');
    await ui.click('Проверить и показать материалы');
    expect(ui.element.textContent).toContain('Протокол не включён в эту редакцию пакета');
  });
  it('rejects a response carrying a foreign SHA and shows local source errors', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.first);
    const page = await f.api.importedRun.listInspectionPage({ origin: f.first });
    if (!page.ok) throw new Error('page_missing');
    vi.spyOn(f.api.importedRun, 'listInspectionPage').mockResolvedValueOnce({
      ok: true,
      result: { ...page.result, origin: { ...f.first, outerPackageSha256: 'f'.repeat(64) } },
    });
    await ui.click('Проверить и показать материалы');
    expect(ui.element.querySelector('[role="alert"]')?.textContent).toContain('другой редакции');
    expect(ui.element.textContent).not.toContain('Синтетический протокол UI');
    vi.spyOn(f.api.importedRun, 'listInspectionPage').mockResolvedValueOnce({
      ok: false,
      error: {
        code: 'file_missing',
        message: 'Управляемый архив отсутствует.',
        details: {},
        retryable: false,
      },
    });
    await ui.click('Проверить и показать материалы');
    expect(ui.element.querySelector('[role="alert"]')?.textContent).toContain(
      'Управляемый архив отсутствует',
    );
  });
  it('marks prior verification stale after reattach and blocks disabled reads', async () => {
    const f = await fixture();
    const ui = await mount(f.api, f.first);
    await ui.click('Проверить и показать материалы');
    const read = vi.spyOn(f.api.importedRun, 'listInspectionPage');
    await ui.render(f.first, true, 1);
    await ui.click('Проверить и показать материалы');
    expect(read).not.toHaveBeenCalled();
    expect(ui.element.textContent).toContain('требуется новая проверка');
    expect(ui.element.textContent).not.toContain('Синтетический протокол UI');
    await ui.render(f.first, false, 1);
    await ui.click('Проверить и показать материалы');
    expect(read).toHaveBeenCalledOnce();
  });
});
