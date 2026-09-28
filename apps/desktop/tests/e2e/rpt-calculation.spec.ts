import { mkdirSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { _electron as electron, expect, test } from '@playwright/test';
import type { ImpellerApi } from '@impeller-reliability/contracts';

declare global {
  interface Window {
    readonly impeller?: ImpellerApi;
  }
}

test('imports RPT, saves documented inputs and table 4, then reopens the exact snapshot', async () => {
  const root = resolve(import.meta.dirname, '../../../..');
  const evidenceRoot = resolve(root, '.tmp/.codex/evidence/rpt-import-e2e');
  const projectPath = join(evidenceRoot, 'rpt-import.irproj');
  rmSync(evidenceRoot, { recursive: true, force: true });
  mkdirSync(evidenceRoot, { recursive: true });
  const app = await electron.launch({
    args: [join(resolve(import.meta.dirname, '../..'), 'out/main/index.js')],
    cwd: root,
    env: {
      ...process.env,
      NODE_ENV: 'test',
      IMPELLER_AUTOMATED_PROJECT_PATH: projectPath,
      IMPELLER_AUTOMATED_R130RUN_PATH: join(
        root,
        'fixtures/contracts/r130run/v1/m9a/packages/normal_final_rpt_full_stop.r130run',
      ),
      IMPELLER_TEST_USER_DATA: join(evidenceRoot, 'user-data'),
    },
  });
  const errors: string[] = [];
  try {
    const page = await app.firstWindow();
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByRole('button', { name: 'Создать проект' }).click();
    await page.getByRole('button', { name: 'Модели колёс' }).click();
    await page.getByLabel('Полное наименование').fill('Локальная модель РПТ');
    await page.getByRole('button', { name: 'Сохранить модель' }).click();
    await page.getByRole('button', { name: 'Образцы' }).click();
    await page.getByRole('combobox', { name: 'Модель рабочего колеса' }).click();
    await page.getByRole('option', { name: 'Локальная модель РПТ' }).click();
    await page.getByLabel('Идентификационный номер').fill('LOCAL-RPT-001');
    await page.getByRole('button', { name: 'Сохранить образец' }).click();
    const documentId = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const wheels = await api.wheelModel.list(false);
      if (!wheels.ok || wheels.result[0] === undefined) throw new Error('wheel_missing');
      const created = await api.caseDocument.create({
        caseDocumentId: crypto.randomUUID(),
        document: {
          documentKind: 'measurement_or_attestation_record',
          title: 'Протокол времени до отказа',
          designation: 'РПТ-ОТК-01',
          revisionLabel: '01',
          documentDate: '2024-07-02',
          issuer: 'ЛИЦ ВВУ',
          notes: '',
        },
        wheelModelIds: [wheels.result[0].wheelModelId],
        specimenIds: [],
      });
      if (!created.ok) throw new Error(created.error.code);
      return created.result.caseDocumentId;
    });
    await page.getByRole('button', { name: 'Результаты R130SH' }).click();
    await page.getByRole('button', { name: 'Импортировать результат R130SH' }).click();
    await expect(page.getByText('Импорт завершён')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'normal_final_rpt_full_stop' })).toBeVisible();
    await page.getByRole('combobox', { name: 'Local Specimen' }).click();
    await page.getByRole('option', { name: 'LOCAL-RPT-001 — Локальная модель РПТ' }).click();
    await page.getByLabel('Причина привязки').fill('Идентичность подтверждена инженером');
    await page.getByRole('button', { name: 'Сохранить привязку' }).click();
    await page.getByRole('button', { name: 'Подготовить исполнение для анализа' }).click();
    await expect(page.getByText(/Аналитическое исполнение РПТ подтверждено/u)).toBeVisible();

    await page.getByRole('button', { name: 'Расчёт РПТ' }).click();
    await page.getByRole('combobox', { name: 'Модель рабочего колеса' }).click();
    await page.getByRole('option', { name: 'Локальная модель РПТ' }).click();
    await page.getByRole('button', { name: /РПТ.*normal_final_rpt_full_stop/u }).click();
    await expect(page.getByText(/полная остановка/u)).toBeVisible();
    await expect(page.getByText('Методические требования в источнике R130SH')).toBeVisible();
    await expect(page.getByText('Доступно документов дела: 1')).toBeVisible();
    for (const field of [
      'Номинальная частота nP',
      'Заданное число циклов NЦ',
      'Коэффициент запаса k2',
      'Время разгона tР',
      'Время установившегося вращения tУСТ',
      'Время торможения tТ',
    ]) {
      await page
        .getByRole('group', { name: new RegExp(field, 'u') })
        .getByRole('combobox', { name: 'Происхождение значения' })
        .click();
      await page.getByRole('option', { name: 'Значение выбранного плана R130SH' }).click();
    }
    const reserve = page.getByRole('group', { name: /Коэффициент запаса k2/u });
    await reserve.getByRole('combobox', { name: 'Происхождение значения' }).click();
    await page.getByRole('option', { name: 'Документированное дополнение инженера' }).click();
    await reserve.getByRole('textbox', { name: 'Значение дополнения' }).fill('1.5');
    await reserve
      .getByRole('textbox', { name: 'Основание замещения' })
      .fill('Сценарий по протоколу');
    await reserve.getByRole('combobox', { name: 'Документ дела (если использован)' }).click();
    await page
      .getByRole('option')
      .filter({ hasText: 'Протокол времени до отказа' })
      .first()
      .click();
    await reserve.getByRole('textbox', { name: 'Раздел или поле документа' }).fill('раздел 2');
    await page.getByRole('combobox', { name: 'Применимость формулы таблицы 4' }).click();
    await page.getByRole('option', { name: 'Точное документированное время до отказа' }).click();
    await page.getByRole('textbox', { name: /T_ОТК, с/u }).fill('8');
    await page
      .getByRole('textbox', { name: 'Основание применимости' })
      .fill('От начала испытания до документированного отказа');
    await page
      .getByRole('combobox', { name: 'Документ с моментом отказа и началом отсчёта' })
      .click();
    await page
      .getByRole('option')
      .filter({ hasText: 'Протокол времени до отказа' })
      .first()
      .click();
    await page
      .getByRole('group', { name: /Фактическое время до отказа/u })
      .getByRole('textbox', { name: 'Раздел или поле документа' })
      .fill('раздел 3, начало и отказ');
    await page
      .getByRole('textbox', { name: 'Основание создания расчётного снимка' })
      .fill('Расчёт по документированному сценарию');
    await page.getByRole('button', { name: 'Рассчитать и сохранить РПТ' }).click();
    await expect(page.getByText(/Расчёт РПТ сохранён/u)).toBeVisible();
    const result = page.getByRole('region', { name: 'Зафиксированный результат' });
    await expect(result).toContainText('3 циклов');
    await expect(result).toContainText('12 с');
    await expect(result).toContainText('2 циклов до отказа');
    await expect(result).toContainText('Нижняя точка уставки отличается от типовой формулы');
    const saved = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const wheels = await api.wheelModel.list(false);
      if (!wheels.ok || wheels.result[0] === undefined) throw new Error('wheel_missing');
      const page = await api.rptCalculation.listPage(wheels.result[0].wheelModelId);
      if (!page.ok || page.result.items[0] === undefined) throw new Error('rpt_history_missing');
      const detail = await api.rptCalculation.getDetail(page.result.items[0].calculationSnapshotId);
      if (!detail.ok) throw new Error(detail.error.code);
      return {
        id: detail.result.calculationSnapshot.calculationSnapshotId,
        inputHash: detail.result.inputSnapshot.contentSha256,
        resultHash: detail.result.calculationSnapshot.contentSha256,
        documentId: detail.result.inputSnapshot.inputSnapshot.failureEvidence?.document?.documentId,
      };
    });
    expect(saved.documentId).toBe(documentId);
    await page.getByRole('button', { name: 'Закрыть проект' }).click();
    await page.getByRole('button', { name: 'Новый проект' }).click();
    await page.getByRole('button', { name: 'Расчёт РПТ' }).click();
    await page.getByRole('combobox', { name: 'Модель рабочего колеса' }).click();
    await page.getByRole('option', { name: 'Локальная модель РПТ' }).click();
    await page.getByRole('button', { name: /3 циклов · таблица 4: рассчитана/u }).click();
    const historicalResult = page.getByRole('region', { name: 'Зафиксированный результат' });
    await expect(historicalResult.getByText('Контекст сохранённого плана')).toBeVisible();
    await expect(historicalResult).toContainText('Методическое требование источника');
    await expect(historicalResult).toContainText('Уставка источника');
    await expect(historicalResult).toContainText('полная остановка');
    const reopened = await page.evaluate(async (calculationId) => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const detail = await api.rptCalculation.getDetail(calculationId);
      if (!detail.ok) throw new Error(detail.error.code);
      return {
        inputHash: detail.result.inputSnapshot.contentSha256,
        resultHash: detail.result.calculationSnapshot.contentSha256,
      };
    }, saved.id);
    expect(reopened).toEqual({ inputHash: saved.inputHash, resultHash: saved.resultHash });
    expect(errors).toEqual([]);
  } finally {
    await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});
