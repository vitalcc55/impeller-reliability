import { Button, Checkbox, Group, Select, Text, Textarea, TextInput, Title } from '@mantine/core';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';

import type {
  CompletenessWarning,
  CustomerDraft,
  CustomerProfile,
  DesktopError,
  ImpellerApi,
  Specimen,
  SpecimenDraft,
  SpecimenSummary,
  WheelModel,
  WheelModelDraft,
  WheelModelSummary,
} from '@impeller-reliability/contracts';

export type DossierSection = 'customer' | 'wheels' | 'specimens';

interface AnalystDossierProps {
  readonly desktopApi: ImpellerApi;
  readonly projectId: string;
  readonly section: DossierSection;
  readonly disabled: boolean;
  readonly onDirtyChange: (dirty: boolean) => void;
  readonly requestTransition: (dirty: boolean, action: () => void, discard: () => void) => void;
}

export interface AnalystDossierHandle {
  discardActiveDraft(): void;
  waitForPendingSave(): Promise<void>;
  verifyAfterReattach(): Promise<boolean>;
}

const emptyCustomer: CustomerDraft = {
  fullName: '',
  legalAddress: '',
  actualAddress: '',
  notes: '',
};
const emptyWheel: WheelModelDraft = {
  fullName: '',
  designation: '',
  nominalDiameterMm: null,
  nominalSpeedRpm: null,
  bladeCount: null,
  geometryDescription: '',
  compositionDescription: '',
  materialDescription: '',
  notes: '',
};
const emptySpecimen: SpecimenDraft = {
  wheelModelId: '00000000-0000-4000-8000-000000000000',
  identificationNumber: '',
  batchNumber: '',
  marking: '',
  manufacturedOn: null,
  receivedOn: null,
  workingDiameterMm: null,
  initialConditionNotes: '',
  notes: '',
};

