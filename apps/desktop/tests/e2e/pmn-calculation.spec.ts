import { mkdirSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { _electron as electron, expect, test } from '@playwright/test';
import type { ImpellerApi } from '@impeller-reliability/contracts';

declare global {
  interface Window {
    readonly impeller?: ImpellerApi;
  }
}

test('saves two PMN results, keeps a separate draft, and reopens frozen history', async () => {
  const root = resolve(import.meta.dirname, '../../../..');
  const evidenceRoot = resolve(root, '.tmp/.codex/evidence/pmn-import-e2e');
  const projectPath = join(evidenceRoot, 'pmn-import.irproj');
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
        'fixtures/contracts/r130run/v1/m9a/packages/normal_final_pmn.r130run',
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
    await page.getByLabel('Полное наименование').fill('Локальная модель ПМН');
    await page.getByRole('button', { name: 'Сохранить модель' }).click();
    await page.getByRole('button', { name: 'Образцы' }).click();
    await page.getByRole('combobox', { name: 'Модель рабочего колеса' }).click();
    await page.getByRole('option', { name: 'Локальная модель ПМН' }).click();
    await page.getByLabel('Идентификационный номер').fill('LOCAL-PMN-001');
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
          designation: 'ПМН-ОТК-01',
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
    await expect(page.getByRole('heading', { name: 'normal_final_pmn' })).toBeVisible();
    await page.getByRole('combobox', { name: 'Local Specimen' }).click();
    await page.getByRole('option', { name: 'LOCAL-PMN-001 — Локальная модель ПМН' }).click();
    await page.getByLabel('Причина привязки').fill('Идентичность подтверждена инженером');
    await page.getByRole('button', { name: 'Сохранить привязку' }).click();
    await page.getByRole('button', { name: 'Подготовить исполнение для анализа' }).click();
    await expect(page.getByText(/Аналитическое исполнение ПМН подтверждено/u)).toBeVisible();

    await page.getByRole('button', { name: 'Расчёт ПМН' }).click();
    await page.getByRole('combobox', { name: 'Модель рабочего колеса' }).click();
    await page.getByRole('option', { name: 'Локальная модель ПМН' }).click();
    await page.getByRole('button', { name: /ПМН.*normal_final_pmn/u }).click();
    await expect(page.getByText('Методические требования в источнике R130SH')).toBeVisible();
    await expect(page.getByText('Доступно документов дела: 1')).toBeVisible();
    await page
      .getByRole('textbox', { name: 'Основание создания расчётного снимка' })
      .fill('Проверка формы ПМН');
    await page.getByRole('button', { name: 'Рассчитать и сохранить ПМН' }).click();
    await expect(page.locator('.feedback--error')).toBeFocused();
    await expect(
      page
        .getByRole('group', { name: /Номинальная частота nР/u })
        .getByText(/Выберите происхождение/u),
    ).toBeVisible();
    const fieldLabels = [
      'Номинальная частота nР',
      'Число циклов NЦ3',
      'Коэффициент превышения частоты k3',
      'Время разгона tР3',
      'Время установившегося вращения tУСТ3',
      'Время торможения tТ3',
    ];
    for (const field of fieldLabels) {
      await page
        .getByRole('group', { name: new RegExp(field, 'u') })
        .getByRole('combobox', { name: 'Происхождение значения' })
        .click();
      await page.getByRole('option', { name: 'Значение выбранного плана R130SH' }).click();
    }
    const reserve = page.getByRole('group', { name: /Коэффициент превышения частоты k3/u });
    await reserve.getByRole('combobox', { name: 'Происхождение значения' }).click();
    await page.getByRole('option', { name: 'Документированное дополнение инженера' }).click();
    await page.getByRole('button', { name: 'Рассчитать и сохранить ПМН' }).click();
    await expect(reserve.getByRole('textbox', { name: 'Значение дополнения' })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
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
    await page.getByRole('combobox', { name: 'Применимость формулы таблицы 5' }).click();
    await page.getByRole('option', { name: 'Точное документированное время до отказа' }).click();
    await page.getByRole('textbox', { name: /T_ОТК, с/u }).fill('8');
    await page
      .getByRole('textbox', { name: 'Основание применимости' })
      .fill('От начала испытания до документированного отказа');
    await page.getByRole('button', { name: 'Рассчитать и сохранить ПМН' }).click();
    await expect(
      page.getByRole('combobox', { name: 'Документ с моментом отказа и началом отсчёта' }),
    ).toHaveAttribute('aria-invalid', 'true');
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
    await page.getByRole('button', { name: 'Рассчитать и сохранить ПМН' }).click();
    await expect(page.getByText(/Расчёт ПМН сохранён/u)).toBeVisible();
    const result = page.getByRole('region', { name: 'Зафиксированный результат' });
    await expect(result).toContainText('2250 об/мин');
    await expect(result).toContainText('10 с');
    await expect(result).toContainText(
      'Количество циклов до отказа по таблице 5 ПМИ, округление вверх: 2',
    );
    await expect(result).toContainText('не число полностью завершённых или зачтённых циклов');
    await expect(result).toContainText('T_ОТК: 8 с');
    const saved = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const wheels = await api.wheelModel.list(false);
      if (!wheels.ok || wheels.result[0] === undefined) throw new Error('wheel_missing');
      const page = await api.pmnCalculation.listPage(wheels.result[0].wheelModelId);
      if (!page.ok || page.result.items[0] === undefined) throw new Error('pmn_history_missing');
      const detail = await api.pmnCalculation.getDetail(page.result.items[0].calculationSnapshotId);
      if (!detail.ok) throw new Error(detail.error.code);
      return {
        id: detail.result.calculationSnapshot.calculationSnapshotId,
        inputHash: detail.result.inputSnapshot.contentSha256,
        resultHash: detail.result.calculationSnapshot.contentSha256,
        documentId: detail.result.inputSnapshot.inputSnapshot.failureEvidence?.document?.documentId,
      };
    });
    expect(saved.documentId).toBe(documentId);
    await page.getByRole('combobox', { name: 'Редакция плана в выбранном архиве' }).click();
    await page.getByRole('option', { name: 'Эффективный план' }).click();
    for (const field of fieldLabels) {
      await page
        .getByRole('group', { name: new RegExp(field, 'u') })
        .getByRole('combobox', { name: 'Происхождение значения' })
        .click();
      await page.getByRole('option', { name: 'Значение выбранного плана R130SH' }).click();
    }
    const cycles = page.getByRole('group', { name: /Число циклов NЦ3/u });
    await cycles.getByRole('combobox', { name: 'Происхождение значения' }).click();
    await page.getByRole('option', { name: 'Документированное дополнение инженера' }).click();
    await cycles.getByRole('textbox', { name: 'Значение дополнения' }).fill('3');
    await cycles.getByRole('textbox', { name: 'Основание замещения' }).fill('Повторный сценарий');
    await page.getByRole('combobox', { name: 'Применимость формулы таблицы 5' }).click();
    await page.getByRole('option', { name: 'Точное документированное время до отказа' }).click();
    await page.getByRole('textbox', { name: /T_ОТК, с/u }).fill('0');
    await page
      .getByRole('textbox', { name: 'Основание применимости' })
      .fill('Отказ в начале отсчёта');
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
      .fill('раздел 4, начало и отказ');
    const reason = page.getByRole('textbox', { name: 'Основание создания расчётного снимка' });
    await reason.fill('Повторный расчёт по эффективному плану');
    await page.getByRole('button', { name: 'Рассчитать и сохранить ПМН' }).click();
    await expect(page.getByText(/Расчёт ПМН сохранён/u)).toBeVisible();
    await expect(result).toContainText(
      'Количество циклов до отказа по таблице 5 ПМИ, округление вверх: 0',
    );
    const second = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const wheels = await api.wheelModel.list(false);
      if (!wheels.ok || wheels.result[0] === undefined) throw new Error('wheel_missing');
      const history = await api.pmnCalculation.listPage(wheels.result[0].wheelModelId);
      if (!history.ok || history.result.items.length !== 2 || history.result.items[0] === undefined)
        throw new Error('two_pmn_results_missing');
      const detail = await api.pmnCalculation.getDetail(
        history.result.items[0].calculationSnapshotId,
      );
      if (!detail.ok) throw new Error(detail.error.code);
      return {
        id: detail.result.calculationSnapshot.calculationSnapshotId,
        inputHash: detail.result.inputSnapshot.contentSha256,
        resultHash: detail.result.calculationSnapshot.contentSha256,
        plan: detail.result.inputSnapshot.inputSnapshot.source.planSelection,
        failureDuration:
          detail.result.inputSnapshot.inputSnapshot.failureEvidence?.durationToFailureS,
        documentLocator:
          detail.result.inputSnapshot.inputSnapshot.failureEvidence?.document?.locator,
      };
    });
    expect(second).toMatchObject({
      plan: 'effective',
      failureDuration: '0',
      documentLocator: 'раздел 4, начало и отказ',
    });
    await reason.fill('Третий черновик остаётся отдельно');
    await expect(page.getByText('Есть несохранённые изменения.')).toBeVisible();
    await page.getByRole('button', { name: /NЦ3: 2 · таблица 5: рассчитана/u }).click();
    await expect(result).toContainText('T_ОТК: 8 с');
    await expect(reason).toHaveValue('Третий черновик остаётся отдельно');
    await page.getByRole('button', { name: /NЦ3: 3 · таблица 5: рассчитана/u }).click();
    await expect(result).toContainText('T_ОТК: 0 с');
    await expect(reason).toHaveValue('Третий черновик остаётся отдельно');
    const historyCount = await page.evaluate(async () => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const wheels = await api.wheelModel.list(false);
      if (!wheels.ok || wheels.result[0] === undefined) throw new Error('wheel_missing');
      const history = await api.pmnCalculation.listPage(wheels.result[0].wheelModelId);
      if (!history.ok) throw new Error(history.error.code);
      return history.result.items.length;
    });
    expect(historyCount).toBe(2);
    await page.getByRole('button', { name: 'Диагностика' }).click();
    await page.getByRole('button', { name: 'Перезапустить ядро' }).click();
    await expect(page.getByRole('dialog', { name: 'Есть несохранённые изменения' })).toBeVisible();
    await page.getByRole('button', { name: 'Перезапустить, не удаляя черновик' }).click();
    await expect(page.getByText('Локальный контур готов к работе.')).toBeVisible();
    await page.getByRole('button', { name: 'Проекты' }).click();
    await expect(reason).toHaveValue('Третий черновик остаётся отдельно');
    await expect(
      page.getByText('Ядро перезапущено. Черновик остался только в форме и не записан в проект.'),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /NЦ3: 2 · таблица 5: рассчитана/u }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /NЦ3: 3 · таблица 5: рассчитана/u }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Обзор' }).click();
    await expect(page.getByText('Сначала решите, что делать с черновиком')).toBeVisible();
    await page.getByRole('button', { name: 'Остаться здесь' }).click();
    await expect(reason).toHaveValue('Третий черновик остаётся отдельно');
    await page.getByRole('button', { name: 'Закрыть проект' }).click();
    await page.getByRole('button', { name: 'Закрыть без сохранения' }).click();
    await page.getByRole('button', { name: 'Новый проект' }).click();
    await page.getByRole('button', { name: 'Расчёт ПМН' }).click();
    await page.getByRole('combobox', { name: 'Модель рабочего колеса' }).click();
    await page.getByRole('option', { name: 'Локальная модель ПМН' }).click();
    await page.getByRole('button', { name: /NЦ3: 2 · таблица 5: рассчитана/u }).click();
    const historicalResult = page.getByRole('region', { name: 'Зафиксированный результат' });
    await expect(historicalResult.getByText('Контекст сохранённого плана')).toBeVisible();
    await expect(historicalResult).toContainText('Методическое требование источника');
    await expect(historicalResult).toContainText('Уставка источника');
    await expect(historicalResult).toContainText('2250 об/мин');
    await expect(historicalResult).toContainText('10 с');
    await expect(historicalResult).toContainText('T_ОТК: 8 с');
    await expect(historicalResult).toContainText(
      'Применимость: Точное документированное время до отказа',
    );
    await expect(historicalResult).toContainText('Принято: 1.5 безразмерный');
    await expect(historicalResult).toContainText('исходное значение плана: 1.1 безразмерный');
    await expect(historicalResult).toContainText(
      'Протокол времени до отказа, редакция записи 1 (01), раздел 3, начало и отказ',
    );
    const reopened = await page.evaluate(async (calculationId) => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const detail = await api.pmnCalculation.getDetail(calculationId);
      if (!detail.ok) throw new Error(detail.error.code);
      return {
        inputHash: detail.result.inputSnapshot.contentSha256,
        resultHash: detail.result.calculationSnapshot.contentSha256,
      };
    }, saved.id);
    expect(reopened).toEqual({ inputHash: saved.inputHash, resultHash: saved.resultHash });
    await page.getByRole('button', { name: /NЦ3: 3 · таблица 5: рассчитана/u }).click();
    await expect(historicalResult).toContainText('15 с');
    await expect(historicalResult).toContainText('T_ОТК: 0 с');
    await expect(historicalResult).toContainText('раздел 4, начало и отказ');
    const reopenedSecond = await page.evaluate(async (calculationId) => {
      const api = window.impeller;
      if (api === undefined) throw new Error('preload_api_missing');
      const detail = await api.pmnCalculation.getDetail(calculationId);
      if (!detail.ok) throw new Error(detail.error.code);
      return {
        inputHash: detail.result.inputSnapshot.contentSha256,
        resultHash: detail.result.calculationSnapshot.contentSha256,
      };
    }, second.id);
    expect(reopenedSecond).toEqual({ inputHash: second.inputHash, resultHash: second.resultHash });
    expect(errors).toEqual([]);
  } finally {
    await app.close();
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});
