import { Button, Group, Modal, Text, Title } from '@mantine/core';
import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type {
  DesktopError,
  ImpellerApi,
  InspectionMaterialPage,
  InspectionMaterialDetail,
  MaterialIdentity,
  MaterialCopyReleaseRequest,
  MaterialOrigin,
  PhotoMaterialPage,
  ProtocolMaterialDetail,
} from '@impeller-reliability/contracts';
import { sameMaterialOrigin } from './material-origin';

export interface ImportedRunMaterialsHandle {
  invalidate(): void;
}
interface Props {
  readonly api: ImpellerApi;
  readonly origin: MaterialOrigin;
  readonly disabled: boolean;
  readonly refreshRevision: number;
  readonly onWork: (work: Promise<void>, opens: boolean) => void;
}
const stateLabels = {
  verified: 'Метаданные проверены',
  ambiguous: 'Идентичность неоднозначна',
  unavailable: 'Недоступен по источнику',
  too_large: 'Превышен предел записи',
  not_included: 'Протокол не включён в эту редакцию пакета',
};
function readError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Не удалось прочитать материал. Восстановите worker и повторите проверку.',
    details: {},
    retryable: true,
  };
}
function originError(): DesktopError {
  return {
    code: 'file_integrity_mismatch',
    message: 'Ответ относится к другой редакции источника.',
    details: {},
    retryable: false,
  };
}

