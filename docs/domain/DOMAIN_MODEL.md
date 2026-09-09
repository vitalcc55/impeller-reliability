# Domain Model

`Project` — аналитическое дело Impeller Reliability (`ReliabilityCase`). Оно агрегирует редактируемый `analyst_enrichment` (`CustomerProfile`, `WheelModel`, `Specimen`, `AnalystSourceDocument`), неизменяемый `r130sh_source`, M04A `derived_analysis` и audit. `TestCampaign` в будущем будет только downstream-группировкой импортированных запусков. Impeller Reliability не имеет собственного исполняемого `TestPlan`.

`ImportedTestRun` — принятая immutable export revision R130SH; её `r130sh_source` владеет exact managed archive, inventory и узкой projection. `ImportedRunPlanSnapshot` — неизменяемый original/effective plan внутри source projection; его владельцем остаётся R130SH. `TestExecution` фиксирует конкретную export revision, физический `Specimen` и его `WheelModel` на момент materialization. `FailureObservation` хранит source-derived свидетельство, а не статистическую классификацию.

`ReliabilityObservation` — явная неизменяемая версионируемая интерпретация инженера для одного `TestExecution`: classification, форма endpoint, документированная наработка либо её отсутствие, выбранные evidence references и frozen provenance документа. `ReliabilityDataset` — неизменяемая версия рассмотренного состава exact observation versions с раздельными policy eligibility и решением `included/excluded`. `AnalysisInputSnapshot` и `CalculationSnapshot` отсутствуют до M04C.

`acceptedElapsedS` принадлежит source/result evidence и означает только накопленную длину промежутков между соседними accepted samples внутри included segments. Он не является длительностью испытания, censor endpoint или life metric и поэтому не заполняет `FailureObservation.durationS` или `ReliabilityObservation` автоматически.

M03A contract validation не создаёт новую domain entity: её transient job/report не являются `ImportedTestRun`, `ImportedRunPlanSnapshot`, project entity, audit event, import receipt, analysis input или признаком готовности к расчёту. Точная runtime-модель принадлежит Integration/IPC и карте состояния.

Первый публикуемый `Project` представлен контейнером `.irproj` и clean pre-release schema v1: dossier и R130SH source tables дополнены immutable `TestExecution`, `FailureObservation`, versioned `ReliabilityObservation`, versioned `ReliabilityDataset` и append-only audit. Source registry/inventory/projection неизменяемы; binding optimistic, resolution append-only, exact import/version retry не создаёт revision/event.

`AnalystSourceDocument` («Документ дела») — редактируемая регистрационная запись analyst enrichment. Она имеет вид, название, обозначение, редакцию, дату, issuer, notes и применимость к нескольким WheelModel/Specimen; отсутствие links означает всё дело. У записи не более одной неизменяемой управляемой копии. Файл не является imported R130SH source: новая фактическая редакция создаёт новый документ, hard delete и supersedes-chain отсутствуют.

`ManagedDocumentFile` хранит original name, media type, size, SHA-256, project-relative POSIX path и время attach. Содержимое находится только внутри локального `.irproj`; абсолютный исходный путь не является domain data. Integrity status вычисляется при чтении файла и не изменяет зарегистрированный hash: `not_attached`, `verified`, `missing`, `modified`, `verification_error`.

App-level `health.sqlite` продолжает содержать только инфраструктурную `schema_info` и не является частью Project.