export const AnalystDossier = forwardRef<AnalystDossierHandle, AnalystDossierProps>(
  function AnalystDossier(
    { desktopApi, projectId, section, disabled, onDirtyChange, requestTransition },
    ref,
  ): React.JSX.Element {
    const [customer, setCustomer] = useState<CustomerProfile | null>(null);
    const [customerDraft, setCustomerDraft] = useState<CustomerDraft>(emptyCustomer);
    const [wheels, setWheels] = useState<readonly WheelModelSummary[]>([]);
    const [wheel, setWheel] = useState<WheelModel | null>(null);
    const [wheelDraft, setWheelDraft] = useState<WheelModelDraft>(emptyWheel);
    const [wheelCreateId, setWheelCreateId] = useState(() => crypto.randomUUID());
    const [specimens, setSpecimens] = useState<readonly SpecimenSummary[]>([]);
    const [specimen, setSpecimen] = useState<Specimen | null>(null);
    const [specimenDraft, setSpecimenDraft] = useState<SpecimenDraft>(emptySpecimen);
    const [specimenBaseline, setSpecimenBaseline] = useState<SpecimenDraft>(emptySpecimen);
    const [specimenCreateId, setSpecimenCreateId] = useState(() => crypto.randomUUID());
    const [includeArchived, setIncludeArchived] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [confirmArchiveKey, setConfirmArchiveKey] = useState<string | null>(null);
    const [loadedKey, setLoadedKey] = useState<string | null>(null);
    const pendingSaveRef = useRef<Promise<void> | null>(null);
    const selectionRevisionRef = useRef(0);
    const loadKey = `${projectId}:${section}:${includeArchived ? 'archived' : 'active'}`;

    const customerDirty = !sameCustomer(customerDraft, customer);
    const wheelDirty = !sameWheel(wheelDraft, wheel);
    const specimenDirty = JSON.stringify(specimenDraft) !== JSON.stringify(specimenBaseline);
    const activeDirty =
      section === 'customer' ? customerDirty : section === 'wheels' ? wheelDirty : specimenDirty;
    const activeWheels = wheels.filter((item) => item.archivedAtUtc === null);

    useEffect(() => onDirtyChange(activeDirty), [activeDirty, onDirtyChange]);

    const loadCustomer = useCallback(async (): Promise<void> => {
      const result = await desktopApi.caseCustomer.get();
      if (!result.ok) return setError(result.error);
      setCustomer(result.result);
      setCustomerDraft(result.result === null ? emptyCustomer : customerToDraft(result.result));
    }, [desktopApi]);
    const loadWheels = useCallback(async (): Promise<void> => {
      const result = await desktopApi.wheelModel.list(includeArchived);
      if (!result.ok) return setError(result.error);
      setWheels(result.result);
    }, [desktopApi, includeArchived]);
    const loadSpecimens = useCallback(async (): Promise<void> => {
      const result = await desktopApi.specimen.list(includeArchived);
      if (!result.ok) return setError(result.error);
      setSpecimens(result.result);
    }, [desktopApi, includeArchived]);

    useEffect(() => {
      const timer = window.setTimeout(() => {
        const load =
          section === 'customer'
            ? loadCustomer()
            : section === 'wheels'
              ? loadWheels()
              : Promise.all([loadWheels(), loadSpecimens()]).then(() => undefined);
        void load.catch(() => setError(unavailableError())).finally(() => setLoadedKey(loadKey));
      }, 0);
      return () => window.clearTimeout(timer);
    }, [loadCustomer, loadKey, loadSpecimens, loadWheels, section]);

    const run = async (key: string, action: () => Promise<void>): Promise<void> => {
      setBusy(key);
      setError(null);
      setMessage(null);
      try {
        await action();
      } catch {
        setError(unavailableError());
      } finally {
        setBusy(null);
      }
    };
    const trackSave = (operation: Promise<void>): Promise<void> => {
      pendingSaveRef.current = operation;
      void operation.finally(() => {
        if (pendingSaveRef.current === operation) pendingSaveRef.current = null;
      });
      return operation;
    };

    const saveCustomer = (): Promise<void> =>
      trackSave(
        run('customer-save', async () => {
          const result = await desktopApi.caseCustomer.upsert({
            expectedRevision: customer?.recordRevision ?? null,
            customer: customerDraft,
          });
          if (!result.ok) return setError(result.error);
          setCustomer(result.result);
          setCustomerDraft(customerToDraft(result.result));
          setMessage(
            `Сведения заказчика сохранены. Редакция ${String(result.result.recordRevision)}.`,
          );
        }),
      );

    const selectWheel = (wheelModelId: string | null): void => {
      if (wheelModelId === null) return;
      requestTransition(
        wheelDirty,
        () => {
          const selectionRevision = ++selectionRevisionRef.current;
          setConfirmArchiveKey(null);
          void (async () => {
            try {
              const result = await desktopApi.wheelModel.get(wheelModelId);
              if (selectionRevision !== selectionRevisionRef.current) return;
              if (!result.ok) return setError(result.error);
              setWheel(result.result);
              setWheelDraft(wheelToDraft(result.result));
            } catch {
              if (selectionRevision === selectionRevisionRef.current) {
                setError(unavailableError());
              }
            }
          })();
        },
        () => setWheelDraft(wheel === null ? emptyWheel : wheelToDraft(wheel)),
      );
    };
    const newWheel = (): void => {
      requestTransition(
        wheelDirty,
        () => {
          selectionRevisionRef.current += 1;
          setConfirmArchiveKey(null);
          setWheel(null);
          setWheelDraft(emptyWheel);
          setWheelCreateId(crypto.randomUUID());
          setMessage(null);
          setError(null);
        },
        () => setWheelDraft(wheel === null ? emptyWheel : wheelToDraft(wheel)),
      );
    };
    const saveWheel = (): Promise<void> =>
      trackSave(
        run('wheel-save', async () => {
          const result =
            wheel === null
              ? await desktopApi.wheelModel.create({
                  wheelModelId: wheelCreateId,
                  ...wheelDraft,
                })
              : await desktopApi.wheelModel.update({
                  wheelModelId: wheel.wheelModelId,
                  expectedRevision: wheel.recordRevision,
                  wheelModel: wheelDraft,
                });
          if (!result.ok) return setError(result.error);
          setWheel(result.result);
          setWheelDraft(wheelToDraft(result.result));
          await loadWheels();
          setMessage(`Модель сохранена. Редакция ${String(result.result.recordRevision)}.`);
        }),
      );
    const toggleWheelArchive = (): void => {
      if (wheel === null) return;
      const confirmationKey = `wheel:${wheel.wheelModelId}:${wheel.archivedAtUtc === null ? 'archive' : 'restore'}`;
      if (confirmArchiveKey !== confirmationKey) {
        setConfirmArchiveKey(confirmationKey);
        return;
      }
      requestTransition(
        wheelDirty,
        () =>
          void run('wheel-archive', async () => {
            const command = {
              wheelModelId: wheel.wheelModelId,
              expectedRevision: wheel.recordRevision,
            };
            const result =
              wheel.archivedAtUtc === null
                ? await desktopApi.wheelModel.archive(command)
                : await desktopApi.wheelModel.restore(command);
            if (!result.ok) return setError(result.error);
            setWheel(result.result);
            setWheelDraft(wheelToDraft(result.result));
            setConfirmArchiveKey(null);
            await loadWheels();
          }),
        () => setWheelDraft(wheelToDraft(wheel)),
      );
    };

    const selectSpecimen = (specimenId: string | null): void => {
      if (specimenId === null) return;
      requestTransition(
        specimenDirty,
        () => {
          const selectionRevision = ++selectionRevisionRef.current;
          setConfirmArchiveKey(null);
          void (async () => {
            try {
              const result = await desktopApi.specimen.get(specimenId);
              if (selectionRevision !== selectionRevisionRef.current) return;
              if (!result.ok) return setError(result.error);
              setSpecimen(result.result);
              const draft = specimenToDraft(result.result);
              setSpecimenDraft(draft);
              setSpecimenBaseline(draft);
            } catch {
              if (selectionRevision === selectionRevisionRef.current) {
                setError(unavailableError());
              }
            }
          })();
        },
        () => setSpecimenDraft(specimenBaseline),
      );
    };
    const newSpecimen = (): void => {
      const firstModel = wheels.find((item) => item.archivedAtUtc === null);
      requestTransition(
        specimenDirty,
        () => {
          selectionRevisionRef.current += 1;
          setConfirmArchiveKey(null);
          setSpecimen(null);
          const draft = {
            ...emptySpecimen,
            wheelModelId: firstModel?.wheelModelId ?? emptySpecimen.wheelModelId,
          };
          setSpecimenDraft(draft);
          setSpecimenBaseline(draft);
          setSpecimenCreateId(crypto.randomUUID());
          setMessage(null);
          setError(null);
        },
        () => setSpecimenDraft(specimenBaseline),
      );
    };
    const saveSpecimen = (): Promise<void> =>
      trackSave(
        run('specimen-save', async () => {
          const result =
            specimen === null
              ? await desktopApi.specimen.create({
                  specimenId: specimenCreateId,
                  ...specimenDraft,
                })
              : await desktopApi.specimen.update({
                  specimenId: specimen.specimenId,
                  expectedRevision: specimen.recordRevision,
                  specimen: specimenDraft,
                });
          if (!result.ok) return setError(result.error);
          setSpecimen(result.result);
          const draft = specimenToDraft(result.result);
          setSpecimenDraft(draft);
          setSpecimenBaseline(draft);
          await loadSpecimens();
          setMessage(`Образец сохранён. Редакция ${String(result.result.recordRevision)}.`);
        }),
      );
    const toggleSpecimenArchive = (): void => {
      if (specimen === null) return;
      const confirmationKey = `specimen:${specimen.specimenId}:${specimen.archivedAtUtc === null ? 'archive' : 'restore'}`;
      if (confirmArchiveKey !== confirmationKey) {
        setConfirmArchiveKey(confirmationKey);
        return;
      }
      requestTransition(
        specimenDirty,
        () =>
          void run('specimen-archive', async () => {
            const command = {
              specimenId: specimen.specimenId,
              expectedRevision: specimen.recordRevision,
            };
            const result =
              specimen.archivedAtUtc === null
                ? await desktopApi.specimen.archive(command)
                : await desktopApi.specimen.restore(command);
            if (!result.ok) return setError(result.error);
            setSpecimen(result.result);
            const draft = specimenToDraft(result.result);
            setSpecimenDraft(draft);
            setSpecimenBaseline(draft);
            setConfirmArchiveKey(null);
            await loadSpecimens();
          }),
        () => setSpecimenDraft(specimenBaseline),
      );
    };

    useImperativeHandle(
      ref,
      () => ({
        discardActiveDraft: () => {
          if (section === 'customer')
            setCustomerDraft(customer === null ? emptyCustomer : customerToDraft(customer));
          else if (section === 'wheels')
            setWheelDraft(wheel === null ? emptyWheel : wheelToDraft(wheel));
          else setSpecimenDraft(specimenBaseline);
        },
        waitForPendingSave: async () => {
          if (pendingSaveRef.current !== null) await pendingSaveRef.current;
        },
        verifyAfterReattach: async () => {
          if (!activeDirty) return true;
          if (section === 'customer') {
            const result = await desktopApi.caseCustomer.get();
            return result.ok && result.result?.recordRevision === customer?.recordRevision;
          }
          if (section === 'wheels' && wheel !== null) {
            const result = await desktopApi.wheelModel.get(wheel.wheelModelId);
            return result.ok && result.result.recordRevision === wheel.recordRevision;
          }
          if (section === 'specimens' && specimen !== null) {
            const result = await desktopApi.specimen.get(specimen.specimenId);
            return result.ok && result.result.recordRevision === specimen.recordRevision;
          }
          return true;
        },
      }),
      [activeDirty, customer, desktopApi, section, specimen, specimenBaseline, wheel],
    );

    return (
      <section className="project-surface dossier-surface" aria-labelledby="dossier-title">
        <div className="section-heading">
          <Title id="dossier-title" order={2}>
            {section === 'customer'
              ? 'Заказчик'
              : section === 'wheels'
                ? 'Модели рабочих колёс'
                : 'Физические образцы'}
          </Title>
          <Text>
            Это редактируемые сведения аналитического дела. После появления импорта исходные
            значения R130SH будут храниться отдельно.
          </Text>
        </div>
        <DossierFeedback message={message} error={error} />
        {loadedKey !== loadKey ? (
          <div className="dossier-loading" role="status">
            Загрузка сведений дела…
          </div>
        ) : section === 'customer' ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void saveCustomer();
            }}
          >
            <div className="dossier-form dossier-form--two-columns">
              <TextInput
                label="Полное наименование"
                required
                value={customerDraft.fullName}
                disabled={disabled || busy !== null}
                onChange={(event) =>
                  setCustomerDraft({ ...customerDraft, fullName: event.currentTarget.value })
                }
              />
              <TextInput
                label="Юридический адрес"
                value={customerDraft.legalAddress}
                disabled={disabled || busy !== null}
                onChange={(event) =>
                  setCustomerDraft({ ...customerDraft, legalAddress: event.currentTarget.value })
                }
              />
              <TextInput
                label="Фактический адрес"
                value={customerDraft.actualAddress}
                disabled={disabled || busy !== null}
                onChange={(event) =>
                  setCustomerDraft({ ...customerDraft, actualAddress: event.currentTarget.value })
                }
              />
              <Textarea
                className="dossier-span"
                label="Примечания"
                minRows={3}
                value={customerDraft.notes}
                disabled={disabled || busy !== null}
                onChange={(event) =>
                  setCustomerDraft({ ...customerDraft, notes: event.currentTarget.value })
                }
              />
            </div>
            <Warnings warnings={customer?.warnings ?? ['customer_address_missing']} />
            <FormActions
              label="Сохранить заказчика"
              revision={customer?.recordRevision ?? null}
              loading={busy === 'customer-save'}
              disabled={disabled || busy !== null || customerDraft.fullName.trim() === ''}
            />
          </form>
        ) : section === 'wheels' ? (
          <div className="dossier-editor">
            <DossierList
              title="Каталог моделей"
              items={wheels.map((item) => ({
                id: item.wheelModelId,
                title: item.fullName,
                subtitle: item.designation || 'Без обозначения',
                archived: item.archivedAtUtc !== null,
              }))}
              selectedId={wheel?.wheelModelId ?? null}
              busy={busy}
              disabled={disabled}
              onSelect={selectWheel}
              onCreate={newWheel}
              includeArchived={includeArchived}
              onIncludeArchived={setIncludeArchived}
              createLabel="Новая модель"
            />
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void saveWheel();
              }}
              className="dossier-detail"
            >
              <div className="dossier-form dossier-form--two-columns">
                <TextInput
                  label="Полное наименование"
                  required
                  value={wheelDraft.fullName}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({ ...wheelDraft, fullName: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Обозначение"
                  value={wheelDraft.designation}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({ ...wheelDraft, designation: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Номинальный диаметр"
                  description="мм"
                  value={wheelDraft.nominalDiameterMm ?? ''}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({ ...wheelDraft, nominalDiameterMm: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Номинальная частота вращения"
                  description="об/мин"
                  value={wheelDraft.nominalSpeedRpm?.toString() ?? ''}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({
                      ...wheelDraft,
                      nominalSpeedRpm: positiveIntegerOrNull(event.currentTarget.value),
                    })
                  }
                />
                <TextInput
                  label="Количество лопастей"
                  value={wheelDraft.bladeCount?.toString() ?? ''}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({
                      ...wheelDraft,
                      bladeCount: positiveIntegerOrNull(event.currentTarget.value),
                    })
                  }
                />
                <Textarea
                  label="Геометрия"
                  value={wheelDraft.geometryDescription}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({ ...wheelDraft, geometryDescription: event.currentTarget.value })
                  }
                />
                <Textarea
                  label="Состав"
                  value={wheelDraft.compositionDescription}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({
                      ...wheelDraft,
                      compositionDescription: event.currentTarget.value,
                    })
                  }
                />
                <Textarea
                  label="Материал"
                  value={wheelDraft.materialDescription}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({ ...wheelDraft, materialDescription: event.currentTarget.value })
                  }
                />
                <Textarea
                  className="dossier-span"
                  label="Примечания"
                  value={wheelDraft.notes}
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setWheelDraft({ ...wheelDraft, notes: event.currentTarget.value })
                  }
                />
              </div>
              <Warnings
                warnings={
                  wheel?.warnings ?? [
                    'wheel_nominal_diameter_missing',
                    'wheel_nominal_speed_missing',
                  ]
                }
              />
              <Group className="form-actions">
                <Button
                  type="submit"
                  loading={busy === 'wheel-save'}
                  disabled={
                    disabled ||
                    busy !== null ||
                    wheelDraft.fullName.trim() === '' ||
                    (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                >
                  Сохранить модель
                </Button>
                {wheel !== null ? (
                  <Button
                    className="danger-action"
                    variant={
                      confirmArchiveKey?.startsWith(`wheel:${wheel.wheelModelId}:`)
                        ? 'filled'
                        : 'subtle'
                    }
                    loading={busy === 'wheel-archive'}
                    disabled={disabled || busy !== null}
                    onClick={toggleWheelArchive}
                  >
                    {confirmArchiveKey?.startsWith(`wheel:${wheel.wheelModelId}:`)
                      ? 'Подтвердить действие'
                      : wheel.archivedAtUtc === null
                        ? 'Архивировать'
                        : 'Восстановить'}
                  </Button>
                ) : null}
                <Text size="sm">Редакция: {wheel?.recordRevision ?? 'новая запись'}</Text>
              </Group>
            </form>
          </div>
        ) : (
          <div className="dossier-editor">
            <DossierList
              title="Реестр образцов"
              items={specimens.map((item) => ({
                id: item.specimenId,
                title: item.identificationNumber,
                subtitle: item.wheelModelName,
                archived: item.archivedAtUtc !== null,
              }))}
              selectedId={specimen?.specimenId ?? null}
              busy={busy}
              disabled={disabled}
              onSelect={selectSpecimen}
              onCreate={newSpecimen}
              includeArchived={includeArchived}
              onIncludeArchived={setIncludeArchived}
              createLabel="Новый образец"
              createDisabled={activeWheels.length === 0}
            />
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void saveSpecimen();
              }}
              className="dossier-detail"
            >
              {activeWheels.length === 0 ? (
                <div className="dossier-prerequisite" role="status">
                  <strong>Сначала создайте модель рабочего колеса</strong>
                  <span>Новый образец всегда относится к одной неархивной модели.</span>
                </div>
              ) : null}
              <div className="dossier-form dossier-form--two-columns">
                <Select
                  label="Модель рабочего колеса"
                  required
                  data={activeWheels.map((item) => ({
                    value: item.wheelModelId,
                    label:
                      item.designation === ''
                        ? item.fullName
                        : `${item.fullName} · ${item.designation}`,
                  }))}
                  value={specimenDraft.wheelModelId}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(value) => {
                    if (value !== null) setSpecimenDraft({ ...specimenDraft, wheelModelId: value });
                  }}
                />
                <TextInput
                  label="Идентификационный номер"
                  required
                  value={specimenDraft.identificationNumber}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({
                      ...specimenDraft,
                      identificationNumber: event.currentTarget.value,
                    })
                  }
                />
                <TextInput
                  label="Номер партии"
                  value={specimenDraft.batchNumber}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({ ...specimenDraft, batchNumber: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Маркировка"
                  value={specimenDraft.marking}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({ ...specimenDraft, marking: event.currentTarget.value })
                  }
                />
                <TextInput
                  type="date"
                  label="Дата изготовления"
                  value={specimenDraft.manufacturedOn ?? ''}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({
                      ...specimenDraft,
                      manufacturedOn: event.currentTarget.value,
                    })
                  }
                />
                <TextInput
                  type="date"
                  label="Дата поступления"
                  value={specimenDraft.receivedOn ?? ''}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({ ...specimenDraft, receivedOn: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Рабочий диаметр"
                  description="мм"
                  value={specimenDraft.workingDiameterMm ?? ''}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({
                      ...specimenDraft,
                      workingDiameterMm: event.currentTarget.value,
                    })
                  }
                />
                <Textarea
                  label="Состояние при поступлении"
                  value={specimenDraft.initialConditionNotes}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({
                      ...specimenDraft,
                      initialConditionNotes: event.currentTarget.value,
                    })
                  }
                />
                <Textarea
                  className="dossier-span"
                  label="Примечания"
                  value={specimenDraft.notes}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (specimen !== null && specimen.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setSpecimenDraft({ ...specimenDraft, notes: event.currentTarget.value })
                  }
                />
              </div>
              <Warnings warnings={specimen?.warnings ?? ['specimen_working_diameter_missing']} />
              <Group className="form-actions">
                <Button
                  type="submit"
                  loading={busy === 'specimen-save'}
                  disabled={
                    disabled ||
                    busy !== null ||
                    specimenDraft.identificationNumber.trim() === '' ||
                    !activeWheels.some(
                      (item) =>
                        item.wheelModelId === specimenDraft.wheelModelId &&
                        item.archivedAtUtc === null,
                    )
                  }
                >
                  Сохранить образец
                </Button>
                {specimen !== null ? (
                  <Button
                    className="danger-action"
                    variant={
                      confirmArchiveKey?.startsWith(`specimen:${specimen.specimenId}:`)
                        ? 'filled'
                        : 'subtle'
                    }
                    loading={busy === 'specimen-archive'}
                    disabled={disabled || busy !== null}
                    onClick={toggleSpecimenArchive}
                  >
                    {confirmArchiveKey?.startsWith(`specimen:${specimen.specimenId}:`)
                      ? 'Подтвердить действие'
                      : specimen.archivedAtUtc === null
                        ? 'Архивировать'
                        : 'Восстановить'}
                  </Button>
                ) : null}
                <Text size="sm">Редакция: {specimen?.recordRevision ?? 'новая запись'}</Text>
              </Group>
            </form>
          </div>
        )}
      </section>
    );
  },
);

