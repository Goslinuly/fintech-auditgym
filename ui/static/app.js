"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};

/* ------------------------------------------------------------------ labels */
const TYPE_RU = {
  duplicate_invoice: "Дубль счёта",
  payment_allocation: "Платёж не зачтён",
  fx_conversion: "Неверный курс",
  period_cutoff: "Не тот месяц",
};
const TYPE_HINT = {
  duplicate_invoice: "Один счёт внесён дважды под разными номерами. Выручка завышена.",
  payment_allocation: "Деньги от покупателя пришли, но не зачтены в оплату счёта или зачтены не полностью.",
  fx_conversion: "Валютный документ пересчитан по курсу другой даты.",
  period_cutoff: "Документ другого месяца проведён в этом.",
};
const GROUP_RU = {
  A: "Сценарий A: один валютный платёж на стыке месяцев, три ошибки сразу",
  B: "Сценарий B: дубль валютного счёта, оплаченный по неверному курсу",
};
const DISTRACTOR_RU = {
  split_payment: "Счёт оплачен несколькими платежами",
  partial_payment: "Счёт оплачен частично",
  recurring: "Еженедельные счета на одну сумму",
  refund: "Двойная оплата, деньги вернули",
  boundary: "Документ на стыке месяцев",
  next_period_invoice: "Счёт следующего месяца, не проведён",
  weekend_fx: "Документ в выходной, курс пятницы",
  prior_invoice: "Счёт прошлого месяца",
};
const PRESET_RU = {
  clean_small: "Без ошибок, небольшая",
  clean_large: "Без ошибок, крупная",
  mixed_small: "Несколько ошибок, небольшая",
  mixed_medium: "Несколько ошибок, средняя",
  mixed_large: "Много ошибок, крупная",
  interactions: "Связанные ошибки (A и B)",
  single_type: "Много ошибок одного вида",
};
const SPLIT_RU = { sample: "Пример", train: "Обучающая", dev: "Проверочная" };
const SPLIT_GROUP_RU = { sample: "Пример", train: "Обучающие", dev: "Проверочные" };
const CCY_RU = { KZT: "тенге", USD: "доллар США", EUR: "евро", JPY: "иена" };
const CCY_IN = { KZT: "тенге", USD: "долларах США", EUR: "евро", JPY: "иенах" };
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const ACCOUNT_RU = {
  accounts_receivable: "Долги покупателей",
  revenue: "Выручка",
  unapplied_cash: "Нераспределённые поступления",
  fx_gain_loss: "Курсовые разницы",
  payroll_tax_payable: "Налоги с зарплаты",
  other_income: "Прочие доходы",
  "expense:rent": "Аренда",
  "expense:payroll": "Зарплата",
  "expense:utilities": "Коммунальные услуги",
  "expense:software": "Программы и подписки",
  "expense:bank_fees": "Банковские комиссии",
  "expense:travel": "Командировки",
  "expense:office": "Офисные расходы",
  "expense:marketing": "Маркетинг",
};
const TABLE_RU = { invoices: "Счёт", payments: "Платёж", ledger: "Строка журнала", fx: "Курс" };
const TABS = [
  ["invoices", "Счета", "Счета, выставленные покупателям. Клик по строке покажет оплаты и проводку."],
  ["payments", "Платежи", "Деньги от покупателей и возвраты. В колонке «Зачтён в» видно, какой счёт оплачен."],
  ["ledger", "Журнал проводок", "Все проводки месяца в валюте учёта. В каждой проводке дебет равен кредиту."],
  ["fx", "Курсы валют", "Курсы публикуются по рабочим дням. В выходные действует последний опубликованный."],
];

