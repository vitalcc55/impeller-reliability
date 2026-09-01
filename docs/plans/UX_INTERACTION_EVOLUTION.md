# План развития UX-взаимодействий

Этот план распределяет развитие пользовательских взаимодействий по предметным этапам. Он не создаёт пятую архитектурную карту, не является журналом состояния и не разрешает будущие функции без отдельной задачи. Текущая продуктовая истина остаётся в `PRODUCT.md`, повторяемые решения — в `DESIGN.md`, состояние и потоки — в четырёх существующих архитектурных картах, проверяемые требования — в `docs/requirements/` и `docs/testing/TEST_STRATEGY.md`.

## Цель и направление

Impeller Reliability развивается как desktop engineering workspace в режиме `Operate`: предсказуемый, насыщенный данными, прослеживаемый, доступный с клавиатуры, устойчивый к отказам и адаптивный к размеру окна. Визуальная система «Инженерный чертёж ЛИЦ ВВУ» сохраняется. UX-архитектура расширяет её поведенческими контрактами; декоративная полировка следует за реальной плотностью предметных экранов.

Нормативный ориентир renderer — WCAG 2.2 AA. Клавиатурные составные элементы следуют WAI-ARIA Authoring Practices, desktop-адаптация — рекомендациям Windows, автоматические проверки дополняются ручными, поскольку не доказывают доступность целиком.

## Общие инварианты

- пользователь всегда может определить текущий раздел, выбранный объект и сохранённость изменений;
- одинаковые состояния и действия имеют одинаковую семантику во всех модулях;
- persisted truth, runtime state и renderer draft не смешиваются;
- validation/conflict/transport failure сохраняют локальный ввод;
- статус не забирает focus, ошибка выражена текстом, цвет не является единственным сигналом;
- focus видим, порядок DOM и визуальный порядок согласованы, положительный `tabindex` запрещён;
- обязательная функция не исчезает в поддерживаемом desktop диапазоне; master-detail сохраняет читаемую пропорцию от 1280×720;
- reversible действие использует archive/restore; необратимое действие требует осознанного подтверждения;
- provenance, единица, warning и integrity показываются рядом с инженерным значением, когда они существуют в предметном контракте;
- autosave domain data не вводится: audit и optimistic revision создаются только явной предметной операцией.

## M02.2B — обязательный фундамент сейчас

Для Project, Analyst Dossier и CaseDocument закрепляются:

- словарь наблюдаемых состояний `loading`, `empty`, `ready.clean`, `ready.dirty`, `validating`, `saving`, `validation_failed`, `save_failed`, `revision_conflict`, `unavailable`, `detached`, `archived`; код не обязан использовать один глобальный state machine;
- единый draft-owner contract для смены раздела/объекта, attach, archive/restore, close/open/restart и detached discard;
- сохранение введённых значений при validation, conflict и transport failure;
- последовательная загрузка ProjectSession без конкурирующих запросов к сериализованной сессии;
- семантические headings/regions/status/alert, доступные имена, keyboard activation и видимый focus;
- возврат focus после подтверждения и разумный focus после create/archive/restore без самопроизвольных прыжков после фонового ответа;
- Windows desktop/laptop от 1280×720, оптимизация 1536×864–1920×1080; mobile/640 px и отдельная узкоэкранная композиция исключены; перенос длинных имён, SHA-256 и русских строк;
- empty/error/warning/integrity состояния документов и честная семантика SQLite-only backup;
- Browser/Electron E2E для keyboard, dirty-loss, focus, поддерживаемых Windows desktop layouts, clean console и worker failure.

M02.2B не создаёт command palette, универсальный router/history, recovery-хранилище черновиков, job framework, DataGrid, chart framework или новую компонентную библиотеку.

## M03A — проверка контракта

M03A вводит минимальный повторяемый UX read-only job: `queued`, `running`, известный/неизвестный progress, `cancelling`, `completed`, `failed`, `cancelled`; последовательный polling с backoff; cancel/retry/clear; infrastructure alert; текстовую severity; восстановление focus только владельцу действия; schema/validator/upstream/hash/findings provenance. Отмена или ошибка dialog сохраняет предыдущий terminal report, принятый новый файл атомарно заменяет его. Это validation evidence в «Диагностике», не `r130sh_source` и не импорт.

## M03B — импорт и persisted provenance

M03B production importer расширяет job lifecycle состояниями validation/copy/revalidation/commit, bounded cancel/drain и reopen reconciliation. В project workspace появляется «Результаты R130SH»: master-detail список, десять bounded detail-секций, постоянная diagnostic-partial метка, source/analyst comparison, явная specimen binding и resolution provenance. Persisted truth — immutable `r130sh_source` и managed archive; runtime job остаётся in-memory, общий job journal не создаётся.

## M04 — расчётная вертикаль и визуализация

До первого РБД-сценария вводятся общая команда расчёта, доступность действия из состояния inputs/runtime, job feedback без блокировки всего приложения, source/unit/warning presentation и контракт chart container: title, description, axes/units, legend, reference lines, keyboard-accessible exact values, reset и табличная/текстовая альтернатива. Конкретная визуальная композиция определяется на реальных данных РБД, затем повторно используется РПТ/ПМН только при совпадающей семантике.

## Перед FMEA и плотными таблицами

Command/shortcut layer становится единым владельцем действия, label, shortcut и availability; toolbar/menu/shortcut вызывают одну команду. Navigation history получает только существенные рабочие состояния. Для читаемых данных используется semantic table; interactive grid появляется лишь при cell editing/selection/cell navigation и обязан иметь полный keyboard/focus contract, sticky context и сохранение редактируемой строки.

## Перед production release

- ручные проходы NVDA и Narrator;
- Windows High Contrast, DPI/text scaling и reduced motion;
- экстремальный reflow/zoom-equivalent viewport;
- автоматический axe gate как дополнение, а не замена ручной проверки;
- измерение `app visible`, `project opened`, `section switched`, `save acknowledged` и первого результата для реализованных сценариев;
- решение о recovery checkpoint несохранённого renderer draft после crash всего Electron. Такой checkpoint не является project truth, не создаёт audit/revision и удаляется после подтверждённого save/discard.

## Критерий готовности каждого будущего UX-пакета

Решение имеет одного владельца, описывает normal/empty/loading/error/conflict/detached/archived состояния по применимости, работает с клавиатуры в поддерживаемом Windows desktop диапазоне, не теряет draft, не скрывает обязательную функцию, проверено детерминированным тестом и одним ограниченным визуальным проходом. Новая системная абстракция появляется только после подтверждённого повторения или до первого экрана, который без неё создаст несовместимые реализации.
