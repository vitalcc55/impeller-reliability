import { Button, Checkbox, Group, Select, Text, Textarea, TextInput, Title } from '@mantine/core';
import {
  forwardRef,
  type ReactNode,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import type {
  CaseDocument,
  CaseDocumentDraft,
  CaseDocumentKind,
  CaseDocumentSummary,
  CaseDocumentWarning,
  CompletenessWarning,
  CustomerDraft,
  CustomerProfile,
  DesktopError,
  ImpellerApi,
  ReliabilityExecution,
  Specimen,
  SpecimenDraft,
  SpecimenSummary,
  WheelModel,
  WheelModelDraft,
  WheelModelSummary,
} from '@impeller-reliability/contracts';
import { caseDocumentKindSchema } from '@impeller-reliability/contracts';

import {
  formatOptionalPositiveInteger,
  parseOptionalPositiveInteger,
} from './optional-positive-integer';
import { decideReattachEntity } from './reattach-reconciliation';

export type DossierSection = 'customer' | 'wheels' | 'specimens' | 'documents';

interface AnalystDossierProps {
  readonly desktopApi: ImpellerApi;
  readonly projectId: string;
  readonly section: DossierSection;
  readonly disabled: boolean;
  readonly onDirtyChange: (dirty: boolean) => void;
  readonly onPendingChange: (pending: boolean) => void;
  readonly requestTransition: (dirty: boolean, action: () => void, discard: () => void) => void;
}

export interface AnalystDossierHandle {
  discardActiveDraft(): void;
  waitForPendingSave(): Promise<void>;
  verifyAfterReattach(): Promise<DossierReattachResult>;
}

export type DossierReattachResult =
  | { readonly status: 'reconciled' }
  | { readonly status: 'conflict' }
  | { readonly status: 'error'; readonly error: DesktopError };

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
const emptyDocument: CaseDocumentDraft = {
  documentKind: 'other',
  title: '',
  designation: '',
  revisionLabel: '',
  documentDate: null,
  issuer: '',
  notes: '',
};
const documentKindLabel: Readonly<Record<CaseDocumentKind, string>> = {
  technical_specification: 'Технические условия',
  individual_test_method: 'Индивидуальная ПМИ',
  typical_test_method: 'Типовая ПМИ',
  customer_requirement: 'Требования заказчика',
  test_request: 'Заявка на испытание',
  operational_documentation: 'Эксплуатационная документация',
  standard: 'Нормативный документ',
  drawing: 'Чертёж или конструкторский документ',
  measurement_or_attestation_record: 'Поверка или аттестация',
  other: 'Иной материал дела',
};
const documentKindOptions = Object.entries(documentKindLabel).map(([value, label]) => ({
  value,
  label,
}));

export const AnalystDossier = forwardRef<AnalystDossierHandle, AnalystDossierProps>(
  function AnalystDossier(
    { desktopApi, projectId, section, disabled, onDirtyChange, onPendingChange, requestTransition },
    ref,
  ): React.JSX.Element {
    const [customer, setCustomer] = useState<CustomerProfile | null>(null);
    const [customerDraft, setCustomerDraft] = useState<CustomerDraft>(emptyCustomer);
    const [wheels, setWheels] = useState<readonly WheelModelSummary[]>([]);
    const [wheel, setWheel] = useState<WheelModel | null>(null);
    const [wheelDraft, setWheelDraft] = useState<WheelModelDraft>(emptyWheel);
    const [reliabilityExecutions, setReliabilityExecutions] = useState<
      readonly ReliabilityExecution[]
    >([]);
    const [nominalSpeedInput, setNominalSpeedInput] = useState('');
    const [bladeCountInput, setBladeCountInput] = useState('');
    const [wheelCreateId, setWheelCreateId] = useState(() => crypto.randomUUID());
    const [specimens, setSpecimens] = useState<readonly SpecimenSummary[]>([]);
    const [specimen, setSpecimen] = useState<Specimen | null>(null);
    const [specimenDraft, setSpecimenDraft] = useState<SpecimenDraft>(emptySpecimen);
    const [specimenBaseline, setSpecimenBaseline] = useState<SpecimenDraft>(emptySpecimen);
    const [specimenCreateId, setSpecimenCreateId] = useState(() => crypto.randomUUID());
    const [documents, setDocuments] = useState<readonly CaseDocumentSummary[]>([]);
    const [document, setDocument] = useState<CaseDocument | null>(null);
    const [documentDraft, setDocumentDraft] = useState<CaseDocumentDraft>(emptyDocument);
    const [documentBaseline, setDocumentBaseline] = useState<CaseDocumentDraft>(emptyDocument);
    const [documentWheelIds, setDocumentWheelIds] = useState<readonly string[]>([]);
    const [documentSpecimenIds, setDocumentSpecimenIds] = useState<readonly string[]>([]);
    const [documentBaselineWheelIds, setDocumentBaselineWheelIds] = useState<readonly string[]>([]);
    const [documentBaselineSpecimenIds, setDocumentBaselineSpecimenIds] = useState<
      readonly string[]
    >([]);
    const [documentCreateId, setDocumentCreateId] = useState(() => crypto.randomUUID());
    const [documentKindFilter, setDocumentKindFilter] = useState<CaseDocumentKind | null>(null);
    const wheelNameRef = useRef<HTMLInputElement>(null);
    const specimenNumberRef = useRef<HTMLInputElement>(null);
    const documentTitleRef = useRef<HTMLInputElement>(null);
    const [includeArchived, setIncludeArchived] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<DesktopError | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [confirmArchiveKey, setConfirmArchiveKey] = useState<string | null>(null);
    const [loadedKey, setLoadedKey] = useState<string | null>(null);
    const pendingSaveRef = useRef<Promise<void> | null>(null);
    const selectionRevisionRef = useRef(0);
    const loadRevisionRef = useRef(0);
    const loadKey = `${projectId}:${section}:${includeArchived ? 'archived' : 'active'}:${
      section === 'documents' ? (documentKindFilter ?? 'all') : 'all'
    }`;

    const customerDirty = !sameCustomer(customerDraft, customer);
    const nominalSpeed = parseOptionalPositiveInteger(nominalSpeedInput);
    const bladeCount = parseOptionalPositiveInteger(bladeCountInput);
    const hasInvalidIntegerInput = nominalSpeed.kind === 'invalid' || bladeCount.kind === 'invalid';
    const wheelDirty =
      !sameWheel(wheelDraft, wheel) ||
      nominalSpeedInput !== formatOptionalPositiveInteger(wheel?.nominalSpeedRpm ?? null) ||
      bladeCountInput !== formatOptionalPositiveInteger(wheel?.bladeCount ?? null);
    const specimenDirty = JSON.stringify(specimenDraft) !== JSON.stringify(specimenBaseline);
    const documentDirty =
      JSON.stringify(documentDraft) !== JSON.stringify(documentBaseline) ||
      JSON.stringify(documentWheelIds) !== JSON.stringify(documentBaselineWheelIds) ||
      JSON.stringify(documentSpecimenIds) !== JSON.stringify(documentBaselineSpecimenIds);
    const activeDirty =
      section === 'customer'
        ? customerDirty
        : section === 'wheels'
          ? wheelDirty
          : section === 'specimens'
            ? specimenDirty
            : documentDirty;
    const activeWheels = wheels.filter((item) => item.archivedAtUtc === null);

    useEffect(() => onDirtyChange(activeDirty), [activeDirty, onDirtyChange]);
    useEffect(() => {
      onPendingChange(busy !== null);
      return () => onPendingChange(false);
    }, [busy, onPendingChange]);

    const loadCustomer = useCallback(
      async (loadRevision?: number): Promise<void> => {
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        const result = await desktopApi.caseCustomer.get();
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        if (!result.ok) return setError(result.error);
        setCustomer(result.result);
        setCustomerDraft(result.result === null ? emptyCustomer : customerToDraft(result.result));
      },
      [desktopApi],
    );
    const loadWheels = useCallback(
      async (loadRevision?: number): Promise<void> => {
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        const result = await desktopApi.wheelModel.list(includeArchived);
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        if (!result.ok) return setError(result.error);
        setWheels(result.result);
      },
      [desktopApi, includeArchived],
    );
    const loadSpecimens = useCallback(
      async (loadRevision?: number): Promise<void> => {
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        const result = await desktopApi.specimen.list(includeArchived);
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        if (!result.ok) return setError(result.error);
        setSpecimens(result.result);
      },
      [desktopApi, includeArchived],
    );
    const loadDocuments = useCallback(
      async (loadRevision?: number): Promise<void> => {
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        const result = await desktopApi.caseDocument.list({
          includeArchived,
          documentKind: documentKindFilter,
        });
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        if (!result.ok) return setError(result.error);
        setDocuments(result.result);
      },
      [desktopApi, documentKindFilter, includeArchived],
    );
    const loadDocumentTargets = useCallback(
      async (loadRevision?: number): Promise<void> => {
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        const wheelResult = await desktopApi.wheelModel.list(true);
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        if (!wheelResult.ok) return setError(wheelResult.error);
        const specimenResult = await desktopApi.specimen.list(true);
        if (loadRevision !== undefined && loadRevision !== loadRevisionRef.current) return;
        if (!specimenResult.ok) return setError(specimenResult.error);
        setWheels(wheelResult.result);
        setSpecimens(specimenResult.result);
      },
      [desktopApi],
    );

    useEffect(() => {
      const loadRevision = ++loadRevisionRef.current;
      const timer = window.setTimeout(() => {
        const load =
          section === 'customer'
            ? loadCustomer(loadRevision)
            : section === 'wheels'
              ? loadWheels(loadRevision)
              : section === 'specimens'
                ? loadWheels(loadRevision).then(() => loadSpecimens(loadRevision))
                : loadDocumentTargets(loadRevision).then(() => loadDocuments(loadRevision));
        void load
          .catch(() => {
            if (loadRevision === loadRevisionRef.current) setError(unavailableError());
          })
          .finally(() => {
            if (loadRevision === loadRevisionRef.current) setLoadedKey(loadKey);
          });
      }, 0);
      return () => {
        window.clearTimeout(timer);
        if (loadRevision === loadRevisionRef.current) loadRevisionRef.current += 1;
      };
    }, [
      loadCustomer,
      loadDocuments,
      loadDocumentTargets,
      loadKey,
      loadSpecimens,
      loadWheels,
      section,
    ]);

    useEffect(() => {
      if (section !== 'wheels' || wheel === null) return;
      let active = true;
      void desktopApi.reliabilityExecution.listByWheel(wheel.wheelModelId).then((result) => {
        if (!active) return;
        if (!result.ok) return setError(result.error);
        setReliabilityExecutions(result.result);
      });
      return () => {
        active = false;
      };
    }, [desktopApi, section, wheel]);

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
    const resetWheelDraft = useCallback((value: WheelModel | null): void => {
      setWheelDraft(value === null ? emptyWheel : wheelToDraft(value));
      setNominalSpeedInput(formatOptionalPositiveInteger(value?.nominalSpeedRpm ?? null));
      setBladeCountInput(formatOptionalPositiveInteger(value?.bladeCount ?? null));
    }, []);
    const resetDocumentDraft = useCallback((value: CaseDocument | null): void => {
      const draft = value === null ? emptyDocument : documentToDraft(value);
      const wheelIds = value?.wheelModelIds ?? [];
      const specimenIds = value?.specimenIds ?? [];
      setDocumentDraft(draft);
      setDocumentBaseline(draft);
      setDocumentWheelIds(wheelIds);
      setDocumentBaselineWheelIds(wheelIds);
      setDocumentSpecimenIds(specimenIds);
      setDocumentBaselineSpecimenIds(specimenIds);
    }, []);

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
          void run('wheel-load', async () => {
            const result = await desktopApi.wheelModel.get(wheelModelId);
            if (selectionRevision !== selectionRevisionRef.current) return;
            if (!result.ok) return setError(result.error);
            setWheel(result.result);
            setWheelDraft(wheelToDraft(result.result));
            setNominalSpeedInput(formatOptionalPositiveInteger(result.result.nominalSpeedRpm));
            setBladeCountInput(formatOptionalPositiveInteger(result.result.bladeCount));
          });
        },
        () => resetWheelDraft(wheel),
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
          setNominalSpeedInput('');
          setBladeCountInput('');
          setWheelCreateId(crypto.randomUUID());
          setMessage(null);
          setError(null);
          window.setTimeout(() => wheelNameRef.current?.focus(), 0);
        },
        () => resetWheelDraft(wheel),
      );
    };
    const saveWheel = (): Promise<void> => {
      if (nominalSpeed.kind === 'invalid' || bladeCount.kind === 'invalid') {
        return Promise.resolve();
      }
      const normalizedDraft: WheelModelDraft = {
        ...wheelDraft,
        nominalSpeedRpm: nominalSpeed.value,
        bladeCount: bladeCount.value,
      };
      return trackSave(
        run('wheel-save', async () => {
          const result =
            wheel === null
              ? await desktopApi.wheelModel.create({
                  wheelModelId: wheelCreateId,
                  ...normalizedDraft,
                })
              : await desktopApi.wheelModel.update({
                  wheelModelId: wheel.wheelModelId,
                  expectedRevision: wheel.recordRevision,
                  wheelModel: normalizedDraft,
                });
          if (!result.ok) return setError(result.error);
          setWheel(result.result);
          setWheelDraft(wheelToDraft(result.result));
          setNominalSpeedInput(formatOptionalPositiveInteger(result.result.nominalSpeedRpm));
          setBladeCountInput(formatOptionalPositiveInteger(result.result.bladeCount));
          await loadWheels();
          setMessage(`Модель сохранена. Редакция ${String(result.result.recordRevision)}.`);
        }),
      );
    };
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
            setNominalSpeedInput(formatOptionalPositiveInteger(result.result.nominalSpeedRpm));
            setBladeCountInput(formatOptionalPositiveInteger(result.result.bladeCount));
            setConfirmArchiveKey(null);
            await loadWheels();
          }),
        () => resetWheelDraft(wheel),
      );
    };

    const selectSpecimen = (specimenId: string | null): void => {
      if (specimenId === null) return;
      requestTransition(
        specimenDirty,
        () => {
          const selectionRevision = ++selectionRevisionRef.current;
          setConfirmArchiveKey(null);
          void run('specimen-load', async () => {
            const result = await desktopApi.specimen.get(specimenId);
            if (selectionRevision !== selectionRevisionRef.current) return;
            if (!result.ok) return setError(result.error);
            setSpecimen(result.result);
            const draft = specimenToDraft(result.result);
            setSpecimenDraft(draft);
            setSpecimenBaseline(draft);
          });
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
          window.setTimeout(() => specimenNumberRef.current?.focus(), 0);
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

    const selectDocument = (caseDocumentId: string | null): void => {
      if (caseDocumentId === null) return;
      requestTransition(
        documentDirty,
        () => {
          const selectionRevision = ++selectionRevisionRef.current;
          setConfirmArchiveKey(null);
          void run('document-load', async () => {
            const result = await desktopApi.caseDocument.get(caseDocumentId);
            if (selectionRevision !== selectionRevisionRef.current) return;
            if (!result.ok) return setError(result.error);
            setDocument(result.result);
            resetDocumentDraft(result.result);
          });
        },
        () => resetDocumentDraft(document),
      );
    };
    const newDocument = (): void => {
      requestTransition(
        documentDirty,
        () => {
          selectionRevisionRef.current += 1;
          setConfirmArchiveKey(null);
          setDocument(null);
          resetDocumentDraft(null);
          setDocumentCreateId(crypto.randomUUID());
          setMessage(null);
          setError(null);
          window.setTimeout(() => documentTitleRef.current?.focus(), 0);
        },
        () => resetDocumentDraft(document),
      );
    };
    const saveDocument = (withFile: boolean): Promise<void> =>
      trackSave(
        run(withFile ? 'document-create-file' : 'document-save', async () => {
          const command = {
            caseDocumentId: documentCreateId,
            document: documentDraft,
            wheelModelIds: [...documentWheelIds].sort(),
            specimenIds: [...documentSpecimenIds].sort(),
          };
          const result =
            document === null
              ? withFile
                ? await desktopApi.caseDocument.createWithFile(command)
                : await desktopApi.caseDocument.create(command)
              : await desktopApi.caseDocument.update({
                  ...command,
                  caseDocumentId: document.caseDocumentId,
                  expectedRevision: document.recordRevision,
                });
          if (!result.ok) {
            if (result.error.code !== 'cancelled') setError(result.error);
            return;
          }
          setDocument(result.result);
          resetDocumentDraft(result.result);
          await loadDocuments();
          setMessage(`Документ сохранён. Редакция ${String(result.result.recordRevision)}.`);
        }),
      );
    const attachDocumentFile = (): void => {
      if (document === null) return;
      requestTransition(
        documentDirty,
        () =>
          void trackSave(
            run('document-attach', async () => {
              const result = await desktopApi.caseDocument.attachFile({
                caseDocumentId: document.caseDocumentId,
                expectedRevision: document.recordRevision,
              });
              if (!result.ok) {
                if (result.error.code !== 'cancelled') setError(result.error);
                return;
              }
              setDocument(result.result);
              resetDocumentDraft(result.result);
              await loadDocuments();
              setMessage(`Файл прикреплён. Редакция ${String(result.result.recordRevision)}.`);
            }),
          ),
        () => resetDocumentDraft(document),
      );
    };
    const verifyDocumentFile = (): void => {
      if (document === null) return;
      void run('document-verify', async () => {
        const result = await desktopApi.caseDocument.verifyFile(document.caseDocumentId);
        if (!result.ok) return setError(result.error);
        setDocument(result.result);
        setMessage('Проверка управляемой копии завершена.');
      });
    };
    const openDocumentFile = (): void => {
      if (document === null) return;
      void run('document-open', async () => {
        const result = await desktopApi.caseDocument.openFile(document.caseDocumentId);
        if (!result.ok) return setError(result.error);
        setMessage('Управляемая копия передана системному приложению.');
      });
    };
    const toggleDocumentArchive = (): void => {
      if (document === null) return;
      const confirmationKey = `document:${document.caseDocumentId}:${document.archivedAtUtc === null ? 'archive' : 'restore'}`;
      if (confirmArchiveKey !== confirmationKey) {
        setConfirmArchiveKey(confirmationKey);
        return;
      }
      requestTransition(
        documentDirty,
        () =>
          void trackSave(
            run('document-archive', async () => {
              const command = {
                caseDocumentId: document.caseDocumentId,
                expectedRevision: document.recordRevision,
              };
              const result =
                document.archivedAtUtc === null
                  ? await desktopApi.caseDocument.archive(command)
                  : await desktopApi.caseDocument.restore(command);
              if (!result.ok) return setError(result.error);
              setDocument(result.result);
              resetDocumentDraft(result.result);
              setConfirmArchiveKey(null);
              await loadDocuments();
            }),
          ),
        () => resetDocumentDraft(document),
      );
    };

    const finishReattach = useCallback((): DossierReattachResult => {
      setError(null);
      setMessage(null);
      setConfirmArchiveKey(null);
      return { status: 'reconciled' };
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        discardActiveDraft: () => {
          if (section === 'customer')
            setCustomerDraft(customer === null ? emptyCustomer : customerToDraft(customer));
          else if (section === 'wheels') resetWheelDraft(wheel);
          else if (section === 'specimens') setSpecimenDraft(specimenBaseline);
          else resetDocumentDraft(document);
        },
        waitForPendingSave: async () => {
          if (pendingSaveRef.current !== null) await pendingSaveRef.current;
        },
        verifyAfterReattach: async () => {
          if (section === 'customer') {
            const result = await desktopApi.caseCustomer.get();
            if (!result.ok) return { status: 'error', error: result.error };
            const decision = decideReattachEntity({
              dirty: activeDirty,
              localRevision: customer?.recordRevision ?? null,
              remoteRevision: result.result?.recordRevision ?? null,
            });
            if (decision === 'conflict') return { status: 'conflict' };
            setCustomer(result.result);
            if (decision === 'adopt') {
              setCustomerDraft(
                result.result === null ? emptyCustomer : customerToDraft(result.result),
              );
            }
            return finishReattach();
          }
          if (section === 'wheels') {
            const result = await desktopApi.wheelModel.get(wheel?.wheelModelId ?? wheelCreateId);
            const remote = result.ok ? result.result : null;
            if (!result.ok) {
              if (result.error.code !== 'entity_not_found') {
                return { status: 'error', error: result.error };
              }
            }
            const decision = decideReattachEntity({
              dirty: activeDirty,
              localRevision: wheel?.recordRevision ?? null,
              remoteRevision: remote?.recordRevision ?? null,
            });
            if (decision === 'conflict') return { status: 'conflict' };
            const list = await desktopApi.wheelModel.list(includeArchived);
            if (!list.ok) return { status: 'error', error: list.error };
            setWheels(list.result);
            if (remote !== null) {
              setWheel(remote);
              if (decision === 'adopt') resetWheelDraft(remote);
            }
            return finishReattach();
          }
          if (section === 'specimens') {
            const result = await desktopApi.specimen.get(specimen?.specimenId ?? specimenCreateId);
            const remote = result.ok ? result.result : null;
            if (!result.ok) {
              if (result.error.code !== 'entity_not_found') {
                return { status: 'error', error: result.error };
              }
            }
            const decision = decideReattachEntity({
              dirty: activeDirty,
              localRevision: specimen?.recordRevision ?? null,
              remoteRevision: remote?.recordRevision ?? null,
            });
            if (decision === 'conflict') return { status: 'conflict' };
            const wheelList = await desktopApi.wheelModel.list(true);
            if (!wheelList.ok) return { status: 'error', error: wheelList.error };
            const specimenList = await desktopApi.specimen.list(includeArchived);
            if (!specimenList.ok) return { status: 'error', error: specimenList.error };
            setWheels(wheelList.result);
            setSpecimens(specimenList.result);
            if (remote !== null) {
              setSpecimen(remote);
              if (decision === 'adopt') {
                const draft = specimenToDraft(remote);
                setSpecimenDraft(draft);
                setSpecimenBaseline(draft);
              }
            }
            return finishReattach();
          }
          if (section === 'documents') {
            const result = await desktopApi.caseDocument.get(
              document?.caseDocumentId ?? documentCreateId,
            );
            const remote = result.ok ? result.result : null;
            if (!result.ok) {
              if (result.error.code !== 'entity_not_found') {
                return { status: 'error', error: result.error };
              }
            }
            const decision = decideReattachEntity({
              dirty: activeDirty,
              localRevision: document?.recordRevision ?? null,
              remoteRevision: remote?.recordRevision ?? null,
            });
            if (decision === 'conflict') return { status: 'conflict' };
            const wheelList = await desktopApi.wheelModel.list(true);
            if (!wheelList.ok) return { status: 'error', error: wheelList.error };
            const specimenList = await desktopApi.specimen.list(true);
            if (!specimenList.ok) return { status: 'error', error: specimenList.error };
            const documentList = await desktopApi.caseDocument.list({
              includeArchived,
              documentKind: documentKindFilter,
            });
            if (!documentList.ok) return { status: 'error', error: documentList.error };
            setWheels(wheelList.result);
            setSpecimens(specimenList.result);
            setDocuments(documentList.result);
            if (remote !== null) {
              setDocument(remote);
              if (decision === 'adopt') resetDocumentDraft(remote);
            }
            return finishReattach();
          }
          return finishReattach();
        },
      }),
      [
        activeDirty,
        customer,
        desktopApi,
        document,
        documentCreateId,
        documentKindFilter,
        finishReattach,
        includeArchived,
        resetWheelDraft,
        resetDocumentDraft,
        section,
        specimen,
        specimenBaseline,
        specimenCreateId,
        wheel,
        wheelCreateId,
      ],
    );

    return (
      <section className="project-surface dossier-surface" aria-labelledby="dossier-title">
        <div className="section-heading">
          <Title id="dossier-title" order={2}>
            {section === 'customer'
              ? 'Заказчик'
              : section === 'wheels'
                ? 'Модели рабочих колёс'
                : section === 'specimens'
                  ? 'Физические образцы'
                  : 'Документы дела'}
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
              disabled={disabled || busy !== null}
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
                  ref={wheelNameRef}
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
                  value={nominalSpeedInput}
                  error={
                    nominalSpeed.kind === 'invalid'
                      ? 'Введите целое положительное число.'
                      : undefined
                  }
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) => setNominalSpeedInput(event.currentTarget.value)}
                />
                <TextInput
                  label="Количество лопастей"
                  value={bladeCountInput}
                  error={
                    bladeCount.kind === 'invalid' ? 'Введите целое положительное число.' : undefined
                  }
                  disabled={
                    disabled || busy !== null || (wheel !== null && wheel.archivedAtUtc !== null)
                  }
                  onChange={(event) => setBladeCountInput(event.currentTarget.value)}
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
              {wheel !== null ? (
                <section
                  className="dossier-reliability-executions"
                  aria-labelledby="wheel-executions-title"
                >
                  <Title id="wheel-executions-title" order={4}>
                    Испытания надёжности
                  </Title>
                  {reliabilityExecutions.length === 0 ? (
                    <Text size="sm">
                      Для этой модели пока нет materialized исполнений из связанного R130SH
                      источника.
                    </Text>
                  ) : (
                    <div className="dossier-execution-list">
                      {reliabilityExecutions.map((execution) => (
                        <div key={execution.executionId} className="dossier-execution-row">
                          <strong>{execution.method.toUpperCase()}</strong>
                          <span>{execution.lifecycleStatus}</span>
                          <span>Источник: {execution.localImportId}</span>
                          <span>Наблюдений: {String(execution.failureObservations.length)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              ) : null}
              <Group className="form-actions">
                <Button
                  type="submit"
                  loading={busy === 'wheel-save'}
                  disabled={
                    disabled ||
                    busy !== null ||
                    wheelDraft.fullName.trim() === '' ||
                    hasInvalidIntegerInput ||
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
        ) : section === 'specimens' ? (
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
                  ref={specimenNumberRef}
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
        ) : (
          <div className="dossier-editor dossier-editor--documents">
            <DossierList
              title="Реестр документов"
              items={documents.map((item) => ({
                id: item.caseDocumentId,
                title: item.title,
                subtitle:
                  item.designation === '' ? documentKindLabel[item.documentKind] : item.designation,
                archived: item.archivedAtUtc !== null,
              }))}
              selectedId={document?.caseDocumentId ?? null}
              busy={busy}
              disabled={disabled}
              onSelect={selectDocument}
              onCreate={newDocument}
              includeArchived={includeArchived}
              onIncludeArchived={setIncludeArchived}
              createLabel="Новый документ"
              filter={
                <Select
                  label="Фильтр по виду"
                  clearable
                  data={documentKindOptions}
                  value={documentKindFilter}
                  disabled={disabled || busy !== null}
                  onChange={(value) => {
                    if (value === null) return setDocumentKindFilter(null);
                    const parsed = caseDocumentKindSchema.safeParse(value);
                    if (parsed.success) setDocumentKindFilter(parsed.data);
                  }}
                />
              }
            />
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void saveDocument(false);
              }}
              className="dossier-detail"
            >
              <div className="dossier-form dossier-form--two-columns">
                <Select
                  label="Вид документа"
                  required
                  data={documentKindOptions}
                  value={documentDraft.documentKind}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(value) => {
                    const parsed = caseDocumentKindSchema.safeParse(value);
                    if (parsed.success)
                      setDocumentDraft({
                        ...documentDraft,
                        documentKind: parsed.data,
                      });
                  }}
                />
                <TextInput
                  ref={documentTitleRef}
                  label="Название"
                  required
                  value={documentDraft.title}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setDocumentDraft({ ...documentDraft, title: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Обозначение"
                  value={documentDraft.designation}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setDocumentDraft({ ...documentDraft, designation: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Редакция"
                  value={documentDraft.revisionLabel}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setDocumentDraft({ ...documentDraft, revisionLabel: event.currentTarget.value })
                  }
                />
                <TextInput
                  type="date"
                  label="Дата документа"
                  value={documentDraft.documentDate ?? ''}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setDocumentDraft({ ...documentDraft, documentDate: event.currentTarget.value })
                  }
                />
                <TextInput
                  label="Организация/автор"
                  value={documentDraft.issuer}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setDocumentDraft({ ...documentDraft, issuer: event.currentTarget.value })
                  }
                />
                <fieldset className="dossier-applicability">
                  <legend>Применимые модели</legend>
                  {wheels.length === 0 ? (
                    <Text size="sm">Модели ещё не зарегистрированы.</Text>
                  ) : (
                    wheels.map((item) => (
                      <Checkbox
                        key={item.wheelModelId}
                        label={`${item.fullName}${item.archivedAtUtc === null ? '' : ' · архив'}`}
                        checked={documentWheelIds.includes(item.wheelModelId)}
                        disabled={
                          disabled ||
                          busy !== null ||
                          (document !== null && document.archivedAtUtc !== null)
                        }
                        onChange={(event) =>
                          setDocumentWheelIds(
                            toggleIdentifier(
                              documentWheelIds,
                              item.wheelModelId,
                              event.currentTarget.checked,
                            ),
                          )
                        }
                      />
                    ))
                  )}
                </fieldset>
                <fieldset className="dossier-applicability">
                  <legend>Применимые образцы</legend>
                  {specimens.length === 0 ? (
                    <Text size="sm">Образцы ещё не зарегистрированы.</Text>
                  ) : (
                    specimens.map((item) => (
                      <Checkbox
                        key={item.specimenId}
                        label={`${item.identificationNumber} · ${item.wheelModelName}${item.archivedAtUtc === null ? '' : ' · архив'}`}
                        checked={documentSpecimenIds.includes(item.specimenId)}
                        disabled={
                          disabled ||
                          busy !== null ||
                          (document !== null && document.archivedAtUtc !== null)
                        }
                        onChange={(event) =>
                          setDocumentSpecimenIds(
                            toggleIdentifier(
                              documentSpecimenIds,
                              item.specimenId,
                              event.currentTarget.checked,
                            ),
                          )
                        }
                      />
                    ))
                  )}
                </fieldset>
                <Textarea
                  className="dossier-span"
                  label="Примечание"
                  value={documentDraft.notes}
                  disabled={
                    disabled ||
                    busy !== null ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                  onChange={(event) =>
                    setDocumentDraft({ ...documentDraft, notes: event.currentTarget.value })
                  }
                />
              </div>
              <DocumentFileDetails document={document} />
              <Warnings warnings={document?.warnings ?? defaultDocumentWarnings(documentDraft)} />
              <Group className="form-actions">
                <Button
                  type="submit"
                  loading={busy === 'document-save'}
                  disabled={
                    disabled ||
                    busy !== null ||
                    documentDraft.title.trim() === '' ||
                    (document !== null && document.archivedAtUtc !== null)
                  }
                >
                  {document === null ? 'Создать без файла' : 'Сохранить документ'}
                </Button>
                {document === null ? (
                  <Button
                    type="button"
                    variant="light"
                    loading={busy === 'document-create-file'}
                    disabled={disabled || busy !== null || documentDraft.title.trim() === ''}
                    onClick={() => void saveDocument(true)}
                  >
                    Создать с файлом
                  </Button>
                ) : document.file === null && document.archivedAtUtc === null ? (
                  <Button
                    type="button"
                    variant="light"
                    loading={busy === 'document-attach'}
                    disabled={disabled || busy !== null}
                    onClick={attachDocumentFile}
                  >
                    Прикрепить файл
                  </Button>
                ) : null}
                {document !== null && document.file !== null ? (
                  <>
                    <Button
                      type="button"
                      variant="subtle"
                      loading={busy === 'document-verify'}
                      disabled={disabled || busy !== null}
                      onClick={verifyDocumentFile}
                    >
                      Проверить целостность
                    </Button>
                    <Button
                      type="button"
                      variant="subtle"
                      loading={busy === 'document-open'}
                      disabled={
                        disabled || busy !== null || document.integrityStatus !== 'verified'
                      }
                      onClick={openDocumentFile}
                    >
                      Открыть управляемую копию
                    </Button>
                  </>
                ) : null}
                {document !== null ? (
                  <Button
                    type="button"
                    className="danger-action"
                    variant={
                      confirmArchiveKey?.startsWith(`document:${document.caseDocumentId}:`)
                        ? 'filled'
                        : 'subtle'
                    }
                    loading={busy === 'document-archive'}
                    disabled={disabled || busy !== null}
                    onClick={toggleDocumentArchive}
                  >
                    {confirmArchiveKey?.startsWith(`document:${document.caseDocumentId}:`)
                      ? 'Подтвердить действие'
                      : document.archivedAtUtc === null
                        ? 'Архивировать'
                        : 'Восстановить'}
                  </Button>
                ) : null}
                <Text size="sm">Редакция: {document?.recordRevision ?? 'новая запись'}</Text>
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
  filter,
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
  readonly filter?: ReactNode;
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
      {filter}
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
              disabled={disabled || busy !== null}
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

const integrityLabel: Readonly<Record<CaseDocument['integrityStatus'], string>> = {
  not_attached: 'Файл не прикреплён',
  verified: 'Целостность подтверждена',
  missing: 'Управляемый файл отсутствует',
  modified: 'Содержимое управляемого файла изменено',
  verification_error: 'Проверку файла выполнить не удалось',
};

function DocumentFileDetails({
  document,
}: {
  readonly document: CaseDocument | null;
}): React.JSX.Element | null {
  if (document === null) return null;
  const file = document.file;
  return (
    <section className="document-file" aria-labelledby="document-file-title">
      <div>
        <strong id="document-file-title">Управляемая копия</strong>
        <span data-integrity={document.integrityStatus}>
          {integrityLabel[document.integrityStatus]}
        </span>
      </div>
      {file === null ? (
        <Text size="sm">К записи можно один раз прикрепить локальный файл.</Text>
      ) : (
        <dl>
          <div>
            <dt>Исходное имя</dt>
            <dd>{file.originalFileName}</dd>
          </div>
          <div>
            <dt>Тип</dt>
            <dd>{file.mediaType}</dd>
          </div>
          <div>
            <dt>Размер</dt>
            <dd>{formatFileSize(file.sizeBytes)}</dd>
          </div>
          <div className="document-file__hash">
            <dt>SHA-256</dt>
            <dd>{file.sha256}</dd>
          </div>
        </dl>
      )}
    </section>
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

const warningText: Readonly<Record<CompletenessWarning | CaseDocumentWarning, string>> = {
  customer_address_missing: 'Не заполнен юридический или фактический адрес.',
  wheel_nominal_diameter_missing: 'Не указан номинальный диаметр модели.',
  wheel_nominal_speed_missing: 'Не указана номинальная частота вращения.',
  specimen_working_diameter_missing: 'Не указан рабочий диаметр образца.',
  case_document_file_missing: 'Файл документа не прикреплён или отсутствует.',
  case_document_designation_missing: 'Для нормативного документа не заполнено обозначение.',
  case_document_revision_missing: 'Для нормативного документа не указана редакция.',
};

function Warnings({
  warnings,
}: {
  readonly warnings: readonly (CompletenessWarning | CaseDocumentWarning)[];
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
function documentToDraft(value: CaseDocument): CaseDocumentDraft {
  return {
    documentKind: value.documentKind,
    title: value.title,
    designation: value.designation,
    revisionLabel: value.revisionLabel,
    documentDate: value.documentDate,
    issuer: value.issuer,
    notes: value.notes,
  };
}
function toggleIdentifier(
  values: readonly string[],
  identifier: string,
  checked: boolean,
): readonly string[] {
  const next = checked
    ? [...new Set([...values, identifier])]
    : values.filter((value) => value !== identifier);
  return next.sort();
}
function defaultDocumentWarnings(value: CaseDocumentDraft): readonly CaseDocumentWarning[] {
  const warnings: CaseDocumentWarning[] = ['case_document_file_missing'];
  if (
    value.documentKind === 'technical_specification' ||
    value.documentKind === 'individual_test_method' ||
    value.documentKind === 'typical_test_method' ||
    value.documentKind === 'standard'
  ) {
    if (value.designation.trim() === '') warnings.push('case_document_designation_missing');
    if (value.revisionLabel.trim() === '') warnings.push('case_document_revision_missing');
  }
  return warnings;
}
function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${String(sizeBytes)} Б`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} КиБ`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} МиБ`;
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
function unavailableError(): DesktopError {
  return {
    code: 'worker_unavailable',
    message: 'Локальный worker недоступен. Перезапустите ядро и повторите операцию.',
    details: {},
    retryable: true,
  };
}