function accName(code) {
  if (code.startsWith("bank:")) return `Банк, счёт в ${CCY_IN[code.slice(5)] || code.slice(5)}`;
  return ACCOUNT_RU[code] || code;
}
function accCell(code) { return `<span class="acc"><span>${esc(accName(code))}</span><small>${esc(code)}</small></span>`; }
function epLabel(split, name) {
  return split === "sample" ? "Пример" : `${SPLIT_RU[split] || split} №${Number(name.split("_").pop()) + 1}`;
}
function monthLabel(iso) { return `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`; }
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
function countWord(n, one, few, many) { return `${n} ${plural(n, one, few, many)}`; }
function listRu(items) { return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} и ${items[items.length - 1]}`; }
function roundingRu(mode) { return mode === "ROUND_HALF_EVEN" ? "банковское, к чётному" : "математическое, половина вверх"; }
function tzRu(tz) { return `UTC${tz.replace("-", "−")}`; }

const state = {
  episodes: [],
  src: null,
  data: null,
  ix: null,
  answers: store.get("auditgym.answers", false),
  tab: "invoices",
  f: {},
  page: 0,
};

/* Static mirror (GitHub Pages): ui/build_static.py writes the API responses as JSON files
   and a config.js that sets window.AUDITGYM_STATIC = { seeds: [...] }. */
const STATIC = window.AUDITGYM_STATIC || null;

function staticPath(path) {
  const url = new URL(path, "http://x");
  if (url.pathname === "/api/generate") {
    const preset = url.searchParams.get("preset");
    const seed = Number(url.searchParams.get("seed"));
    if (!STATIC.seeds.includes(seed)) {
      throw new Error(`Статическое зеркало: доступны варианты ${STATIC.seeds.join(", ")}. Любой seed — в живой версии или локально (python ui/server.py).`);
    }
    return `api/generate/${encodeURIComponent(preset)}/${seed}.json`;
  }
  return `${url.pathname.slice(1)}.json`;
}

async function api(path) {
  const res = await fetch(STATIC ? staticPath(path) : path);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

/* ------------------------------------------------------------------ index */
function buildIndex(data) {
  const p = data.public;
  const m = p.manifest;
  const ix = {
    m,
    exp: Object.fromEntries(Object.entries(m.currencies).map(([c, v]) => [c, v.exponent])),
    tzMin: parseTz(m.timezone),
    start: Date.parse(m.period_start),
    end: Date.parse(m.period_end),
    inv: new Map(p.invoices.map((x) => [x.invoice_id, x])),
    pay: new Map(p.payments.map((x) => [x.payment_id, x])),
    row: new Map(p.ledger.map((x) => [x.row_id, x])),
    fx: new Map(p.fx.map((x) => [`${x.date}/${x.source_currency}/${x.target_currency}`, x])),
    rowsByInv: new Map(),
    rowsByPay: new Map(),
    paysByInv: new Map(),
    applied: new Map(),
    affected: new Map(),
    evidence: new Map(),
    issues: data.truth ? data.truth.issues : [],
  };
  for (const r of p.ledger) {
    if (r.payment_id) push(ix.rowsByPay, r.payment_id, r);
    else if (r.invoice_id) push(ix.rowsByInv, r.invoice_id, r);
  }
  for (const pay of p.payments) {
    for (const ref of pay.invoice_refs) {
      push(ix.paysByInv, ref.invoice_id, { pay, applied: ref.applied_minor });
      ix.applied.set(ref.invoice_id, (ix.applied.get(ref.invoice_id) || 0) + ref.applied_minor);
    }
  }
  for (const is of ix.issues) {
    for (const a of is.affected_ids) push(ix.affected, a, is);
    for (const s of is.evidence_sets) for (const e of s) push(ix.evidence, e, is);
  }
  return ix;
}
function push(map, k, v) { if (!map.has(k)) map.set(k, []); map.get(k).push(v); }
function parseTz(s) { const sign = s[0] === "-" ? -1 : 1; return sign * (Number(s.slice(1, 3)) * 60 + Number(s.slice(4, 6))); }

/* ------------------------------------------------------------------ formatting */
function moneyText(minor, ccy) {
  const e = state.ix.exp[ccy] ?? 2;
  return `${(minor / 10 ** e).toLocaleString("ru-RU", { minimumFractionDigits: e, maximumFractionDigits: e })} ${ccy}`;
}
function money(minor, ccy) {
  const e = state.ix.exp[ccy] ?? 2;
  return `${(minor / 10 ** e).toLocaleString("ru-RU", { minimumFractionDigits: e, maximumFractionDigits: e })} <span class="faint">${esc(ccy)}</span>`;
}
function ruDateTime(ts) {
  const d = new Date(Date.parse(ts) + state.ix.tzMin * 60000).toISOString();
  return `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)} ${d.slice(11, 16)}`;
}
function isoDateRu(iso) { return `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`; }
function tsCell(ts) {
  const local = `<span class="mono">${esc(ruDateTime(ts))}</span>`;
  if (ts.endsWith(state.ix.m.timezone)) return local;
  return `${local}<span class="ts-local">в документе ${esc(ts.slice(0, 16).replace("T", " "))} UTC</span>`;
}
function inPeriod(ts) { const t = Date.parse(ts); return t >= state.ix.start && t < state.ix.end; }
function refLink(ref, label) {
  return `<span class="idlink" data-ref="${esc(ref)}" title="Открыть">${esc(label ?? ref.split(":").slice(1).join(":"))}</span>`;
}
function refHuman(ref) {
  const [t, ...rest] = ref.split(":");
  const id = rest.join(":");
  if (t === "fx") { const [d, s, g] = id.split("/"); return refLink(ref, `курс ${s}/${g} на ${isoDateRu(d)}`); }
  if (t === "ledger") return refLink(ref, `строка журнала ${id}`);
  if (t === "invoices") return refLink(ref, `счёт ${id}`);
  if (t === "payments") return refLink(ref, `платёж ${id}`);
  return refLink(ref);
}
function dash(v, ref) { return v == null ? '<span class="faint">—</span>' : ref ? refLink(ref) : esc(v); }
function hlClass(ref) {
  if (!state.answers) return "";
  if (state.ix.affected.has(ref)) return "hl-affected";
  if (state.ix.evidence.has(ref)) return "hl-evidence";
  return "";
}
function docOf(ref) {
  const [t, ...rest] = ref.split(":");
  const id = rest.join(":");
  return t === "invoices" ? `счёт ${id}` : t === "payments" ? `платёж ${id}` : id;
}
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

function issueText(is) {
  const amt = moneyText(is.expected_amount_minor, is.currency);
  const aff = is.affected_ids;
  const ev = is.evidence_sets[0] || [];
  if (is.type === "duplicate_invoice") {
    const orig = ev.find((x) => x.startsWith("invoices:") && !aff.includes(x));
    return `${cap(docOf(aff[0]))} повторяет счёт ${orig ? orig.slice(9) : "?"}: покупатель, позиции и сумма совпадают. Его провели второй раз, выручка завышена на ${amt}.`;
  }
  if (is.type === "payment_allocation") {
    const pay = aff.find((x) => x.startsWith("payments:"));
    const inv = aff.find((x) => x.startsWith("invoices:"));
    return `${cap(docOf(pay))} пришёл в оплату счёта ${inv.slice(9)}, но ${amt} не зачтены и лежат на нераспределённых поступлениях. Счёт числится неоплаченным.`;
  }
  if (is.type === "fx_conversion") {
    const fx = ev.find((x) => x.startsWith("fx:"));
    return `${cap(docOf(aff[0]))} пересчитан по курсу не той даты. Нужен курс на ${fx ? isoDateRu(fx.slice(3, 13)) : "?"}, это дата документа по времени компании. Расхождение ${amt}.`;
  }
  if (is.type === "period_cutoff") {
    return `${cap(docOf(aff[0]))} по времени компании относится к другому месяцу, но проведён в этом. Сумма ${amt}.`;
  }
  return is.description;
}

/* ------------------------------------------------------------------ routing */
function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "open" && parts.length >= 3) return { src: { kind: "open", split: parts[1], name: parts[2] }, tab: parts[3] };
  if (parts[0] === "gen" && parts.length >= 3) return { src: { kind: "gen", preset: parts[1], seed: Number(parts[2]) }, tab: parts[3] };
  return null;
}
function srcHash(src, tab) {
  const base = src.kind === "open" ? `#/open/${src.split}/${src.name}` : `#/gen/${src.preset}/${src.seed}`;
  return tab && tab !== "invoices" ? `${base}/${tab}` : base;
}
function sameSrc(a, b) { return a && b && JSON.stringify(a) === JSON.stringify(b); }

