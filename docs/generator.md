# Генератор эпизодов (`auditgym.generator`)

Контракт генератора для среды, оценщика и политик: формат файлов, канонический эталон, правила сопоставления на стороне данных.

## Быстрый старт

```bash
python -m auditgym.generator generate --config configs/presets/mixed_small.json --seed 7 --out /tmp/ep7
python -m auditgym.generator build            # пересобрать episodes/{train,dev,sample} + SHA256SUMS
python -m auditgym.generator build --check    # регенерация байт-в-байт совпадает с закоммиченной
python -m auditgym.generator verify episodes/train/train_00
python -m auditgym.generator selfcheck        # 20 эпизодов «как у внешней проверки»: 5 чистых, 5 с A/B, 10 смешанных
```

```python
from auditgym.generator import generate, load_episode
ep = generate(seed=7, config={"n_documents": 60})   # частичный config мержится с дефолтами
ep.public    # manifest, policy, ledger, invoices, payments, fx  — то, что может видеть среда
ep.private   # truth, provenance                              — только оценщик
public, private = load_episode("episodes/train/train_00")      # те же структуры с диска
```

Одинаковые `seed` + config + `GENERATOR_VERSION` → одинаковые байты (проверено на Python 3.12/3.14/3.15, CI — 3.11).

## Конвейер

1. **Скрытая история** (`history.py`) — компания, контрагенты, счета, платежи, курсы. Всё корректно по построению.
2. **Чистый рендер + инварианты** (`render.py`, `invariants.py`, strict) — Дт = Кт по каждой проводке и в целом, суммы строк счетов, распределения, конверсии по policy, «проведён ⇔ дата в периоде». Плюс эталонный аудитор не должен найти ни одного расхождения.
3. **Инъекция ошибок** (`errors/`) — каждый инъектор меняет параметры проводки или документы зарезервированного корректного потока и пишет изменение в `provenance.changes` (до/после) и проводки до/после в `provenance.ledger_diff`.
4. **Рендер** опубликованных записей.
5. **Независимая проверка** (`verify.py`): инварианты; эталонный аудитор (`audit.py`) по одним публичным записям находит **ровно** issues из `truth.json`; все ID доказательств существуют; хотя бы один набор доказательств **сам по себе** позволяет пересчитать тип, affected_ids и сумму; в публичных файлах нет следов эталона. Не прошло — детерминированная повторная попытка (`attempt` в provenance; на практике < 1 %).

## Файлы эпизода

```
<episode>/public/  manifest.json  policy.json  ledger.csv  invoices.jsonl  payments.jsonl  fx.csv
<episode>/private/ truth.json     provenance.json
```

* Деньги — целые минимальные единицы; разрядность — `manifest.currencies[CCY].exponent` (JPY = 0, остальные = 2).
* Время — ISO-8601 со смещением. Документы записаны либо в часовом поясе компании, либо в UTC (`+00:00`) — это намеренно. Проводки леджера — всегда в поясе компании.
* Курсы — десятичные строки; `1 source = rate target` в основных единицах; только рабочие дни.
* CSV: UTF-8, `\n`, пустая ячейка = `null`. JSONL — компактный JSON по строке, `null` явный.

### `ledger.csv`
`row_id, timestamp, account, debit_minor, credit_minor, currency, entity_id, invoice_id, payment_id` — ровно одна сторона > 0, валюта всегда базовая.

| Проводка | Строки |
| --- | --- |
| Счёт | Дт `accounts_receivable` / Кт `revenue` (invoice_id, payment_id = null) |
| Поступление | Дт `bank:<CCY>`; Кт `accounts_receivable` на каждый invoice_ref (invoice_id + payment_id); Кт `unapplied_cash` на нераспределённый остаток; разница → `fx_gain_loss` |
| Возврат | Кт `bank:<CCY>`; Дт `unapplied_cash` по курсу возвращаемого платежа; разница → `fx_gain_loss` |
| Фоновые | расходы, зарплата, прочий доход — без документов, entity/invoice/payment = null |

### `invoices.jsonl`
`doc_id, invoice_id, entity_id, issued_at, due_at, currency, total_minor, line_items[{description, quantity, unit_price_minor, amount_minor}]`

### `payments.jsonl`
`doc_id, payment_id, entity_id, timestamp, currency, amount_minor, kind ("receipt"|"refund"), invoice_refs[{invoice_id, applied_minor}], refund_of`
Сверх минимального набора полей добавлены `entity_id`, `kind`, `refund_of` — без них нельзя однозначно связать платёж с клиентом и возвратом.

### `policy.json` (кратко)
Период `[start, end)` в поясе компании; дата документа = `issued_at` / `timestamp` **в поясе компании**; курс — на эту дату, если нет — последний опубликованный до неё; счета и платежи конвертируются по курсу своей даты; AR гасится по курсу счёта; каждая строка округляется один раз (`ROUND_HALF_EVEN` или `ROUND_HALF_UP` — см. manifest).

## Канонический эталон (`truth.json`)