function DossierList({
  title,
  items,
  selectedId,
  busy,
  disabled,
  onSelect,
  onCreate,
  includeArchived,
  onIncludeArchived,
  createLabel,
  createDisabled = false,
}: {
  readonly title: string;
  readonly items: readonly { id: string; title: string; subtitle: string; archived: boolean }[];
  readonly selectedId: string | null;
  readonly busy: string | null;
  readonly disabled: boolean;
  readonly onSelect: (id: string) => void;
  readonly onCreate: () => void;
  readonly includeArchived: boolean;
  readonly onIncludeArchived: (value: boolean) => void;
  readonly createLabel: string;
  readonly createDisabled?: boolean;
}): React.JSX.Element {
  return (
    <aside className="dossier-list" aria-label={title}>
      <Group justify="space-between">
        <Title order={3}>{title}</Title>
        <Button
          size="compact-sm"
          variant="subtle"
          disabled={disabled || busy !== null || createDisabled}
          onClick={onCreate}
        >
          {createLabel}
        </Button>
      </Group>
      <Checkbox
        label="Показывать архивные"
        checked={includeArchived}
        disabled={disabled || busy !== null}
        onChange={(event) => onIncludeArchived(event.currentTarget.checked)}
      />
      {items.length === 0 ? (
        <div className="dossier-empty">
          <Text fw={650}>Записей пока нет</Text>
          <Text size="sm">Создайте первую запись для этого дела.</Text>
        </div>
      ) : (
        <div className="dossier-list-rows">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className="dossier-list-row"
              data-active={selectedId === item.id}
              aria-pressed={selectedId === item.id}
              aria-busy={busy !== null}
              disabled={disabled}
              onClick={() => {
                if (busy === null) onSelect(item.id);
              }}
            >
              <strong>{item.title}</strong>
              <span>{item.subtitle}</span>
              {item.archived ? <small>Архив</small> : null}
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}

function FormActions({
  label,
  revision,
  loading,
  disabled,
}: {
  readonly label: string;
  readonly revision: number | null;
  readonly loading: boolean;
  readonly disabled: boolean;
}): React.JSX.Element {
  return (
    <Group className="form-actions">
      <Button type="submit" loading={loading} disabled={disabled}>
        {label}
      </Button>
      <Text size="sm">Редакция: {revision ?? 'новая запись'}</Text>
    </Group>
  );
}

const warningText: Readonly<Record<CompletenessWarning, string>> = {
  customer_address_missing: 'Не заполнен юридический или фактический адрес.',
  wheel_nominal_diameter_missing: 'Не указан номинальный диаметр модели.',
  wheel_nominal_speed_missing: 'Не указана номинальная частота вращения.',
  specimen_working_diameter_missing: 'Не указан рабочий диаметр образца.',
};

function Warnings({
  warnings,
}: {
  readonly warnings: readonly CompletenessWarning[];
}): React.JSX.Element | null {
  if (warnings.length === 0) return null;
  return (
    <div className="dossier-warnings" role="status">
      <strong>Сведения можно сохранить, но они пока неполные</strong>
      {warnings.map((warning) => (
        <span key={warning}>{warningText[warning]}</span>
      ))}
    </div>
  );
}

function DossierFeedback({
  message,
  error,
}: {
  readonly message: string | null;
  readonly error: DesktopError | null;
}): React.JSX.Element | null {
  if (error !== null)
    return (
      <div className="feedback feedback--error" role="alert">
        <strong>Операция не выполнена</strong>
        <span>{error.message}</span>
      </div>
    );
  return message === null ? null : (
    <div className="feedback" role="status">
      <span>{message}</span>
    </div>
  );
}

function customerToDraft(value: CustomerProfile): CustomerDraft {
  return {
    fullName: value.fullName,
    legalAddress: value.legalAddress,
    actualAddress: value.actualAddress,
    notes: value.notes,
  };
}
function wheelToDraft(value: WheelModel): WheelModelDraft {
  return {
    fullName: value.fullName,
    designation: value.designation,
    nominalDiameterMm: value.nominalDiameterMm,
    nominalSpeedRpm: value.nominalSpeedRpm,
    bladeCount: value.bladeCount,
    geometryDescription: value.geometryDescription,
    compositionDescription: value.compositionDescription,
    materialDescription: value.materialDescription,
    notes: value.notes,
  };
}
function specimenToDraft(value: Specimen): SpecimenDraft {
  return {
    wheelModelId: value.wheelModelId,
    identificationNumber: value.identificationNumber,
    batchNumber: value.batchNumber,
    marking: value.marking,
    manufacturedOn: value.manufacturedOn,
    receivedOn: value.receivedOn,
    workingDiameterMm: value.workingDiameterMm,
    initialConditionNotes: value.initialConditionNotes,
    notes: value.notes,
  };
}
function sameCustomer(draft: CustomerDraft, value: CustomerProfile | null): boolean {
  return value === null
    ? draft.fullName === '' &&
        draft.legalAddress === '' &&
        draft.actualAddress === '' &&
        draft.notes === ''
    : JSON.stringify(draft) === JSON.stringify(customerToDraft(value));
}
function sameWheel(draft: WheelModelDraft, value: WheelModel | null): boolean {
  return value === null
    ? JSON.stringify(draft) === JSON.stringify(emptyWheel)
    : JSON.stringify(draft) === JSON.stringify(wheelToDraft(value));
}
function positiveIntegerOrNull(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}
function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Локальный worker недоступен. Перезапустите ядро и повторите операцию.',
    details: {},
    retryable: true,
  };
}