async function route() {
  const r = parseHash();
  if (!r) {
    const first = state.episodes[0];
    if (first) location.replace(srcHash({ kind: "open", split: first.split, name: first.name }));
    return;
  }
  state.tab = TABS.some(([k]) => k === r.tab) ? r.tab : "invoices";
  if (!sameSrc(r.src, state.src)) {
    state.src = r.src;
    state.f = {};
    state.page = 0;
    await loadEpisode();
  }
  renderAll();
}

async function loadEpisode() {
  $("#view").innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const s = state.src;
  try {
    state.data = s.kind === "open"
      ? await api(`/api/episodes/${encodeURIComponent(s.split)}/${encodeURIComponent(s.name)}`)
      : await api(`/api/generate?preset=${encodeURIComponent(s.preset)}&seed=${encodeURIComponent(s.seed)}`);
    state.ix = buildIndex(state.data);
  } catch (e) {
    state.data = null;
    state.ix = null;
    $("#company-head").innerHTML = `<h1>Не удалось загрузить компанию</h1><p class="lead">${esc(e.message)}</p>`;
    $("#view").innerHTML = "";
  }
  if (s.kind === "gen") {
    $("#gen-preset").value = s.preset;
    $("#gen-seed").value = s.seed;
  }
}

/* ------------------------------------------------------------------ render */
function renderAll() {
  renderSidebar();
  if (!state.ix) return;
  renderCompany();
  renderModes();
  renderAnswers();
  renderTabs();
  renderView();
}

function renderSidebar() {
  let html = "";
  for (const split of ["sample", "train", "dev"]) {
    const eps = state.episodes.filter((e) => e.split === split);
    if (!eps.length) continue;
    html += `<div class="ep-group"><div class="section-title">${SPLIT_GROUP_RU[split]}</div>`;
    for (const e of eps) {
      const active = state.src && state.src.kind === "open" && state.src.split === split && state.src.name === e.name;
      const badge = state.answers
        ? e.issues ? `<span class="chip bad">${countWord(e.issues, "ошибка", "ошибки", "ошибок")}</span>` : '<span class="chip good">без ошибок</span>'
        : "";
      html += `<a class="ep${active ? " active" : ""}" href="${srcHash({ kind: "open", split, name: e.name })}">
        <span class="ep-name">${esc(e.company_name)}</span>${badge}
        <span class="ep-sub">${esc(epLabel(split, e.name))}, ${esc(monthLabel(e.period_start))}</span>
      </a>`;
    }
    html += "</div>";
  }
  $("#episode-list").innerHTML = html;
}

function renderCompany() {
  const ix = state.ix;
  const m = ix.m;
  const p = state.data.public;
  const tag = state.src.kind === "open"
    ? epLabel(state.src.split, state.src.name)
    : `${PRESET_RU[state.src.preset] || state.src.preset}, вариант ${state.src.seed}`;
  const others = Object.keys(m.currencies).filter((c) => c !== m.base_currency);
  const lead = `Вымышленная компания, отчётный месяц <b>${esc(monthLabel(m.period_start))}</b>. Учёт ведётся в <b>${esc(CCY_IN[m.base_currency])}</b>${
    others.length ? `, часть документов в ${esc(listRu(others.map((c) => CCY_IN[c] || c)))}` : ""
  }. В данных ${countWord(p.invoices.length, "счёт", "счёта", "счетов")}, ${countWord(p.payments.length, "платёж", "платежа", "платежей")} и ${countWord(p.ledger.length, "строка", "строки", "строк")} в журнале проводок.`;
  $("#company-head").innerHTML = `
    <h1>${esc(m.company_name)}<span class="tag">${esc(tag)}</span></h1>
    <p class="lead">${lead}</p>
    <details class="rules"><summary>Правила учёта</summary><ul>
      <li>Период: с ${esc(ruDateTime(m.period_start))} до ${esc(ruDateTime(m.period_end))}, время компании ${esc(tzRu(m.timezone))}.</li>
      <li>Дата документа считается по времени компании, даже если в документе время указано в UTC.</li>
      <li>Валютные суммы пересчитываются по курсу на дату документа. В выходные действует последний опубликованный курс.</li>
      <li>При оплате валютного счёта долг списывается по курсу счёта, разница идёт в курсовые разницы.</li>
      <li>Округление ${esc(roundingRu(m.rounding.mode))}, до минимальной единицы валюты.</li>
    </ul></details>`;
}

function renderModes() {
  const n = state.ix.issues.length;
  const answersSub = n ? countWord(n, "ошибка", "ошибки", "ошибок") : "ошибок нет";
  $("#modes").innerHTML = `
    <button class="mode${state.answers ? "" : " active"}" data-mode="data" role="tab" aria-selected="${!state.answers}"><b>Данные</b><span>то, что видит агент</span></button>
    <button class="mode answers${state.answers ? " active" : ""}" data-mode="answers" role="tab" aria-selected="${state.answers}"><b>Ответы</b><span>${esc(answersSub)}</span></button>`;
  $("#mode-hint").textContent = state.answers
    ? "Ошибки выделены в таблицах красным, доказательства жёлтым. Агенту эта информация недоступна."
    : "Агент получает только эти документы и должен сам найти ошибки. Переключитесь на «Ответы», чтобы проверить себя.";
}