export const ImportedRunMaterials = forwardRef<ImportedRunMaterialsHandle, Props>(
  function ImportedRunMaterials({ api, origin, disabled, refreshRevision, onWork }, ref) {
    const titleId = useId();
    const [inspections, setInspections] = useState<InspectionMaterialPage | null>(null);
    const [inspection, setInspection] = useState<InspectionMaterialDetail | null>(null);
    const [photos, setPhotos] = useState<PhotoMaterialPage | null>(null);
    const [protocol, setProtocol] = useState<ProtocolMaterialDetail | null>(null);
    const [busy, setBusy] = useState(false);
    const [openingPending, setOpeningPending] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [copyRelease, setCopyRelease] = useState<MaterialCopyReleaseRequest | null>(null);
    const generation = useRef(0);
    const pending = useRef<Promise<void> | null>(null);
    const opening = useRef<string | null>(null);
    const openingIdentity = useRef<MaterialIdentity | null>(null);
    const openingCurrent = useRef<(() => boolean) | null>(null);
    const active = useRef(true);
    const openTrigger = useRef<HTMLButtonElement | null>(null);
    const latest = useRef({ origin, disabled, refreshRevision });
    useLayoutEffect(() => {
      latest.current = { origin, disabled, refreshRevision };
    }, [origin, disabled, refreshRevision]);
    const invalidate = useCallback((): void => {
      generation.current += 1;
      const id = opening.current;
      if (id !== null)
        void api.importedRun
          .cancelMaterialOpen(id)
          .then((response) => {
            if (active.current) {
              setCopyRelease(null);
              if (!response.ok) setError(response.error);
            }
          })
          .catch(() => {
            if (active.current) setError(readError());
          });
    }, [api]);
    useImperativeHandle(ref, () => ({ invalidate }), [invalidate]);
    useEffect(() => {
      active.current = true;
      return () => {
        active.current = false;
        invalidate();
      };
    }, [invalidate]);
    useEffect(() => {
      if (disabled) invalidate();
    }, [disabled, invalidate]);
    useEffect(
      () =>
        api.importedRun.subscribeCopyReleaseRequested((request) => {
          if (
            !active.current ||
            latest.current.disabled ||
            opening.current !== request.operationId ||
            !openingCurrent.current?.() ||
            openingIdentity.current?.kind !== request.identity.kind ||
            openingIdentity.current.materialId !== request.identity.materialId ||
            !sameMaterialOrigin(latest.current.origin, request.identity.origin)
          ) {
            void api.importedRun.respondCopyRelease({ ...request, decision: 'keep' }).catch(() => {
              if (active.current && opening.current === request.operationId) setError(readError());
            });
            return;
          }
          setCopyRelease(request);
        }),
      [api],
    );
    useEffect(() => {
      if (opening.current !== null && !openingCurrent.current?.()) invalidate();
    }, [origin, refreshRevision, invalidate]);
    // Reattach invalidates the previous verification without touching dossier drafts.
    const [validRevision, setValidRevision] = useState(refreshRevision);
    const fresh = validRevision === refreshRevision;
    const inspected =
      inspections !== null && sameMaterialOrigin(inspections.origin, origin) && fresh;

    function run(work: (current: () => boolean) => Promise<void>, opens = false): void {
      if (pending.current !== null || disabled) return;
      const token = ++generation.current;
      const selectedOrigin = origin;
      const current = () =>
        active.current &&
        generation.current === token &&
        !latest.current.disabled &&
        latest.current.refreshRevision === refreshRevision &&
        sameMaterialOrigin(latest.current.origin, selectedOrigin);
      setBusy(true);
      setOpeningPending(opens);
      setError(null);
      setMessage(null);
      const operation = (async () => {
        try {
          await work(current);
        } catch {
          if (current()) setError(readError());
        } finally {
          pending.current = null;
          opening.current = null;
          openingIdentity.current = null;
          openingCurrent.current = null;
          if (active.current) {
            setBusy(false);
            setOpeningPending(false);
            setCopyRelease(null);
          }
        }
      })();
      pending.current = operation;
      onWork(operation, opens);
    }
    function checked(responseOrigin: MaterialOrigin): boolean {
      if (sameMaterialOrigin(responseOrigin, origin)) return true;
      setError(originError());
      return false;
    }
    function load(): void {
      run(async (current) => {
        setInspections(null);
        setInspection(null);
        setPhotos(null);
        setProtocol(null);
        setMessage('Проверка осмотров…');
        const first = await api.importedRun.listInspectionPage({ origin });
        if (!current()) return;
        if (!first.ok) {
          setError(first.error);
          setMessage(null);
          return;
        }
        if (!checked(first.result.origin)) return;
        setMessage('Проверка фотографий…');
        const second = await api.importedRun.listPhotoPage({ origin });
        if (!current()) return;
        if (!second.ok) {
          setError(second.error);
          setMessage(null);
          return;
        }
        if (!checked(second.result.origin)) return;
        setMessage('Проверка включённого протокола…');
        const third = await api.importedRun.getProtocol({ origin });
        if (!current()) return;
        if (!third.ok) {
          setError(third.error);
          setMessage(null);
          return;
        }
        if (!checked(third.result.origin)) return;
        setValidRevision(refreshRevision);
        setInspections(first.result);
        setPhotos(second.result);
        setProtocol(third.result);
        setInspection(null);
        setMessage('Материалы выбранной редакции прочитаны.');
      });
    }
    function selectInspection(id: string): void {
      run(async (current) => {
        setInspection(null);
        setMessage('Чтение выбранного осмотра…');
        const response = await api.importedRun.getInspection({ origin, inspectionId: id });
        if (!current()) return;
        if (!response.ok) {
          setError(response.error);
          setMessage(null);
          return;
        }
        if (!checked(response.result.origin) || response.result.item.materialId !== id) {
          setError(originError());
          return;
        }
        setInspection(response.result);
        setMessage(null);
      });
    }
    function page(kind: 'inspection' | 'photo', cursor: string): void {
      run(async (current) => {
        setMessage('Проверка следующей страницы…');
        if (kind === 'inspection') {
          const response = await api.importedRun.listInspectionPage({ origin, cursor });
          if (!current()) return;
          if (!response.ok) {
            setError(response.error);
            setMessage(null);
            return;
          }
          if (!checked(response.result.origin)) return;
          setInspections(response.result);
          setInspection(null);
        } else {
          const response = await api.importedRun.listPhotoPage({ origin, cursor });
          if (!current()) return;
          if (!response.ok) {
            setError(response.error);
            setMessage(null);
            return;
          }
          if (!checked(response.result.origin)) return;
          setPhotos(response.result);
        }
        setMessage(null);
      });
    }
    function openMaterial(identity: MaterialIdentity): void {
      const focused = document.activeElement;
      openTrigger.current = focused instanceof HTMLButtonElement ? focused : null;
      run(async (current) => {
        const operationId = crypto.randomUUID();
        opening.current = operationId;
        openingIdentity.current = identity;
        openingCurrent.current = current;
        setMessage('Проверка файла перед системным открытием…');
        const response = await api.importedRun.openMaterial({ identity, operationId });
        if (
          !response.ok &&
          response.error.code === 'material_open_unconfirmed' &&
          active.current &&
          sameMaterialOrigin(latest.current.origin, identity.origin)
        ) {
          setError(response.error);
          setMessage(null);
          return;
        }
        if (!current()) return;
        if (!response.ok) {
          if (response.error.code === 'cancelled') {
            setMessage('Открытие материала отменено.');
            return;
          }
          setError(response.error);
          setMessage(null);
          return;
        }
        if (
          !checked(response.result.identity.origin) ||
          response.result.identity.kind !== identity.kind ||
          response.result.identity.materialId !== identity.materialId
        ) {
          setError(originError());
          return;
        }
        setMessage(
          'Открытие передано системной программе. Это не подтверждение прочтения документа.',
        );
      }, true);
    }
    async function cancelOpen(): Promise<void> {
      const id = opening.current;
      if (id === null) return;
      try {
        const response = await api.importedRun.cancelMaterialOpen(id);
        if (!active.current || opening.current !== id) return;
        if (!response.ok) setError(response.error);
        else
          setMessage(
            response.result.cancelled
              ? 'Отмена запрошена; ожидаем завершения очистки.'
              : 'Системное открытие уже началось; ожидаем результат.',
          );
      } catch {
        if (active.current) setError(readError());
      }
    }
    async function decideCopyRelease(decision: 'keep' | 'release'): Promise<void> {
      const request = copyRelease;
      if (request === null) return;
      setCopyRelease(null);
      try {
        const response = await api.importedRun.respondCopyRelease({
          ...request,
          decision:
            openingCurrent.current?.() && opening.current === request.operationId
              ? decision
              : 'keep',
        });
        if (!response.ok || !response.result.accepted) {
          if (active.current && !response.ok) setError(response.error);
          void cancelOpen();
        }
      } catch {
        if (active.current) setError(readError());
        void cancelOpen();
      }
    }
    useEffect(() => {
      if (
        !busy &&
        active.current &&
        openTrigger.current?.isConnected &&
        !openTrigger.current.disabled
      ) {
        openTrigger.current.focus();
        openTrigger.current = null;
      }
    }, [busy]);
    const blocked = disabled || busy;
    return (
      <section
        className="r130sh-detail-section source-materials"
        aria-labelledby={titleId}
        aria-busy={busy}
      >
        <Modal
          opened={
            copyRelease !== null &&
            !disabled &&
            fresh &&
            sameMaterialOrigin(copyRelease.identity.origin, origin)
          }
          onClose={() => void decideCopyRelease('keep')}
          title="Освободить временные копии материалов?"
          centered
          size="lg"
          returnFocus={false}
          closeButtonProps={{ 'aria-label': 'Сохранить копии и закрыть подтверждение' }}
        >
          <Text>
            Каталог достиг 64 копий или для новой копии осталось меньше 100 МиБ. Общий предел — 400
            МиБ. Можно сохранить копии: материал откроется, если ему хватит оставшейся ёмкости.
          </Text>
          <Text mt="md">
            Закройте все окна фотографий и протоколов, открытые Impeller Reliability. Сохраните
            нужные правки в другом месте: временные копии и изменения в них будут удалены. Исходные
            материалы дела сохраняются.
          </Text>
          <Text mt="md" size="sm">
            Копии с незавершённым запросом ОС, неизвестные и подменённые файлы сохраняются и могут
            препятствовать освобождению. Если просмотрщики ещё открыты, сохраните копии.
          </Text>
          <Group mt="lg" justify="flex-end">
            <Button variant="default" data-autofocus onClick={() => void decideCopyRelease('keep')}>
              Сохранить копии
            </Button>
            <Button color="red" onClick={() => void decideCopyRelease('release')}>
              Освободить копии
            </Button>
          </Group>
        </Modal>
        <Title order={4} id={titleId}>
          Первичные материалы
        </Title>
        <Text>
          Редакция экспорта {origin.exportRevision}. Чтение доступно до привязки образца и не меняет
          дело.
        </Text>
        <Button variant="default" disabled={blocked} onClick={load}>
          {inspected ? 'Повторно проверить материалы' : 'Проверить и показать материалы'}
        </Button>
        {disabled ? <p role="status">Чтение и открытие материалов временно недоступны.</p> : null}
        {!fresh ? (
          <p role="status">После восстановления worker требуется новая проверка материалов.</p>
        ) : null}
        {message === null || disabled || !fresh ? null : <p role="status">{message}</p>}
        {error === null ? null : (
          <p role="alert">
            {error.message} ({error.code})
          </p>
        )}
        {!openingPending || !busy ? null : (
          <Button variant="default" onClick={() => void cancelOpen()}>
            Отменить открытие
          </Button>
        )}
        {!inspected ? null : (
          <>
            <p>
              Текущая проверка: {inspections.verification.validatorVersion}, область — метаданные
              первичных материалов. Историческое подтверждение импорта сохранено отдельно. Байты
              проверяются при открытии; содержание документа, подпись и вывод лаборатории этой
              проверкой не подтверждаются.
            </p>
            <Verification verification={inspections.verification} />
            <section aria-label="Осмотры">
              <Title order={5}>Осмотры</Title>
              {inspections.items.length === 0 ? (
                <p>В этой редакции нет осмотров.</p>
              ) : (
                <ul className="source-material-list">
                  {inspections.items.map((item) => (
                    <li key={item.sourceIndex}>
                      <strong>
                        {item.data === null ? item.materialId : stageLabel(item.data)}
                      </strong>
                      <span>{stateLabels[item.state]}</span>
                      {item.detail === null ? null : <p>{item.detail}</p>}
                      {item.materialId === null || item.state !== 'verified' ? null : (
                        <Button
                          size="compact-sm"
                          variant="default"
                          disabled={blocked}
                          onClick={() => selectInspection(item.materialId ?? '')}
                        >
                          Показать осмотр {item.materialId}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {inspections.pageBound === 'byte_limit' ? (
                <p>Страница ограничена размером ответа; продолжение доступно ниже.</p>
              ) : null}
              {inspections.nextCursor === null ? null : (
                <Button
                  variant="default"
                  disabled={blocked}
                  onClick={() => page('inspection', inspections.nextCursor ?? '')}
                >
                  Следующая страница осмотров
                </Button>
              )}
              {inspection === null ? null : (
                <InspectionDetail
                  detail={inspection}
                  availablePhotoIds={
                    photos?.items
                      .filter(
                        (item) =>
                          item.state === 'verified' && item.data?.availability === 'available',
                      )
                      .flatMap((item) => (item.materialId === null ? [] : [item.materialId])) ?? []
                  }
                  onOpen={(id) => openMaterial({ origin, kind: 'photo', materialId: id })}
                  blocked={blocked}
                />
              )}
            </section>
            {photos === null ? null : (
              <section aria-label="Фотографии">
                <Title order={5}>Фотографии этой редакции</Title>
                {photos.items.length === 0 ? (
                  <p>В этой редакции нет зарегистрированных фотографий.</p>
                ) : (
                  <ul className="source-material-list">
                    {photos.items.map((item) => (
                      <li key={item.sourceIndex}>
                        <strong>Фотография {item.materialId}</strong>
                        <span>{stateLabels[item.state]}</span>
                        {item.data === null ? null : (
                          <Rows
                            entries={[
                              [
                                'Связь',
                                item.data.inspectionId === null
                                  ? 'Для всего запуска'
                                  : `Осмотр ${item.data.inspectionId}`,
                              ],
                              ['Формат', item.data.mediaType],
                              ['Размер, байт', String(item.data.size)],
                              ['Размеры, px', `${item.data.widthPx} × ${item.data.heightPx}`],
                              ['SHA-256', item.data.sha256],
                              [
                                'Добавил',
                                `${item.data.actor.fullName}; ${item.data.actor.position}`,
                              ],
                              ['Идентификатор сотрудника', item.data.actor.employeeId],
                              ['Историческая атрибуция', String(item.data.actor.legacy)],
                              ['Время UTC', item.data.attachedAtUtc],
                              ['Причина недоступности', item.data.unavailableReason],
                            ]}
                          />
                        )}
                        {item.detail === null ? null : <p>{item.detail}</p>}
                        <References references={item.references} />
                        {item.state !== 'verified' ||
                        item.data?.availability !== 'available' ||
                        item.materialId === null ? null : (
                          <Button
                            size="compact-sm"
                            variant="default"
                            disabled={blocked}
                            onClick={() =>
                              openMaterial({
                                origin,
                                kind: 'photo',
                                materialId: item.materialId ?? '',
                              })
                            }
                          >
                            Открыть фотографию {item.materialId}
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {photos.pageBound === 'byte_limit' ? (
                  <p>Страница ограничена размером ответа; записи не обрезаны.</p>
                ) : null}
                {photos.nextCursor === null ? null : (
                  <Button
                    variant="default"
                    disabled={blocked}
                    onClick={() => page('photo', photos.nextCursor ?? '')}
                  >
                    Следующая страница фотографий
                  </Button>
                )}
              </section>
            )}
            {protocol === null ? null : (
              <section aria-label="Лабораторный протокол">
                <Title order={5}>Лабораторный протокол</Title>
                <p>{stateLabels[protocol.item.state]}</p>
                {protocol.item.detail === null ? null : <p>{protocol.item.detail}</p>}
                {protocol.item.data === null ? null : (
                  <Rows
                    entries={[
                      ['Номер протокола', protocol.item.data.protocolNumber],
                      ['Идентификатор выпуска', protocol.item.data.releaseId],
                      ['Редакция протокола', protocol.item.data.revisionNumber],
                      ['Шаблон', protocol.item.data.templateVersion],
                      ['Время выпуска UTC', protocol.item.data.releasedAtUtc],
                      ['Выпустил', protocol.item.data.releasedByActor.fullName],
                      ['Должность', protocol.item.data.releasedByActor.position],
                      ['Идентификатор сотрудника', protocol.item.data.releasedByActor.employeeId],
                      [
                        'Историческая атрибуция',
                        protocol.item.data.releasedByActor.legacy === null
                          ? null
                          : String(protocol.item.data.releasedByActor.legacy),
                      ],
                      [
                        'Исходные реквизиты сотрудника',
                        protocol.item.data.releasedByActor.sourceJson,
                      ],
                      ['Идентификатор запуска', protocol.item.data.runId],
                      ['Редакция экспорта', String(origin.exportRevision)],
                      ['SHA-256 PDF', protocol.item.data.contentSha256],
                      ['Размер PDF, байт', String(protocol.item.data.pdfSizeBytes)],
                    ]}
                  />
                )}
                <References references={protocol.item.references} />
                {protocol.item.state !== 'verified' || protocol.item.materialId === null ? null : (
                  <Button
                    variant="default"
                    disabled={blocked}
                    onClick={() =>
                      openMaterial({
                        origin,
                        kind: 'protocol',
                        materialId: protocol.item.materialId ?? '',
                      })
                    }
                  >
                    Открыть исходный PDF
                  </Button>
                )}
              </section>
            )}
          </>
        )}
      </section>
    );
  },
);

function Rows({
  entries,
}: {
  readonly entries: readonly (readonly [string, string | null])[];
}): React.JSX.Element {
  return (
    <dl className="source-data-list">
      {entries.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value === null ? 'Не указано (null)' : value === '' ? 'Пустая строка' : value}</dd>
        </div>
      ))}
    </dl>
  );
}
function sourceBoolean(value: boolean): string {
  return value ? 'Да (true)' : 'Нет (false)';
}
function stageLabel(data: NonNullable<InspectionMaterialDetail['item']['data']>): string {
  if (data.stage === 'vibration_pause')
    return `Осмотр после превышения вибропорога № ${data.tripIndex}`;
  return {
    pre_test: 'Осмотр до испытания',
    post_trial_run: 'После опробования',
    post_rbd: 'Итоговый осмотр РБД',
    post_rpt: 'Итоговый осмотр РПТ',
    post_pmn: 'Итоговый осмотр ПМН',
  }[data.stage];
}
function References({
  references,
}: {
  readonly references: InspectionMaterialDetail['item']['references'];
}): React.JSX.Element {
  return (
    <ul>
      {references.map((reference, index) => (
        <li key={`${reference.kind}-${reference.materialId}-${index}`}>
          {reference.kind === 'photo' ? 'Фотография' : 'Осмотр'} {reference.materialId}:{' '}
          {
            {
              resolved: 'связь разрешена',
              unresolved: 'ссылка не разрешена',
              ambiguous: 'ссылка неоднозначна',
            }[reference.status]
          }
        </li>
      ))}
    </ul>
  );
}
function InspectionDetail({
  detail,
  availablePhotoIds,
  onOpen,
  blocked,
}: {
  readonly detail: InspectionMaterialDetail;
  readonly availablePhotoIds: readonly string[];
  readonly onOpen: (id: string) => void;
  readonly blocked: boolean;
}): React.JSX.Element {
  const data = detail.item.data;
  return (
    <section aria-label="Деталь осмотра">
      <Title order={5}>Осмотр {detail.item.materialId}</Title>
      <p>{stateLabels[detail.item.state]}</p>
      {data === null ? null : (
        <Rows
          entries={[
            ['Этап', `${stageLabel(data)} (${data.stage})`],
            ['Время UTC', data.performedAtUtc],
            ['Время от начала, с', data.runElapsedS],
            [
              'Номер превышения вибропорога',
              data.tripIndex === null ? 'Неприменимо (null)' : data.tripIndex,
            ],
            ['Сотрудник', data.actor.fullName],
            ['Должность', data.actor.position],
            ['Идентификатор сотрудника', data.actor.employeeId],
            ['Трещины', sourceBoolean(data.findings.cracks)],
            ['Сколы', sourceBoolean(data.findings.chips)],
            ['Деформация', sourceBoolean(data.findings.deformation)],
            ['Частичное разрушение', sourceBoolean(data.findings.partialDestruction)],
            ['Полное разрушение', sourceBoolean(data.findings.totalDestruction)],
            [
              'Балансировочные элементы',
              `${{ intact: 'Целы', damaged: 'Повреждены', not_assessed: 'Не оценены' }[data.findings.balancingElementsState]} (${data.findings.balancingElementsState})`,
            ],
            ['Прочие признаки', data.findings.otherFindings],
            [
              'Исходный результат осмотра',
              `${{ clear: 'Блокирующее повреждение не отмечено', blocking_damage: 'Блокирующее повреждение', inconclusive: 'Неопределённый результат' }[data.inspectionOutcome]} (${data.inspectionOutcome})`,
            ],
            ['Комментарий', data.comment],
          ]}
        />
      )}
      {detail.item.detail === null ? null : <p>{detail.item.detail}</p>}
      <References references={detail.item.references} />
      {detail.item.references
        .filter(
          (r) =>
            r.kind === 'photo' &&
            r.status === 'resolved' &&
            availablePhotoIds.includes(r.materialId),
        )
        .map((r) => (
          <Button
            key={r.materialId}
            variant="default"
            size="compact-sm"
            disabled={blocked || detail.item.state !== 'verified'}
            onClick={() => onOpen(r.materialId)}
          >
            Открыть фотографию {r.materialId}
          </Button>
        ))}
    </section>
  );
}
function Verification({
  verification,
}: {
  readonly verification: InspectionMaterialPage['verification'];
}): React.JSX.Element {
  return (
    <details>
      <summary>
        Текущая диагностика метаданных:{' '}
        {verification.semanticVerdict === 'passed' ? 'Пройдена' : 'Не пройдена'}; ошибок{' '}
        {verification.findingCounts.error}; предупреждений {verification.findingCounts.warning}
      </summary>
      <p>Источник правил: {verification.validationContractCommit}</p>
      <ul>
        {verification.findings.map((finding, index) => (
          <li key={index}>
            {finding.code}: {finding.message}
          </li>
        ))}
      </ul>
      {verification.findingCounts.truncated ? (
        <p>Список диагностик ограничен. Всего: {verification.findingCounts.total}.</p>
      ) : null}
    </details>
  );
}