```json
{"issue_id": "ISS-03", "type": "fx_conversion", "affected_ids": ["payments:PAY-ACYU33"],
 "expected_amount_minor": 2310270, "currency": "KZT",
 "evidence_sets": [["fx:2026-11-20/USD/KZT", "ledger:L3FUPUZU", "payments:PAY-ACYU33"]],
 "tolerance_minor": 1, "group": "B1", "description": "..."}
```

Формат ID везде: `"<table>:<id>"` — `ledger:<row_id>`, `invoices:<invoice_id>`, `payments:<payment_id>`, `fx:<date>/<source>/<target>`. `doc_id` в эталоне не используется (матчер может принимать его как алиас). Списки отсортированы.

| type | Когда это ошибка (правило `audit.py`) | affected_ids | expected_amount_minor, currency | evidence_sets |
| --- | --- | --- | --- | --- |
| `duplicate_invoice` | ≥ 2 проведённых счёта с одинаковыми entity, валютой, суммой и **идентичными** line_items; дубль — все, кроме самого раннего по `issued_at` | `[invoices:<дубль>]` | total дубля, валюта счёта | `[оригинал, дубль]` |
| `payment_allocation` | у проведённого поступления остаток r > 0 и: refs пусты и ровно один счёт того же клиента и валюты имеет открытый остаток = сумме платежа; или один ref, и открытый остаток этого счёта = r | `[payments:P, invoices:I]` | r, валюта платежа | `[P, I]` |
| `fx_conversion` | проведённый документ в иностранной валюте, главная строка (AR-дебет / bank) ≠ конверсии по курсу policy | `[<документ>]` | \|проведено − правильно\| главной строки, базовая валюта | `[документ, главная строка леджера, fx-строка правильной даты]` |
| `period_cutoff` | проведённый документ, чья дата в поясе компании вне `[start, end)` | `[<документ>]` | \|главная строка как проведена\|, базовая валюта | `[документ, главная строка леджера]` |

Открытый остаток счёта = `total_minor − Σ applied_minor` по всем платежам. `tolerance_minor`: 1 для FX (округление), 0 для остальных — рекомендация, окончательный допуск задаёт матчер.

### Взаимодействующие сценарии (`group`)
* **A** — поступление в иностранной валюте через минуты после конца периода (в UTC дата ещё последняя), проведено в периоде, распределено частично и сконвертировано по курсу не той даты → `period_cutoff` + `payment_allocation` + `fx_conversion` на одном платеже. Сумма cutoff — строка банка **как проведена** (с FX-ошибкой).
* **B** — дубль счёта в иностранной валюте; дубль оплачен, платёж сконвертирован по курсу не той даты → `duplicate_invoice` + `fx_conversion`.

### Отвлекающие корректные случаи (в эталон не попадают)
Разбитая оплата 2–3 платежами; честная частичная оплата; еженедельные счета одному клиенту на одну сумму (разные `service week of …`); двойная оплата, оставленная в `unapplied_cash` и возвращённая (`kind: refund`); документы в минутах от границы периода в UTC; счета следующего периода без проводок; документы в выходные по курсу пятницы; счета прошлого периода, оплаченные сейчас или открытые.

## Config

Пресеты — `configs/presets/*.json`; сплиты — `configs/{train,dev,sample,selfcheck}/episodes.json` (имя, пресет, seed). Любое число — `int` или диапазон `[lo, hi]`, сэмплируется по seed.

| Ключ | Значение |
| --- | --- |
| `config_version` | `"1.0"` |
| `n_ledger_rows` / `n_documents` | 200–2000 / 20–200 (целевые; фактические — в manifest.counts) |
| `base_currency`, `currencies`, `n_foreign_currencies`, `foreign_invoice_share` | валюты (KZT, USD, EUR, JPY) |
| `timezone`, `period` | `"+05:00"`, `"2026-03"` или `null` = случайно |
| `clean` | `true` → ни одной ошибки |
| `error_counts` | по каждому из 4 типов отдельно |
| `interactions` | `{"A": n, "B": n}` |
| `distractors` | количество каждого вида отвлекающих случаев |

Валидация: максимум 12 issues на эпизод (A = 3, B = 2), FX/A/B требуют ≥ 2 валют.

## Для среды, оценщика и политик

* **Среда:** отдавать только `ep.public`; `private` — только оценщику. Поля, начинающиеся с `_`, в публичных данных не встречаются.
* **Оценщик:** `truth.json` готов к матчингу; `auditgym.generator.audit.audit` — эталонная реализация правил выше, удобна для тестов. Пары `transforms.remap_ids` / `shuffle_rows` дают «тот же эпизод с новыми ID» для парных проверок.
* **Политики:** Oracle = `truth.issues` → findings (`claimed_amount_minor = expected_amount_minor`, `evidence_ids = evidence_sets[0]`).

## Ограничения
* Только дебиторская сторона (продажи и поступления); кредиторки, НДС и мультивалютных переоценок на конец периода нет.
* Фиксированные смещения часовых поясов, без перехода на летнее время; праздники не моделируются (курсы только по будним дням).
* Генератор открыт, поэтому при малом пространстве seed эпизод теоретически можно сопоставить с seed перебором. Агент в среде не имеет доступа к коду и файлам, но для закрытой проверки стоит брать большие seed.