function renderAnswers() {
  const box = $("#answers");
  if (!state.answers) { box.innerHTML = ""; return; }
  const ix = state.ix;
  const pv = state.data.provenance;
  let body;
  if (!ix.issues.length) {
    body = `<div class="clean-box"><b>Ошибок нет</b>Правильный ответ агента здесь пустой список. Любое обвинение снизит награду.</div>`;
  } else {
    const seen = new Set();
    body = `<div class="issues">${ix.issues.map((is, i) => {
      let label = "";
      if (is.group && !seen.has(is.group)) {
        seen.add(is.group);
        label = `<div class="group-label"><b>${esc(GROUP_RU[is.group[0]] || is.group)}</b></div>`;
      }
      const diff = is.affected_ids.map((a) => pv?.ledger_diff?.[a]).find(Boolean);
      return `${label}<article class="issue t-${esc(is.type)}">
        <div class="issue-top"><span class="issue-n">${i + 1}</span><span class="issue-type">${esc(TYPE_RU[is.type] || is.type)}</span><span class="issue-amount">${money(is.expected_amount_minor, is.currency)}</span></div>
        <p class="issue-text">${esc(issueText(is))}</p>
        <div class="issue-meta">
          <div>Где</div><div>${is.affected_ids.map(refHuman).join(", ")}</div>
          <div>Доказательства</div><div>${is.evidence_sets.map((s) => s.map(refHuman).join(", ")).join("<br>или ")}</div>
        </div>
        <div class="issue-actions">
          <button class="btn small" data-open="${esc(is.affected_ids[0])}">Открыть документ</button>
          ${diff ? `<details><summary class="link-btn">Проводка до и после</summary>${diffBlock(diff)}</details>` : ""}
        </div>
      </article>`;
    }).join("")}</div>`;
  }
  const traps = (pv?.distractors || []).filter((d) => d.ids.length);
  const trapsHtml = traps.length
    ? `<div class="traps"><h3>Похоже на ошибку, но нет</h3><p>Эти случаи учтены правильно. Агент не должен их отмечать.</p>
        <div class="trap-list">${traps.map((d) => `<div class="trap"><b>${esc(DISTRACTOR_RU[d.kind] || d.kind)}</b><span>${d.ids.map(refHuman).join(", ")}</span></div>`).join("")}</div></div>`
    : "";
  box.innerHTML = `<div class="answers-wrap"><h2 class="section-h">${ix.issues.length ? `Ошибки в этой компании: ${ix.issues.length}` : "Ошибки в этой компании"}</h2>${body}${trapsHtml}</div>`;
}

function diffBlock(d) {
  const base = state.ix.m.base_currency;
  const amt = (v) => (v ? moneyText(v, base) : "—").padStart(20);
  const fmt = (rows) => (rows ? rows.map((r) => `${accName(r.account).padEnd(30)} Дт ${amt(r.debit_minor)}   Кт ${amt(r.credit_minor)}`).join("\n") : "проводки не было");
  return `<div class="diff"><div><h4>До</h4><pre>${esc(fmt(d.before))}</pre></div><div><h4>После</h4><pre class="after">${esc(fmt(d.after))}</pre></div></div>`;
}

function renderTabs() {
  const p = state.data.public;
  const counts = { invoices: p.invoices.length, payments: p.payments.length, ledger: p.ledger.length, fx: p.fx.length };
  $("#tabs").innerHTML = TABS.map(([k, label]) => `<button class="tab${k === state.tab ? " active" : ""}" data-tab="${k}" role="tab">${label}<span class="count">${counts[k]}</span></button>`).join("");
}

function renderView() {
  const help = TABS.find(([k]) => k === state.tab)[2];
  const fn = { invoices: viewInvoices, payments: viewPayments, ledger: viewLedger, fx: viewFx }[state.tab];
  $("#view").innerHTML = `<p class="tab-help">${esc(help)}</p>${fn()}`;
}

/* ------------------------------------------------------------------ tables */
function filterInput(key, placeholder) {
  return `<input class="search" data-f="${key}" placeholder="${esc(placeholder)}" value="${esc(state.f[key] || "")}">`;
}
function filterSelect(key, options, label) {
  return `<select data-f="${key}" aria-label="${esc(label)}"><option value="">${esc(label)}</option>${options
    .map(([v, t]) => `<option value="${esc(v)}"${state.f[key] === v ? " selected" : ""}>${esc(t)}</option>`)
    .join("")}</select>`;
}
function hlOption(options) { return state.answers ? [...options, ["hl", "только с ошибками"]] : options; }
function legend() { return state.answers ? '<div class="legend"><span class="l-aff">ошибка</span><span class="l-ev">доказательство</span></div>' : ""; }
function paged(rows, renderRow, cols, empty) {
  const size = 50;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  if (state.page >= pages) state.page = pages - 1;
  const slice = rows.slice(state.page * size, state.page * size + size);
  const body = slice.length ? slice.map(renderRow).join("") : `<tr><td colspan="${cols}"><div class="empty">${empty}</div></td></tr>`;
  const pager = pages > 1
    ? `<div class="pager"><span>${state.page * size + 1}–${Math.min(rows.length, (state.page + 1) * size)} из ${rows.length}</span>
       <span><button class="btn small" data-page="-1"${state.page === 0 ? " disabled" : ""}>Назад</button> <button class="btn small" data-page="1"${state.page >= pages - 1 ? " disabled" : ""}>Далее</button></span></div>`
    : "";
  return { body, pager };
}
function matches(obj, q, extra = "") {
  if (!q) return true;
  return (JSON.stringify(obj) + " " + extra).toLowerCase().includes(q.toLowerCase());
}
function unapplied(x) { return x.amount_minor - x.invoice_refs.reduce((s, r) => s + r.applied_minor, 0); }

function viewInvoices() {
  const p = state.data.public;
  const ix = state.ix;
  const f = state.f;
  let rows = p.invoices.filter((i) => matches(i, f.q));
  if (f.ccy) rows = rows.filter((i) => i.currency === f.ccy);
  if (f.st === "paid") rows = rows.filter((i) => i.total_minor - (ix.applied.get(i.invoice_id) || 0) === 0);
  if (f.st === "open") rows = rows.filter((i) => i.total_minor - (ix.applied.get(i.invoice_id) || 0) > 0);
  if (f.st === "other") rows = rows.filter((i) => !ix.rowsByInv.has(i.invoice_id));
  if (f.st === "hl") rows = rows.filter((i) => hlClass(`invoices:${i.invoice_id}`));
  rows = [...rows].sort((a, b) => (inPeriod(a.issued_at) ? 0 : 1) - (inPeriod(b.issued_at) ? 0 : 1) || Date.parse(a.issued_at) - Date.parse(b.issued_at));
  const { body, pager } = paged(rows, (i) => {
    const paid = ix.applied.get(i.invoice_id) || 0;
    const open = i.total_minor - paid;
    const status = open === 0 ? '<span class="chip good">оплачен</span>'
      : paid ? `<span class="chip warn">оплачен частично</span><span class="cell-sub">осталось ${money(open, i.currency)}</span>`
      : '<span class="chip">не оплачен</span>';
    const other = ix.rowsByInv.has(i.invoice_id) ? "" : '<span class="cell-sub">другой месяц, не проведён</span>';
    const item = i.line_items[0].description.split(" · ")[0];
    return `<tr class="${hlClass(`invoices:${i.invoice_id}`)}" data-ref="invoices:${esc(i.invoice_id)}">
      <td class="mono">${esc(i.invoice_id)}</td><td>${tsCell(i.issued_at)}</td>
      <td>${esc(item)}${i.line_items.length > 1 ? ` <span class="faint">и ещё ${i.line_items.length - 1}</span>` : ""}<span class="cell-sub">покупатель ${esc(i.entity_id)}</span></td>
      <td class="num">${money(i.total_minor, i.currency)}</td><td>${status}${other}</td></tr>`;
  }, 5, "Ничего не найдено");
  const ccys = Object.keys(ix.m.currencies).map((c) => [c, `${CCY_RU[c] || c} (${c})`]);
  return `<div class="card"><div class="toolbar">${filterInput("q", "Номер, покупатель, товар")}${filterSelect("ccy", ccys, "Все валюты")}${filterSelect("st", hlOption([["paid", "оплачен"], ["open", "есть долг"], ["other", "другой месяц"]]), "Все счета")}${legend()}</div>
    <div class="table-wrap"><table><thead><tr><th>Номер</th><th>Дата</th><th>Что продано</th><th class="num">Сумма</th><th>Статус</th></tr></thead><tbody>${body}</tbody></table></div>${pager}</div>`;
}

function viewPayments() {
  const p = state.data.public;
  const ix = state.ix;
  const f = state.f;
  let rows = p.payments.filter((x) => matches(x, f.q));
  if (f.st === "receipt" || f.st === "refund") rows = rows.filter((x) => x.kind === f.st);
  if (f.st === "rest") rows = rows.filter((x) => x.kind === "receipt" && unapplied(x) > 0);
  if (f.st === "hl") rows = rows.filter((x) => hlClass(`payments:${x.payment_id}`));
  rows = [...rows].sort((a, b) => (inPeriod(a.timestamp) ? 0 : 1) - (inPeriod(b.timestamp) ? 0 : 1) || Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const { body, pager } = paged(rows, (x) => {
    const ua = x.kind === "receipt" ? unapplied(x) : 0;
    const target = x.kind === "refund"
      ? `возврат по ${refLink(`payments:${x.refund_of}`)}`
      : x.invoice_refs.map((r) => refLink(`invoices:${r.invoice_id}`)).join(", ") || '<span class="chip warn">никуда</span>';
    const other = ix.rowsByPay.has(x.payment_id) ? "" : '<span class="cell-sub">другой месяц, не проведён</span>';
    return `<tr class="${hlClass(`payments:${x.payment_id}`)}" data-ref="payments:${esc(x.payment_id)}">
      <td class="mono">${esc(x.payment_id)}</td><td>${tsCell(x.timestamp)}${other}</td>
      <td>${x.kind === "refund" ? '<span class="chip warn">возврат</span>' : "поступление"}<span class="cell-sub">покупатель ${esc(x.entity_id)}</span></td>
      <td class="num">${money(x.amount_minor, x.currency)}</td><td>${target}${ua ? `<span class="cell-sub">не зачтено ${money(ua, x.currency)}</span>` : ""}</td></tr>`;
  }, 5, "Ничего не найдено");
  return `<div class="card"><div class="toolbar">${filterInput("q", "Номер, покупатель, счёт")}${filterSelect("st", hlOption([["receipt", "поступления"], ["refund", "возвраты"], ["rest", "есть незачтённый остаток"]]), "Все платежи")}${legend()}</div>
    <div class="table-wrap"><table><thead><tr><th>Номер</th><th>Дата</th><th>Тип</th><th class="num">Сумма</th><th>Зачтён в</th></tr></thead><tbody>${body}</tbody></table></div>${pager}</div>`;
}

function viewLedger() {
  const p = state.data.public;
  const base = state.ix.m.base_currency;
  const accounts = [...new Set(p.ledger.map((r) => r.account))].sort((a, b) => accName(a).localeCompare(accName(b), "ru"));
  const f = state.f;
  let rows = p.ledger.filter((r) => matches(r, f.q, accName(r.account)));
  if (f.account) rows = rows.filter((r) => r.account === f.account);
  if (f.st === "doc") rows = rows.filter((r) => r.invoice_id || r.payment_id);
  if (f.st === "bg") rows = rows.filter((r) => !r.invoice_id && !r.payment_id);
  if (f.st === "hl") rows = rows.filter((r) => hlClass(`ledger:${r.row_id}`));
  const { body, pager } = paged(rows, (r) => `<tr class="${hlClass(`ledger:${r.row_id}`)}" data-ref="ledger:${esc(r.row_id)}">
      <td>${tsCell(r.timestamp)}</td><td>${accCell(r.account)}</td>
      <td class="num">${r.debit_minor ? money(r.debit_minor, base) : ""}</td><td class="num">${r.credit_minor ? money(r.credit_minor, base) : ""}</td>
      <td>${r.invoice_id ? refLink(`invoices:${r.invoice_id}`) : r.payment_id ? refLink(`payments:${r.payment_id}`) : '<span class="faint">—</span>'}${r.invoice_id && r.payment_id ? `<span class="cell-sub">платёж ${refLink(`payments:${r.payment_id}`)}</span>` : ""}</td></tr>`, 5, "Ничего не найдено");
  return `<div class="card"><div class="toolbar">${filterInput("q", "Номер, счёт учёта, сумма")}${filterSelect("account", accounts.map((a) => [a, accName(a)]), "Все счета учёта")}${filterSelect("st", hlOption([["doc", "по счетам и платежам"], ["bg", "прочие операции"]]), "Все строки")}${legend()}</div>
    <div class="table-wrap"><table><thead><tr><th>Дата</th><th>Счёт учёта</th><th class="num">Дебет</th><th class="num">Кредит</th><th>Документ</th></tr></thead><tbody>${body}</tbody></table></div>${pager}</div>`;
}

function viewFx() {
  const p = state.data.public;
  const ix = state.ix;
  if (!p.fx.length) return '<div class="card"><div class="empty">У компании одна валюта, курсов нет.</div></div>';
  const by = new Map();
  for (const r of p.fx) push(by, r.source_currency, r);
  const charts = [...by.entries()].map(([ccy, rows]) => `<div class="card fx-card"><h3>${esc(ccy)} к ${esc(ix.m.base_currency)}</h3>${fxChart(rows)}</div>`).join("");
  const { body, pager } = paged(p.fx.filter((r) => matches(r, state.f.q)), (r) => {
    const ref = `fx:${r.date}/${r.source_currency}/${r.target_currency}`;
    return `<tr class="${hlClass(ref)}" data-ref="${esc(ref)}"><td class="mono">${esc(isoDateRu(r.date))}</td><td>1 ${esc(r.source_currency)}</td><td class="num">${esc(r.rate)} ${esc(r.target_currency)}</td></tr>`;
  }, 3, "Ничего не найдено");
  return `<div class="fx-charts">${charts}</div>
    <div class="card"><div class="toolbar">${filterInput("q", "Дата (2026-03-14) или валюта")}${legend()}</div>
    <div class="table-wrap"><table><thead><tr><th>Дата</th><th>Валюта</th><th class="num">Курс</th></tr></thead><tbody>${body}</tbody></table></div>${pager}</div>`;
}
function fxChart(rows) {
  const ix = state.ix;
  const W = 640, H = 170, L = 64, R = 10, T = 10, B = 24;
  const xs = rows.map((r) => Date.parse(r.date + "T12:00:00Z"));
  const ys = rows.map((r) => Number(r.rate));
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.1 || y0 * 0.01;
  const X = (t) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - (y0 - pad)) / (y1 - y0 + 2 * pad)) * (H - T - B);
  const path = xs.map((t, i) => `${i ? "L" : "M"}${X(t).toFixed(1)} ${Y(ys[i]).toFixed(1)}`).join(" ");
  const ps = Math.max(L, X(ix.start)), pe = Math.min(W - R, X(ix.end));
  const fmtV = (v) => v.toLocaleString("ru-RU", { maximumSignificantDigits: 6 });
  const dots = state.answers
    ? rows.map((r, i) => (ix.evidence.has(`fx:${r.date}/${r.source_currency}/${r.target_currency}`) ? `<circle cx="${X(xs[i])}" cy="${Y(ys[i])}" r="4.5" fill="var(--warn)"><title>Доказательство, ${isoDateRu(r.date)}</title></circle>` : "")).join("")
    : "";
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Курс по дням">
    <rect x="${ps}" y="${T}" width="${Math.max(0, pe - ps)}" height="${H - T - B}" fill="var(--accent-soft)"/>
    <text x="${(ps + pe) / 2}" y="${T + 14}" text-anchor="middle">отчётный месяц</text>
    <line class="grid" x1="${L}" x2="${W - R}" y1="${Y(y1)}" y2="${Y(y1)}"/><line class="grid" x1="${L}" x2="${W - R}" y1="${Y(y0)}" y2="${Y(y0)}"/>
    <text x="${L - 6}" y="${Y(y1) + 4}" text-anchor="end">${fmtV(y1)}</text><text x="${L - 6}" y="${Y(y0) + 4}" text-anchor="end">${fmtV(y0)}</text>
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>${dots}
    <text x="${L}" y="${H - 6}">${isoDateRu(rows[0].date)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${isoDateRu(rows[rows.length - 1].date)}</text></svg>`;
}

/* ------------------------------------------------------------------ drawer */
function openRecord(ref) {
  const ix = state.ix;
  const [table, ...rest] = ref.split(":");
  const id = rest.join(":");
  let html;
  if (table === "invoices" && ix.inv.has(id)) html = drawerInvoice(ix.inv.get(id));
  else if (table === "payments" && ix.pay.has(id)) html = drawerPayment(ix.pay.get(id));
  else if (table === "ledger" && ix.row.has(id)) html = drawerRow(ix.row.get(id));
  else if (table === "fx" && ix.fx.has(id)) html = drawerFx(ix.fx.get(id));
  else html = '<div class="empty">Запись не найдена</div>';
  const title = table === "fx" ? `${id.split("/").slice(1).join("/")} на ${isoDateRu(id.slice(0, 10))}` : id;
  $("#drawer-title").innerHTML = `<div class="kind">${esc(TABLE_RU[table] || table)}</div><div class="rid">${esc(title)}</div>`;
  $("#drawer-body").innerHTML = issuesFor(ref) + html;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  $("#drawer-body").scrollTop = 0;
}
function closeOverlays() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
  $("#help").hidden = true;
}
function issuesFor(ref) {
  if (!state.answers) return "";
  const aff = state.ix.affected.get(ref) || [];
  const ev = (state.ix.evidence.get(ref) || []).filter((i) => !aff.includes(i));
  const box = (i, cls, role) => `<div class="callout ${cls}"><b>${role}: ${esc(TYPE_RU[i.type])}</b><br>${esc(issueText(i))}</div>`;
  return aff.map((i) => box(i, "", "Ошибка")).join("") + ev.map((i) => box(i, "ev", "Доказательство")).join("");
}
function kv(pairs) { return `<div class="kv">${pairs.map(([k, v]) => `<div>${esc(k)}</div><div>${v}</div>`).join("")}</div>`; }
function ledgerTable(rows) {
  if (!rows || !rows.length) return '<p class="muted">В этом месяце проводок нет.</p>';
  const base = state.ix.m.base_currency;
  return `<table><thead><tr><th>Счёт учёта</th><th class="num">Дебет</th><th class="num">Кредит</th></tr></thead><tbody>${rows
    .map((r) => `<tr class="${hlClass(`ledger:${r.row_id}`)}" data-ref="ledger:${esc(r.row_id)}"><td>${accCell(r.account)}</td><td class="num">${r.debit_minor ? money(r.debit_minor, base) : ""}</td><td class="num">${r.credit_minor ? money(r.credit_minor, base) : ""}</td></tr>`)
    .join("")}</tbody></table>`;
}
function periodNote(ts) { return inPeriod(ts) ? "" : ' <span class="chip warn">другой месяц</span>'; }
function drawerInvoice(i) {
  const ix = state.ix;
  const pays = ix.paysByInv.get(i.invoice_id) || [];
  const open = i.total_minor - (ix.applied.get(i.invoice_id) || 0);
  return `<section>${kv([
    ["Покупатель", `<span class="mono">${esc(i.entity_id)}</span>`],
    ["Дата", tsCell(i.issued_at) + periodNote(i.issued_at)],
    ["Срок оплаты", tsCell(i.due_at)],
    ["Сумма", money(i.total_minor, i.currency)],
    ["Остаток долга", open ? money(open, i.currency) : '<span class="chip good">оплачен</span>'],
  ])}</section>
  <section><h3>Позиции</h3><table><thead><tr><th>Позиция</th><th class="num">Кол-во</th><th class="num">Цена</th><th class="num">Сумма</th></tr></thead><tbody>${i.line_items
    .map((li) => `<tr><td>${esc(li.description)}</td><td class="num">${li.quantity}</td><td class="num">${money(li.unit_price_minor, i.currency)}</td><td class="num">${money(li.amount_minor, i.currency)}</td></tr>`)
    .join("")}</tbody></table></section>
  <section><h3>Оплаты</h3>${pays.length ? `<table><tbody>${pays.map(({ pay, applied }) => `<tr data-ref="payments:${esc(pay.payment_id)}"><td>${refLink(`payments:${pay.payment_id}`)}</td><td>${tsCell(pay.timestamp)}</td><td class="num">${money(applied, i.currency)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">Оплат нет.</p>'}</section>
  <section><h3>Проводка</h3>${ledgerTable(ix.rowsByInv.get(i.invoice_id))}</section>`;
}
function drawerPayment(x) {
  const ix = state.ix;
  const ua = x.kind === "receipt" ? unapplied(x) : 0;
  return `<section>${kv([
    ["Тип", x.kind === "refund" ? "возврат покупателю" : "поступление от покупателя"],
    ["Покупатель", `<span class="mono">${esc(x.entity_id)}</span>`],
    ["Дата", tsCell(x.timestamp) + periodNote(x.timestamp)],
    ["Сумма", money(x.amount_minor, x.currency)],
    ...(ua ? [["Не зачтено", `<span class="chip warn">${money(ua, x.currency)}</span>`]] : []),
    ...(x.refund_of ? [["Возврат по", refLink(`payments:${x.refund_of}`)]] : []),
  ])}</section>
  <section><h3>Зачтено в счета</h3>${x.invoice_refs.length ? `<table><tbody>${x.invoice_refs.map((r) => {
    const inv = ix.inv.get(r.invoice_id);
    return `<tr data-ref="invoices:${esc(r.invoice_id)}"><td>${refLink(`invoices:${r.invoice_id}`)}</td><td class="num">${money(r.applied_minor, x.currency)}</td><td class="num faint">из ${inv ? money(inv.total_minor, inv.currency) : "?"}</td></tr>`;
  }).join("")}</tbody></table>` : '<p class="muted">Ни в один счёт.</p>'}</section>
  <section><h3>Проводка</h3>${ledgerTable(ix.rowsByPay.get(x.payment_id))}</section>`;
}
function drawerRow(r) {
  const ix = state.ix;
  const base = ix.m.base_currency;
  let siblings;
  if (r.payment_id) siblings = ix.rowsByPay.get(r.payment_id);
  else if (r.invoice_id) siblings = ix.rowsByInv.get(r.invoice_id);
  else siblings = state.data.public.ledger.filter((x) => x.timestamp === r.timestamp && !x.invoice_id && !x.payment_id);
  return `<section>${kv([
    ["Дата", tsCell(r.timestamp)],
    ["Счёт учёта", accCell(r.account)],
    [r.debit_minor ? "Дебет" : "Кредит", money(r.debit_minor || r.credit_minor, base)],
    ["Покупатель", dash(r.entity_id)],
    ["Счёт", dash(r.invoice_id, r.invoice_id && `invoices:${r.invoice_id}`)],
    ["Платёж", dash(r.payment_id, r.payment_id && `payments:${r.payment_id}`)],
  ])}</section>
  <section><h3>Вся проводка</h3>${ledgerTable(siblings)}</section>`;
}
function drawerFx(r) {
  const same = state.data.public.fx.filter((x) => x.source_currency === r.source_currency);
  const i = same.indexOf(r);
  const near = same.slice(Math.max(0, i - 3), i + 4);
  return `<section>${kv([["Дата", esc(isoDateRu(r.date))], ["Курс", `1 ${esc(r.source_currency)} = <span class="mono">${esc(r.rate)}</span> ${esc(r.target_currency)}`]])}</section>
  <section><h3>Соседние дни</h3><table><tbody>${near.map((x) => {
    const ref = `fx:${x.date}/${x.source_currency}/${x.target_currency}`;
    return `<tr class="${x === r ? "hl-evidence" : hlClass(ref)}" data-ref="${esc(ref)}"><td class="mono">${esc(isoDateRu(x.date))}</td><td class="num">${esc(x.rate)}</td></tr>`;
  }).join("")}</tbody></table></section>`;
}

function renderHelp() {
  $("#help-body").innerHTML = `
    <p class="muted">Каждая компания вымышлена и описана за один месяц: счета покупателям, платежи, курсы валют и журнал проводок. Генератор вносит в данные ошибки и сохраняет правильные ответы отдельно. ИИ-агент видит только документы, ищет ошибки и подкрепляет каждую ссылками на записи. Затем его ответ сравнивают с эталоном.</p>
    <ol class="help-steps">
      <li>Выберите компанию слева или создайте новую.</li>
      <li>В режиме «Данные» посмотрите документы так, как их видит агент. Клик по строке открывает подробности.</li>
      <li>Переключитесь на «Ответы»: появится список ошибок, а в таблицах они будут выделены.</li>
    </ol>
    <div class="help-types">${Object.keys(TYPE_RU).map((t) => `<div class="help-type t-${t}"><b>${esc(TYPE_RU[t])}</b>${esc(TYPE_HINT[t])}</div>`).join("")}</div>
    <p class="muted" style="margin-bottom:0">Частичные оплаты, возвраты, одинаковые суммы и время в UTC около полуночи ошибками не считаются.</p>`;
}

/* ------------------------------------------------------------------ events */
function setTab(tab) {
  state.tab = tab;
  state.f = {};
  state.page = 0;
  history.replaceState(null, "", srcHash(state.src, tab));
  renderTabs();
  renderView();
}
function setAnswers(on) {
  state.answers = on;
  store.set("auditgym.answers", on);
  closeOverlays();
  renderAll();
}

document.addEventListener("click", (ev) => {
  const t = ev.target;
  const link = t.closest(".idlink");
  if (link) { ev.stopPropagation(); openRecord(link.dataset.ref); return; }
  const opener = t.closest("[data-open]");
  if (opener) { openRecord(opener.dataset.open); return; }
  const mode = t.closest("[data-mode]");
  if (mode) { setAnswers(mode.dataset.mode === "answers"); return; }
  const tab = t.closest("[data-tab]");
  if (tab) { setTab(tab.dataset.tab); return; }
  const pageBtn = t.closest("[data-page]");
  if (pageBtn) { state.page += Number(pageBtn.dataset.page); renderView(); return; }
  const row = t.closest("tr[data-ref]");
  if (row) { openRecord(row.dataset.ref); return; }
  if (t.closest(".ep") && window.innerWidth <= 900) $("#sidebar").classList.remove("open");
});

let searchTimer;
document.addEventListener("input", (ev) => {
  const el = ev.target;
  if (!el.dataset.f) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.f[el.dataset.f] = el.value;
    state.page = 0;
    const pos = el.selectionStart;
    renderView();
    const again = document.querySelector(`[data-f="${el.dataset.f}"]`);
    if (again && again.tagName === "INPUT") { again.focus(); again.setSelectionRange(pos, pos); }
  }, el.tagName === "SELECT" ? 0 : 180);
});

$("#drawer-close").addEventListener("click", closeOverlays);
$("#scrim").addEventListener("click", closeOverlays);
$("#help").addEventListener("click", (ev) => { if (ev.target.id === "help") closeOverlays(); });
$("#help-close").addEventListener("click", closeOverlays);
$("#help-btn").addEventListener("click", () => { renderHelp(); $("#help").hidden = false; });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeOverlays(); });
$("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#gen-toggle").addEventListener("click", () => {
  const form = $("#gen-form");
  form.hidden = !form.hidden;
  $("#gen-toggle").setAttribute("aria-expanded", String(!form.hidden));
});
$("#gen-dice").addEventListener("click", () => {
  $("#gen-seed").value = STATIC
    ? STATIC.seeds[Math.floor(Math.random() * STATIC.seeds.length)]
    : Math.floor(Math.random() * 1e6);
});
$("#gen-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const seed = Number($("#gen-seed").value);
  if (!Number.isInteger(seed) || seed < 0 || seed > 1e9) { $("#gen-seed").focus(); return; }
  location.hash = srcHash({ kind: "gen", preset: $("#gen-preset").value, seed });
  if (window.innerWidth <= 900) $("#sidebar").classList.remove("open");
});
$("#theme-btn").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("auditgym.theme", next);
});
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ boot */
(async function boot() {
  document.documentElement.dataset.theme = store.get("auditgym.theme", "light");
  const review = new URLSearchParams(location.search).get("review");
  if (review === "1" || review === "0") state.answers = review === "1";
  try {
    const [pr, eps] = await Promise.all([api("/api/presets"), api("/api/episodes")]);
    state.episodes = eps.episodes;
    $("#gen-preset").innerHTML = pr.presets.map((p) => `<option value="${esc(p)}"${p === "interactions" ? " selected" : ""}>${esc(PRESET_RU[p] || p)}</option>`).join("");
    $("#side-foot").innerHTML = `Все данные вымышлены.<br>Генератор ${esc(pr.generator_version)}`;
  } catch (e) {
    $("#company-head").innerHTML = `<h1>Сервер недоступен</h1><p class="lead">${esc(e.message)}</p>`;
    return;
  }
  await route();
})();
