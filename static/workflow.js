const els = {
  authGate: document.getElementById("authGate"),
  loginForm: document.getElementById("loginForm"),
  loginUser: document.getElementById("loginUser"),
  loginPass: document.getElementById("loginPass"),
  loginSubmit: document.getElementById("loginSubmit"),
  loginError: document.getElementById("loginError"),
  logoutButton: document.getElementById("logoutButton"),
  guideModal: document.getElementById("guideModal"),
  guideContent: document.getElementById("guideContent"),
  guideCloseButton: document.getElementById("guideCloseButton"),
  guideConfirmButton: document.getElementById("guideConfirmButton"),
  sentenceModeModal: document.getElementById("sentenceModeModal"),
  sentenceModeCloseButton: document.getElementById("sentenceModeCloseButton"),
  sentenceModeDirectButton: document.getElementById("sentenceModeDirectButton"),
  sentenceModeGeneratedButton: document.getElementById("sentenceModeGeneratedButton"),
  themeSwitch: document.getElementById("themeSwitch"),
  keywordModel: document.getElementById("keywordModel"),
  keywordBaseUrl: document.getElementById("keywordBaseUrl"),
  toggleArxiv: document.getElementById("toggleArxiv"),
  arxivQuery: document.getElementById("arxivQuery"),
  sourceCardArxiv: document.getElementById("sourceCardArxiv"),
  sourceCardSpringer: document.getElementById("sourceCardSpringer"),
  sourceCardElsevier: document.getElementById("sourceCardElsevier"),
  sourceCardIEEE: document.getElementById("sourceCardIEEE"),
  recommendCount: document.getElementById("recommendCount"),
  sampleRatio: document.getElementById("sampleRatio"),
  selectedKeywordCount: document.getElementById("selectedKeywordCount"),
  candidatePool: document.getElementById("candidatePool"),
  extraKeywordCount: document.getElementById("extraKeywordCount"),
  extraCandidatePool: document.getElementById("extraCandidatePool"),
  toggleSpringer: document.getElementById("toggleSpringer"),
  toggleElsevier: document.getElementById("toggleElsevier"),
  toggleIEEE: document.getElementById("toggleIEEE"),
  writeToZotero: document.getElementById("writeToZotero"),
  extraKeywordInput: document.getElementById("extraKeywordInput"),
  ignorePaths: document.getElementById("ignorePaths"),
  historyList: document.getElementById("historyList"),
  startButton: document.getElementById("startButton"),
  statusBadge: document.getElementById("statusBadge"),
  statusMessage: document.getElementById("statusMessage"),
  terminalMeta: document.getElementById("terminalMeta"),
  terminalOutput: document.getElementById("terminalOutput"),
  resultsGrid: document.getElementById("resultsGrid"),
  resultsCount: document.getElementById("resultsCount"),
  resultSourceFilter: document.getElementById("resultSourceFilter"),
  accountMeta: document.getElementById("accountMeta"),
  bindingsStatus: document.getElementById("bindingsStatus"),
  bindingZoteroId: document.getElementById("bindingZoteroId"),
  bindingZoteroKey: document.getElementById("bindingZoteroKey"),
  bindingSpringerKey: document.getElementById("bindingSpringerKey"),
  bindingElsevierKey: document.getElementById("bindingElsevierKey"),
  bindingIEEEKey: document.getElementById("bindingIEEEKey"),
  saveBindingsButton: document.getElementById("saveBindingsButton"),
  adminPanel: document.getElementById("adminPanel"),
  adminCreateUserForm: document.getElementById("adminCreateUserForm"),
  adminNewUsername: document.getElementById("adminNewUsername"),
  adminNewPassword: document.getElementById("adminNewPassword"),
  adminCreateUserButton: document.getElementById("adminCreateUserButton"),
  adminUserStatus: document.getElementById("adminUserStatus"),
  adminUserList: document.getElementById("adminUserList"),
};

const GUIDE_SEEN_STORAGE_PREFIX = "workflow-guide-seen:";
const DEFAULT_REQUEST_TIMEOUT_MS = 15000;
const POLL_REQUEST_TIMEOUT_MS = 6000;
const MAX_POLL_FAILURES = 5;

const state = {
  jobId: null,
  pollTimer: null,
  pollFailures: 0,
  lines: [],
  history: [],
  recommendations: [],
  currentJob: null,
  authenticated: false,
  guideLoaded: false,
  sentenceModeResolver: null,
  currentUser: null,
  currentRole: null,
  bindings: null,
};
const SOURCE_ORDER = ["arXiv", "Springer", "Elsevier", "IEEE"];
const JOURNAL_RANK_LABELS = {
  sci: "JCR分区",
  sciBase: "基础版",
  sciUp: "升级版",
  sciUpSmall: "升级小类",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function nowTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function formatHistoryTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatPublishedDate(value) {
  if (!value) {
    return "";
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value;
  }
  if (/^\d{4}-\d{2}$/.test(value) || /^\d{4}$/.test(value)) {
    return value;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatAuthors(authors) {
  if (!Array.isArray(authors) || !authors.length) {
    return "Unknown";
  }
  return authors.join(", ");
}

function resolvePaperLink(paper) {
  if (paper.url) {
    return paper.url;
  }
  if (paper.doi) {
    return `https://doi.org/${encodeURIComponent(paper.doi)}`;
  }
  return "";
}

function applyTheme(theme) {
  const isLight = theme === "light";
  document.body.classList.toggle("theme-light", isLight);
  if (els.themeSwitch) {
    els.themeSwitch.checked = isLight;
  }
}

function initTheme() {
  const saved = window.localStorage.getItem("workflow-theme");
  const theme = saved === "dark" ? "dark" : "light";
  applyTheme(theme);
}

function renderJournalRank(rank, source) {
  if (!rank || typeof rank !== "object") {
    if (source && source !== "arXiv") {
      return '<span class="rank-empty">暂无分区</span>';
    }
    return "";
  }
  const items = Object.entries(rank)
    .filter(([, value]) => value)
    .map(
      ([key, value]) =>
        `<span class="rank-tag">${escapeHtml(JOURNAL_RANK_LABELS[key] || key)} ${escapeHtml(value)}</span>`,
    );
  if (!items.length) {
    if (source && source !== "arXiv") {
      return '<span class="rank-empty">暂无分区</span>';
    }
    return "";
  }
  return `<div class="rank-list">${items.join("")}</div>`;
}

function renderHistoryList(items = []) {
  state.history = Array.isArray(items) ? items : [];
  if (!state.history.length) {
    els.historyList.innerHTML = '<div class="empty">暂无历史推荐</div>';
    return;
  }

  els.historyList.innerHTML = state.history
    .map((item) => {
      const active = state.currentJob?.job_id === item.job_id ? " active" : "";
      const sources = Array.isArray(item.enabled_sources) ? item.enabled_sources.join(" / ") : "";
      const keywords = Array.isArray(item.selected_keywords)
        ? item.selected_keywords.slice(0, 3).join(" · ")
        : "";
      const owner = item.owner && state.currentRole === "admin"
        ? `<div class="history-meta">用户：${escapeHtml(item.owner)}</div>`
        : "";
      return `
        <button class="history-item${active}" type="button" data-history-id="${escapeHtml(item.job_id || "")}">
          <div class="history-top">
            <strong>${escapeHtml(formatHistoryTime(item.finished_at || item.created_at))}</strong>
            <span>${escapeHtml(String(item.recommendation_count || 0))} 篇</span>
          </div>
          ${owner}
          ${sources ? `<div class="history-meta">${escapeHtml(sources)}</div>` : ""}
          ${keywords ? `<div class="history-note">${escapeHtml(keywords)}</div>` : ""}
        </button>
      `;
    })
    .join("");
}

function renderAdminUserList(items = []) {
  if (!els.adminUserList) {
    return;
  }
  if (!Array.isArray(items) || !items.length) {
    els.adminUserList.innerHTML = '<div class="empty">暂无用户</div>';
    return;
  }
  els.adminUserList.innerHTML = items.map((item) => {
    const configured = item.configured || {};
    const chips = [
      configured.zotero_id ? "Zotero ID" : "",
      configured.zotero_key ? "Zotero Key" : "",
      configured.springer_api_key ? "Springer" : "",
      configured.elsevier_api_key ? "Elsevier" : "",
      configured.ieee_api_key ? "IEEE" : "",
    ].filter(Boolean);
    return `
      <div class="history-item user-item">
        <div class="history-top">
          <strong>${escapeHtml(item.username || "")}</strong>
          <span>${escapeHtml(item.role || "user")}</span>
        </div>
        <div class="history-meta">状态：${escapeHtml(item.status || "active")}</div>
        <div class="terminal-meta-list">
          ${(chips.length ? chips : ["未绑定"]).map((chip) => `<span class="terminal-chip">${escapeHtml(chip)}</span>`).join("")}
        </div>
      </div>
    `;
  }).join("");
}

function applyBindings(bindings) {
  state.bindings = bindings || null;
  const configured = bindings?.configured || {};
  if (els.bindingZoteroId) {
    els.bindingZoteroId.value = bindings?.zotero_id || "";
  }
  if (els.bindingZoteroKey) {
    els.bindingZoteroKey.value = "";
    els.bindingZoteroKey.placeholder = configured.zotero_key ? "已绑定，留空表示不修改" : "请输入 Zotero Key";
  }
  if (els.bindingSpringerKey) {
    els.bindingSpringerKey.value = "";
    els.bindingSpringerKey.placeholder = configured.springer_api_key ? "已绑定，留空表示不修改" : "未绑定则可回退全局配置";
  }
  if (els.bindingElsevierKey) {
    els.bindingElsevierKey.value = "";
    els.bindingElsevierKey.placeholder = configured.elsevier_api_key ? "已绑定，留空表示不修改" : "未绑定则可回退全局配置";
  }
  if (els.bindingIEEEKey) {
    els.bindingIEEEKey.value = "";
    els.bindingIEEEKey.placeholder = configured.ieee_api_key ? "已绑定，留空表示不修改" : "未绑定则可回退全局配置";
  }
  if (els.accountMeta) {
    const username = state.currentUser || "未登录";
    const role = state.currentRole === "admin" ? "管理员" : "用户";
    els.accountMeta.textContent = `${username} · ${role}`;
  }
  if (els.bindingsStatus) {
    const chips = [];
    if (configured.zotero_key) {
      chips.push("已绑定 Zotero Key");
    }
    if (configured.springer_api_key) {
      chips.push("已绑定 Springer Key");
    }
    if (configured.elsevier_api_key) {
      chips.push("已绑定 Elsevier Key");
    }
    if (configured.ieee_api_key) {
      chips.push("已绑定 IEEE Key");
    }
    els.bindingsStatus.textContent = chips.length
      ? chips.join(" / ")
      : "Zotero 不会回退到全局配置；Springer / Elsevier / IEEE 在未绑定时可回退到全局配置。";
  }
}

function renderPaperCard(paper) {
  const authors = formatAuthors(paper.authors);
  const journal = paper.journal || "Unknown";
  const publishedDate = formatPublishedDate(paper.published_date || paper.year);
  const paperLink = resolvePaperLink(paper);
  const title = escapeHtml(paper.title || "Untitled");
  const titleMarkup = paperLink
    ? `<a class="result-title-link" href="${escapeHtml(paperLink)}" target="_blank" rel="noreferrer">${title}</a>`
    : title;

  return `
    <article class="result-item">
      <div class="result-top">
        <h3 class="result-title">${titleMarkup}</h3>
        <span class="result-score">${Number(paper.score || 0).toFixed(1)}</span>
      </div>
      <div class="result-meta">
        <span class="tag">${escapeHtml(paper.source || "Unknown")}</span>
        ${publishedDate ? `<span class="tag">${escapeHtml(publishedDate)}</span>` : ""}
      </div>
      <div class="result-detail">${escapeHtml(authors)}</div>
      <div class="result-detail result-journal-line">
        <span>${escapeHtml(journal)}</span>
        ${renderJournalRank(paper.journal_rank, paper.source)}
      </div>
      ${paper.summary ? `<p>${escapeHtml(paper.summary)}</p>` : ""}
    </article>
  `;
}

function formatSourceCounts(sourceCounts) {
  if (!sourceCounts || typeof sourceCounts !== "object") {
    return "";
  }
  return Object.entries(sourceCounts)
    .filter(([, count]) => Number(count) > 0)
    .map(([source, count]) => `${source} ${count}`)
    .join(" / ");
}

function buildPayload() {
  return {
    keyword_model: els.keywordModel.value.trim(),
    keyword_base_url: els.keywordBaseUrl.value.trim(),
    include_arxiv: els.toggleArxiv.checked,
    arxiv_query: els.arxivQuery.value.trim(),
    zotero_ignore: els.ignorePaths.value,
    top_k: Number(els.recommendCount.value),
    sample_ratio: Number(els.sampleRatio.value),
    selected_keyword_count: Number(els.selectedKeywordCount.value),
    candidate_pool_per_source: Number(els.candidatePool.value),
    extra_keyword_count: Number(els.extraKeywordCount.value),
    extra_candidate_pool_per_source: Number(els.extraCandidatePool.value),
    include_springer: els.toggleSpringer.checked,
    include_elsevier: els.toggleElsevier.checked,
    include_ieee: els.toggleIEEE.checked,
    write_to_zotero: els.writeToZotero.checked,
    extra_keyword_input: els.extraKeywordInput.value.trim(),
    direct_sentence_search: false,
  };
}

function summarizePayload(payload) {
  const enabledSources = [];
  if (payload.include_arxiv) {
    enabledSources.push("arXiv");
  }
  if (payload.include_springer) {
    enabledSources.push("Springer");
  }
  if (payload.include_elsevier) {
    enabledSources.push("Elsevier");
  }
  if (payload.include_ieee) {
    enabledSources.push("IEEE");
  }
  return {
    request: {
      enabled_sources: enabledSources,
      top_k: payload.top_k,
      write_to_zotero: payload.write_to_zotero,
      arxiv_query: payload.include_arxiv ? payload.arxiv_query : "",
      keyword_model: payload.keyword_model || "",
      keyword_base_url: payload.keyword_base_url || "",
      extra_keyword_input_present: Boolean(payload.extra_keyword_input),
      direct_sentence_search: Boolean(payload.direct_sentence_search),
      extra_keyword_count: payload.extra_keyword_count,
      extra_candidate_pool_per_source: payload.extra_candidate_pool_per_source,
    },
  };
}

function syncSourceFields() {
  const keywordMode = els.toggleSpringer.checked || els.toggleElsevier.checked || els.toggleIEEE.checked;
  els.arxivQuery.disabled = !els.toggleArxiv.checked;
  els.keywordModel.disabled = !keywordMode;
  els.keywordBaseUrl.disabled = !keywordMode;
  els.selectedKeywordCount.disabled = !keywordMode;
  els.candidatePool.disabled = !keywordMode;
  els.extraKeywordCount.disabled = !keywordMode;
  els.extraCandidatePool.disabled = !keywordMode;
  els.extraKeywordInput.disabled = !keywordMode;
  els.sourceCardArxiv.dataset.active = String(els.toggleArxiv.checked);
  els.sourceCardSpringer.dataset.active = String(els.toggleSpringer.checked);
  els.sourceCardElsevier.dataset.active = String(els.toggleElsevier.checked);
  els.sourceCardIEEE.dataset.active = String(els.toggleIEEE.checked);
}

function applyConfig(config) {
  const defaults = config?.defaults || {};

  if (defaults.arxiv_query) {
    els.arxivQuery.value = defaults.arxiv_query;
  }
  if (defaults.zotero_ignore) {
    els.ignorePaths.value = defaults.zotero_ignore;
  }
  if (defaults.keyword_base_url) {
    els.keywordBaseUrl.value = defaults.keyword_base_url;
  }
  if (defaults.keyword_model) {
    const matchedOption = Array.from(els.keywordModel.options).find(
      (option) => option.value === defaults.keyword_model,
    );
    if (!matchedOption) {
      const option = document.createElement("option");
      option.value = defaults.keyword_model;
      option.textContent = defaults.keyword_model;
      els.keywordModel.appendChild(option);
    }
    els.keywordModel.value = defaults.keyword_model;
  }
  if (Number.isFinite(Number(defaults.top_k))) {
    els.recommendCount.value = String(defaults.top_k);
  }
  if (Number.isFinite(Number(defaults.sample_ratio))) {
    els.sampleRatio.value = String(defaults.sample_ratio);
  }
  if (Number.isFinite(Number(defaults.selected_keyword_count))) {
    els.selectedKeywordCount.value = String(defaults.selected_keyword_count);
  }
  if (Number.isFinite(Number(defaults.candidate_pool_per_source))) {
    els.candidatePool.value = String(defaults.candidate_pool_per_source);
  }
  if (Number.isFinite(Number(defaults.extra_keyword_count))) {
    els.extraKeywordCount.value = String(defaults.extra_keyword_count);
  }
  if (Number.isFinite(Number(defaults.extra_candidate_pool_per_source))) {
    els.extraCandidatePool.value = String(defaults.extra_candidate_pool_per_source);
  }
  if (typeof defaults.include_arxiv === "boolean") {
    els.toggleArxiv.checked = defaults.include_arxiv;
  }
  if (typeof defaults.include_springer === "boolean") {
    els.toggleSpringer.checked = defaults.include_springer;
  }
  if (typeof defaults.include_elsevier === "boolean") {
    els.toggleElsevier.checked = defaults.include_elsevier;
  }
  if (typeof defaults.include_ieee === "boolean") {
    els.toggleIEEE.checked = defaults.include_ieee;
  }
  if (typeof defaults.write_to_zotero === "boolean") {
    els.writeToZotero.checked = defaults.write_to_zotero;
  }

  syncSourceFields();
}

async function loadConfig() {
  try {
    const response = await apiFetch("/api/config", {
      headers: { "Cache-Control": "no-store" },
    });
    applyConfig(await response.json());
  } catch (_error) {
    // Keep the UI usable even if config loading fails.
  }
}

async function loadBindings() {
  try {
    const response = await apiFetch("/api/users/me/bindings", {
      headers: { "Cache-Control": "no-store" },
    });
    const data = await response.json();
    applyBindings(data);
  } catch (_error) {
    // Keep bindings optional until explicitly needed.
  }
}

async function saveBindings() {
  const payload = {
    zotero_id: els.bindingZoteroId.value.trim(),
    zotero_key: els.bindingZoteroKey.value,
    springer_api_key: els.bindingSpringerKey.value,
    elsevier_api_key: els.bindingElsevierKey.value,
    ieee_api_key: els.bindingIEEEKey.value,
  };
  const response = await apiFetch("/api/users/me/bindings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  applyBindings(data);
  await loadConfig();
  return data;
}

async function loadUsers() {
  if (state.currentRole !== "admin") {
    renderAdminUserList([]);
    return;
  }
  const response = await apiFetch("/api/admin/users", {
    headers: { "Cache-Control": "no-store" },
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  renderAdminUserList(data.items || []);
}

async function createUser() {
  const response = await apiFetch("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: els.adminNewUsername.value.trim(),
      password: els.adminNewPassword.value,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  await loadUsers();
  return data;
}

async function loadHistory() {
  try {
    const response = await apiFetch("/api/history", {
      headers: { "Cache-Control": "no-store" },
    });
    const data = await response.json();
    renderHistoryList(data.items || []);
  } catch (_error) {
    // Keep history optional.
  }
}

async function loadHistoryEntry(jobId) {
  const response = await apiFetch(`/api/history/${encodeURIComponent(jobId)}`, {
    headers: { "Cache-Control": "no-store" },
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }

  stopPolling();
  state.jobId = null;
  state.lines = [];
  state.currentJob = null;
  renderTerminal();
  renderResults([]);
  pushLine(`LOAD HISTORY ${jobId.slice(0, 8)}`, "", "history", {
    source_counts: data.result?.source_counts || {},
    request: {
      enabled_sources: data.result ? Object.keys(data.result.source_counts || {}) : [],
      top_k: Array.isArray(data.result?.recommendations) ? data.result.recommendations.length : 0,
    },
  });
  applyJob(data);
}

function setStatus(status, message) {
  els.statusBadge.textContent = status;
  els.statusMessage.textContent = message;
}

function pushLine(text, type = "", step = "", details = null) {
  state.lines.push({
    time: nowTime(),
    text,
    type,
    step,
    details,
  });
  renderTerminal();
}

function summarizeLineDetails(details) {
  if (!details || typeof details !== "object") {
    return [];
  }
  const chips = [];
  if (Array.isArray(details.selected_keywords) && details.selected_keywords.length) {
    chips.push(`已选 ${details.selected_keywords.join(" / ")}`);
  }
  if (Array.isArray(details.keyword_snapshot) && details.keyword_snapshot.length) {
    chips.push(`快照 ${details.keyword_snapshot.join(" / ")}`);
  }
  if (Array.isArray(details.extra_keyword_snapshot) && details.extra_keyword_snapshot.length) {
    chips.push(`补充 ${details.extra_keyword_snapshot.join(" / ")}`);
  }
  if (Array.isArray(details.extra_selected_keywords) && details.extra_selected_keywords.length) {
    chips.push(`补充已选 ${details.extra_selected_keywords.join(" / ")}`);
  }
  if (details.active_source) {
    chips.push(details.active_source);
  }
  if (details.active_keyword) {
    chips.push(`keyword ${details.active_keyword}`);
  }
  if (details.keyword_group) {
    chips.push(details.keyword_group === "extra" ? "额外检索" : "常规检索");
  }
  if (details.arxiv_query) {
    chips.push(`query ${details.arxiv_query}`);
  }
  if (Number.isFinite(Number(details.candidate_count))) {
    chips.push(`候选 ${details.candidate_count}`);
  }
  if (Number.isFinite(Number(details.journal_count))) {
    chips.push(`期刊 ${details.journal_count}`);
  }
  if (Number.isFinite(Number(details.rated_journal_count))) {
    chips.push(`命中 ${details.rated_journal_count}`);
  }
  if (Number.isFinite(Number(details.unrated_journal_count))) {
    chips.push(`未命中 ${details.unrated_journal_count}`);
  }
  if (Number.isFinite(Number(details.corpus_count))) {
    chips.push(`语料 ${details.corpus_count}`);
  }
  if (Number.isFinite(Number(details.sampled_title_count))) {
    chips.push(`采样 ${details.sampled_title_count}`);
  }
  if (details.embedding_device) {
    chips.push(`embed ${details.embedding_device}`);
  }
  const sourceCounts = formatSourceCounts(details.source_counts);
  if (sourceCounts) {
    chips.push(sourceCounts);
  }
  if (details.request?.enabled_sources?.length) {
    chips.push(`sources ${details.request.enabled_sources.join(", ")}`);
  }
  if (details.request?.keyword_base_url) {
    chips.push(`llm ${details.request.keyword_base_url}`);
  }
  if (details.request?.keyword_model) {
    chips.push(`model ${details.request.keyword_model}`);
  }
  if (details.request?.extra_keyword_input_present) {
    chips.push("含补充词");
  }
  if (details.request?.direct_sentence_search) {
    chips.push("一句话直查");
  }
  if (Number.isFinite(Number(details.request?.top_k))) {
    chips.push(`top ${details.request.top_k}`);
  }
  if (Number.isFinite(Number(details.request?.extra_keyword_count))) {
    chips.push(`extra kw ${details.request.extra_keyword_count}`);
  }
  if (Number.isFinite(Number(details.request?.extra_candidate_pool_per_source))) {
    chips.push(`extra pool ${details.request.extra_candidate_pool_per_source}`);
  }
  return chips.slice(0, 6);
}

function formatLineDetails(details) {
  if (!details || typeof details !== "object") {
    return "";
  }
  if (
    Array.isArray(details.keyword_snapshot) ||
    Array.isArray(details.selected_keywords) ||
    Array.isArray(details.extra_selected_keywords)
  ) {
    const parts = [];
    if (Array.isArray(details.keyword_snapshot) && details.keyword_snapshot.length) {
      parts.push(`snapshot=${details.keyword_snapshot.join(", ")}`);
    }
    if (Array.isArray(details.extra_keyword_snapshot) && details.extra_keyword_snapshot.length) {
      parts.push(`extra=${details.extra_keyword_snapshot.join(", ")}`);
    }
    if (Array.isArray(details.zotero_selected_keywords) && details.zotero_selected_keywords.length) {
      parts.push(`zotero=${details.zotero_selected_keywords.join(", ")}`);
    }
    if (Array.isArray(details.extra_selected_keywords) && details.extra_selected_keywords.length) {
      parts.push(`extra_selected=${details.extra_selected_keywords.join(", ")}`);
    }
    if (Array.isArray(details.extra_retrieval_keywords) && details.extra_retrieval_keywords.length) {
      parts.push(`extra_retrieval=${details.extra_retrieval_keywords.join(", ")}`);
    }
    if (Array.isArray(details.retrieval_keywords) && details.retrieval_keywords.length) {
      parts.push(`retrieval=${details.retrieval_keywords.join(", ")}`);
    }
    if (Array.isArray(details.selected_keywords) && details.selected_keywords.length) {
      parts.push(`selected=${details.selected_keywords.join(", ")}`);
    }
    return parts.join(" | ");
  }
  if (details.request && typeof details.request === "object") {
    const request = details.request;
    const parts = [];
    if (request.enabled_sources?.length) {
      parts.push(`sources=${request.enabled_sources.join(",")}`);
    }
    if (request.arxiv_query) {
      parts.push(`query=${request.arxiv_query}`);
    }
    if (request.keyword_base_url) {
      parts.push(`llm=${request.keyword_base_url}`);
    }
    if (request.keyword_model) {
      parts.push(`model=${request.keyword_model}`);
    }
    if (request.extra_keyword_input_present) {
      parts.push("extra_input=true");
    }
    if (request.direct_sentence_search) {
      parts.push("direct_sentence_search=true");
    }
    if (Number.isFinite(Number(request.extra_keyword_count))) {
      parts.push(`extra_keyword_count=${request.extra_keyword_count}`);
    }
    if (Number.isFinite(Number(request.extra_candidate_pool_per_source))) {
      parts.push(`extra_pool=${request.extra_candidate_pool_per_source}`);
    }
    if (request.embedding_device) {
      parts.push(`device=${request.embedding_device}`);
    }
    if (Number.isFinite(Number(request.top_k))) {
      parts.push(`top_k=${request.top_k}`);
    }
    if (typeof request.write_to_zotero === "boolean") {
      parts.push(`write_to_zotero=${request.write_to_zotero}`);
    }
    return parts.join(" | ");
  }

  const entries = Object.entries(details)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .filter(([key]) => key !== "request")
    .map(([key, value]) => {
      if (typeof value === "object") {
        const compact = formatSourceCounts(value);
        if (compact) {
          return `${key}=${compact}`;
        }
        return "";
      }
      return `${key}=${String(value)}`;
    })
    .filter(Boolean);
  return entries.slice(0, 4).join(" | ");
}

function updateTerminalMeta(job) {
  if (!job) {
    els.terminalMeta.textContent = "等待任务开始";
    return;
  }
  const parts = [];
  if (job.job_id) {
    parts.push(`job ${job.job_id.slice(0, 8)}`);
  }
  if (job.owner && state.currentRole === "admin") {
    parts.push(`owner ${job.owner}`);
  }
  if (job.step && job.step !== "queued") {
    parts.push(job.step);
  }
  if (job.details?.active_source) {
    parts.push(job.details.active_source);
  }
  if (Array.isArray(job.events)) {
    parts.push(`${job.events.length} events`);
  }
  els.terminalMeta.textContent = parts.join(" · ") || "等待任务开始";
}

function renderTerminal() {
  if (!state.lines.length) {
    els.terminalOutput.innerHTML = '<div class="empty">等待任务开始</div>';
    return;
  }

  els.terminalOutput.innerHTML = state.lines
    .slice(-120)
    .map((line) => {
      const lineClass = line.type ? `terminal-entry ${line.type}` : "terminal-entry";
      const detailsText = formatLineDetails(line.details);
      const meta = summarizeLineDetails(line.details);
      return `
        <div class="${lineClass}">
          <div class="terminal-line">
            <span class="terminal-time">${escapeHtml(line.time)}</span>
            <div class="terminal-content">
              <div class="terminal-main">
                ${line.step ? `<span class="terminal-step">${escapeHtml(line.step)}</span>` : ""}
                <span class="terminal-text">${escapeHtml(line.text)}</span>
              </div>
              ${meta.length ? `<div class="terminal-meta-list">${meta.map((item) => `<span class="terminal-chip">${escapeHtml(item)}</span>`).join("")}</div>` : ""}
              ${detailsText ? `<div class="terminal-detail">${escapeHtml(detailsText)}</div>` : ""}
            </div>
          </div>
        </div>
      `;
    })
    .join("");
  els.terminalOutput.scrollTop = els.terminalOutput.scrollHeight;
}

function renderResults(papers = []) {
  state.recommendations = Array.isArray(papers) ? papers : [];
  const filter = els.resultSourceFilter.value || "all";
  const visiblePapers = filter === "all"
    ? state.recommendations
    : state.recommendations.filter((paper) => (paper.source || "") === filter);

  els.resultsCount.textContent = `${visiblePapers.length} / ${state.recommendations.length}`;
  if (!visiblePapers.length) {
    els.resultsGrid.innerHTML = '<div class="empty">暂无结果</div>';
    return;
  }

  const grouped = new Map();
  SOURCE_ORDER.forEach((source) => grouped.set(source, []));
  visiblePapers.forEach((paper) => {
    const source = paper.source || "Other";
    if (!grouped.has(source)) {
      grouped.set(source, []);
    }
    grouped.get(source).push(paper);
  });

  els.resultsGrid.innerHTML = Array.from(grouped.entries())
    .filter(([, items]) => items.length)
    .map(
      ([source, items]) => `
        <section class="result-group">
          <div class="result-group-head">
            <h3>${escapeHtml(source)}</h3>
            <span>${items.length}</span>
          </div>
          <div class="results-sublist">
            ${items.map((paper) => renderPaperCard(paper)).join("")}
          </div>
        </section>
      `,
    )
    .join("");
}

function applyJob(job) {
  state.pollFailures = 0;
  state.currentJob = job;
  renderHistoryList(state.history);
  updateTerminalMeta(job);
  setStatus(job.status, job.error || job.message || "running");
  const seen = new Set(state.lines.map((line) => `${line.time}|${line.step}|${line.text}`));
  (job.events || []).forEach((event) => {
    const text = event.message || event.step;
    const key = `${nowTime(event.time)}|${event.step}|${text}`;
    if (seen.has(key)) {
      return;
    }
    let type = "";
    if (event.step === "failed" || /失败|错误|warning/i.test(text)) {
      type = "error";
    }
    if (event.step === "cancelled") {
      type = "error";
    }
    if (event.step === "completed") {
      type = "success";
    }
    state.lines.push({
      time: nowTime(event.time),
      text,
      type,
      step: event.step || "",
      details: event.details || null,
    });
  });

  if (job.error) {
    const errorText = `ERROR ${job.error}`;
    if (!state.lines.some((line) => line.text === errorText)) {
      state.lines.push({
        time: nowTime(),
        text: errorText,
        type: "error",
        step: "failed",
        details: job.details || null,
      });
    }
  }

  if (job.result?.warnings?.length) {
    job.result.warnings.forEach((warning) => {
      const text = `WARN ${warning}`;
      if (!state.lines.some((line) => line.text === text)) {
        state.lines.push({
          time: nowTime(),
          text,
          type: "error",
          step: "warning",
          details: null,
        });
      }
    });
  }

  renderTerminal();
  renderResults(job.result?.recommendations || []);

  if (job.status === "queued" || job.status === "running") {
    els.startButton.disabled = false;
    els.startButton.textContent = "停止";
    return;
  }

  if (job.status === "stopping") {
    els.startButton.disabled = true;
    els.startButton.textContent = "停止中...";
    return;
  }

  if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
    stopPolling();
    state.jobId = null;
    els.startButton.disabled = false;
    if (job.status === "completed") {
      els.startButton.textContent = "重新开始";
    } else if (job.status === "failed") {
      els.startButton.textContent = "重试";
    } else {
      els.startButton.textContent = "开始";
    }
    if (job.status === "completed") {
      void loadHistory();
    }
  }
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function fetchJob(jobId, timeoutMs = POLL_REQUEST_TIMEOUT_MS) {
  const response = await apiFetch(`/api/workflows/${encodeURIComponent(jobId)}`, {
    headers: { "Cache-Control": "no-store" },
  }, timeoutMs);
  return response.json();
}

function setAuthenticated(value) {
  state.authenticated = Boolean(value);
  document.body.classList.toggle("is-authenticated", state.authenticated);
  if (!state.authenticated) {
    state.currentUser = null;
    state.currentRole = null;
    state.bindings = null;
    if (els.adminPanel) {
      els.adminPanel.hidden = true;
    }
    if (els.adminUserList) {
      renderAdminUserList([]);
    }
  }
}

function buildGuideHtmlFromMarkdown(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraphLines = [];
  let listType = "";
  let listItems = [];
  let inCodeBlock = false;
  let codeFence = "";
  let codeLines = [];

  const parseInline = (text) => {
    let html = escapeHtml(text || "");
    const placeholders = [];
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_match, label, url) => {
      const token = `__MD_LINK_${placeholders.length}__`;
      placeholders.push(`<a href="${url}" target="_blank" rel="noreferrer">${label}</a>`);
      return token;
    });
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    html = html.replace(/(https?:\/\/[^\s<"')]+)/g, (url) => {
      return `<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`;
    });
    placeholders.forEach((anchor, index) => {
      html = html.replace(`__MD_LINK_${index}__`, anchor);
    });
    return html;
  };

  const flushParagraph = () => {
    if (!paragraphLines.length) {
      return;
    }
    blocks.push(`<p>${paragraphLines.map(parseInline).join("<br>")}</p>`);
    paragraphLines = [];
  };

  const flushList = () => {
    if (!listItems.length || !listType) {
      return;
    }
    const tag = listType === "ol" ? "ol" : "ul";
    blocks.push(`<${tag}>${listItems.map((item) => `<li>${parseInline(item)}</li>`).join("")}</${tag}>`);
    listItems = [];
    listType = "";
  };

  const flushCodeBlock = () => {
    if (!codeLines.length) {
      blocks.push("<pre><code></code></pre>");
    } else {
      blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    }
    codeLines = [];
    inCodeBlock = false;
    codeFence = "";
  };

  lines.forEach((line) => {
    const trimmed = line.trim();

    if (inCodeBlock) {
      if (trimmed.startsWith(codeFence)) {
        flushCodeBlock();
      } else {
        codeLines.push(line);
      }
      return;
    }

    if (trimmed.startsWith("```") || trimmed.startsWith("~~~")) {
      flushParagraph();
      flushList();
      inCodeBlock = true;
      codeFence = trimmed.startsWith("~~~") ? "~~~" : "```";
      return;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }

    if (/^#{1,6}\s+/.test(trimmed)) {
      flushParagraph();
      flushList();
      const headingLevel = Math.min(6, (trimmed.match(/^#+/) || [""])[0].length + 1);
      const headingText = trimmed.replace(/^#{1,6}\s+/, "");
      blocks.push(`<h${headingLevel}>${parseInline(headingText)}</h${headingLevel}>`);
      return;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph();
      flushList();
      blocks.push("<hr>");
      return;
    }

    const ulMatch = trimmed.match(/^[-*+]\s+(.*)$/);
    if (ulMatch) {
      flushParagraph();
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      listItems.push(ulMatch[1]);
      return;
    }

    const olMatch = trimmed.match(/^\d+\.\s+(.*)$/);
    if (olMatch) {
      flushParagraph();
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      listItems.push(olMatch[1]);
      return;
    }

    if (trimmed.startsWith(">")) {
      flushParagraph();
      flushList();
      blocks.push(`<blockquote>${parseInline(trimmed.replace(/^>\s?/, ""))}</blockquote>`);
      return;
    }

    flushList();
    paragraphLines.push(trimmed);
  });

  if (inCodeBlock) {
    flushCodeBlock();
  }
  flushParagraph();
  flushList();
  return blocks.join("");
}

function showGuideModal() {
  if (!els.guideModal) {
    return;
  }
  els.guideModal.classList.add("is-open");
  els.guideModal.setAttribute("aria-hidden", "false");
}

function hideGuideModal() {
  if (!els.guideModal) {
    return;
  }
  els.guideModal.classList.remove("is-open");
  els.guideModal.setAttribute("aria-hidden", "true");
}

function openSentenceModeModal() {
  if (!els.sentenceModeModal) {
    return;
  }
  els.sentenceModeModal.classList.add("is-open");
  els.sentenceModeModal.setAttribute("aria-hidden", "false");
}

function closeSentenceModeModal() {
  if (!els.sentenceModeModal) {
    return;
  }
  els.sentenceModeModal.classList.remove("is-open");
  els.sentenceModeModal.setAttribute("aria-hidden", "true");
}

function resolveSentenceMode(choice) {
  const resolver = state.sentenceModeResolver;
  state.sentenceModeResolver = null;
  closeSentenceModeModal();
  if (typeof resolver === "function") {
    resolver(choice);
  }
}

function promptSentenceMode() {
  if (!els.sentenceModeModal) {
    return Promise.resolve(false);
  }
  openSentenceModeModal();
  return new Promise((resolve) => {
    state.sentenceModeResolver = resolve;
  });
}

function getGuideSeenStorageKey() {
  return `${GUIDE_SEEN_STORAGE_PREFIX}${state.currentUser || "anonymous"}`;
}

function hasSeenGuideInSession() {
  try {
    return window.sessionStorage.getItem(getGuideSeenStorageKey()) === "1";
  } catch (_error) {
    return false;
  }
}

function markGuideSeenInSession() {
  try {
    window.sessionStorage.setItem(getGuideSeenStorageKey(), "1");
  } catch (_error) {
    // Ignore session storage failures.
  }
}

function clearGuideSeenInSession() {
  try {
    window.sessionStorage.removeItem(getGuideSeenStorageKey());
  } catch (_error) {
    // Ignore session storage failures.
  }
}

async function loadAndShowGuide() {
  if (hasSeenGuideInSession()) {
    return;
  }
  if (state.guideLoaded) {
    markGuideSeenInSession();
    showGuideModal();
    return;
  }
  try {
    const response = await apiFetch("/api/user-guide", {
      headers: { "Cache-Control": "no-store" },
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    if (els.guideContent) {
      els.guideContent.innerHTML = buildGuideHtmlFromMarkdown(data.markdown || "");
    }
    state.guideLoaded = true;
    markGuideSeenInSession();
    showGuideModal();
  } catch (_error) {
    // 说明书加载失败时不阻塞主流程。
  }
}

function showLoginError(message) {
  if (!els.loginError) {
    return;
  }
  els.loginError.textContent = message || "";
}

async function apiFetch(url, options = {}, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } catch (error) {
    if (error && error.name === "AbortError") {
      throw new Error("请求超时，请检查公网代理连接是否稳定。");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
  if (response.status === 401) {
    setAuthenticated(false);
    showLoginError("请先登录。");
    throw new Error("请先登录");
  }
  return response;
}

async function checkAuth() {
  try {
    const response = await fetch("/api/auth/me", {
      headers: { "Cache-Control": "no-store" },
    });
    if (!response.ok) {
      setAuthenticated(false);
      return false;
    }
    const data = await response.json();
    state.currentUser = data.username || null;
    state.currentRole = data.role || null;
    setAuthenticated(Boolean(data.authenticated));
    if (els.adminPanel) {
      els.adminPanel.hidden = state.currentRole !== "admin";
    }
    return state.authenticated;
  } catch (_error) {
    setAuthenticated(false);
    return false;
  }
}

async function login(username, password) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  state.currentUser = data.username || username;
  state.currentRole = data.role || "user";
  setAuthenticated(true);
  if (els.adminPanel) {
    els.adminPanel.hidden = state.currentRole !== "admin";
  }
  await loadAndShowGuide();
}

async function logout() {
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
    });
  } finally {
    clearGuideSeenInSession();
    stopPolling();
    setAuthenticated(false);
    showLoginError("已退出，请重新登录。");
    renderHistoryList([]);
    applyBindings(null);
  }
}

async function stopWorkflow() {
  if (!state.jobId) {
    return;
  }
  if (state.currentJob?.status === "stopping") {
    return;
  }

  const currentJobId = state.jobId;
  els.startButton.disabled = true;
  els.startButton.textContent = "停止中...";
  setStatus("stopping", "正在停止工作流...");
  pushLine(`POST /api/workflows/${currentJobId.slice(0, 8)}/stop`, "", "request");

  try {
    let response = await apiFetch(`/api/workflows/${encodeURIComponent(currentJobId)}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    let data = await response.json();
    if (response.status === 404) {
      response = await apiFetch("/api/workflows/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: currentJobId }),
      });
      data = await response.json();
    }
    if (!response.ok && response.status !== 409) {
      throw new Error(data.error || data.message || `HTTP ${response.status}`);
    }

    if (data.message) {
      pushLine(data.message, "", "stopping");
    }
    if (data.job) {
      applyJob(data.job);
      return;
    }
    if (state.jobId) {
      applyJob(await fetchJob(state.jobId, 10000));
    }
  } catch (error) {
    els.startButton.disabled = false;
    els.startButton.textContent = "停止";
    const errorText = error.message === "Not found."
      ? "停止接口未命中，请重启后端服务后重试。"
      : "停止请求失败，请重试。";
    setStatus("running", errorText);
    pushLine(`ERROR 停止失败：${error.message}`, "error", "stopping");
  }
}

async function startWorkflow() {
  if (!state.authenticated) {
    showLoginError("请先登录。");
    return;
  }
  if (state.jobId && ["queued", "running", "stopping"].includes(state.currentJob?.status || "running")) {
    await stopWorkflow();
    return;
  }
  state.lines = [];
  state.pollFailures = 0;
  state.jobId = null;
  state.currentJob = null;
  renderHistoryList(state.history);
  renderTerminal();
  renderResults([]);
  els.startButton.disabled = true;
  els.startButton.textContent = "提交中...";
  setStatus("queued", "提交中");
  updateTerminalMeta(null);
  const payload = buildPayload();
  if (payload.extra_keyword_input) {
    payload.direct_sentence_search = await promptSentenceMode();
  }
  pushLine("POST /api/workflows/today", "", "request", summarizePayload(payload));

  try {
    const response = await apiFetch("/api/workflows/today", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }

    state.jobId = data.job_id;
    setStatus("queued", data.message || "queued");
    pushLine(`JOB ${data.job_id.slice(0, 8)} accepted`, "", "queued");
    els.startButton.disabled = false;
    els.startButton.textContent = "停止";

    stopPolling();
    state.pollTimer = window.setInterval(async () => {
      try {
        applyJob(await fetchJob(state.jobId, POLL_REQUEST_TIMEOUT_MS));
      } catch (error) {
        state.pollFailures += 1;
        if (state.pollFailures >= MAX_POLL_FAILURES) {
          stopPolling();
          state.jobId = null;
          els.startButton.disabled = false;
          els.startButton.textContent = "重试";
          setStatus("failed", `轮询失败：${error.message}`);
          pushLine(`ERROR 轮询失败：${error.message}`, "error", "failed");
          return;
        }
        setStatus("running", `连接波动，自动重试 ${state.pollFailures}/${MAX_POLL_FAILURES}`);
        pushLine(
          `WARN 连接波动，正在重试 (${state.pollFailures}/${MAX_POLL_FAILURES})`,
          "error",
          "polling",
        );
      }
    }, 1200);

    applyJob(await fetchJob(state.jobId, 10000));
  } catch (error) {
    stopPolling();
    state.jobId = null;
    els.startButton.disabled = false;
    els.startButton.textContent = "开始";
    setStatus("failed", error.message);
    pushLine(`ERROR ${error.message}`, "error", "failed");
  }
}

els.startButton.addEventListener("click", startWorkflow);
els.toggleArxiv.addEventListener("change", syncSourceFields);
els.toggleSpringer.addEventListener("change", syncSourceFields);
els.toggleElsevier.addEventListener("change", syncSourceFields);
els.toggleIEEE.addEventListener("change", syncSourceFields);
els.resultSourceFilter.addEventListener("change", () => renderResults(state.recommendations));
if (els.themeSwitch) {
  els.themeSwitch.addEventListener("change", () => {
    const theme = els.themeSwitch.checked ? "light" : "dark";
    window.localStorage.setItem("workflow-theme", theme);
    applyTheme(theme);
  });
}
els.historyList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-history-id]");
  if (!(button instanceof HTMLElement)) {
    return;
  }
  try {
    await loadHistoryEntry(button.dataset.historyId || "");
  } catch (error) {
    pushLine(`ERROR ${error.message}`, "error", "history");
  }
});

if (els.saveBindingsButton) {
  els.saveBindingsButton.addEventListener("click", async () => {
    els.saveBindingsButton.disabled = true;
    try {
      await saveBindings();
      if (els.bindingsStatus) {
        els.bindingsStatus.textContent = "绑定已保存。";
      }
    } catch (error) {
      if (els.bindingsStatus) {
        els.bindingsStatus.textContent = error.message || "绑定保存失败。";
      }
    } finally {
      els.saveBindingsButton.disabled = false;
    }
  });
}

if (els.adminCreateUserForm) {
  els.adminCreateUserForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!els.adminCreateUserButton) {
      return;
    }
    els.adminCreateUserButton.disabled = true;
    try {
      const data = await createUser();
      els.adminNewUsername.value = "";
      els.adminNewPassword.value = "";
      if (els.adminUserStatus) {
        els.adminUserStatus.textContent = `已创建用户 ${data.username}`;
      }
    } catch (error) {
      if (els.adminUserStatus) {
        els.adminUserStatus.textContent = error.message || "创建用户失败。";
      }
    } finally {
      els.adminCreateUserButton.disabled = false;
    }
  });
}

if (els.loginForm) {
  els.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showLoginError("");
    els.loginSubmit.disabled = true;
    try {
      await login(els.loginUser.value.trim(), els.loginPass.value);
      await loadBindings();
      await loadConfig();
      await loadHistory();
      if (state.currentRole === "admin") {
        await loadUsers();
      }
      els.loginPass.value = "";
    } catch (error) {
      showLoginError(error.message || "登录失败，请重试。");
    } finally {
      els.loginSubmit.disabled = false;
    }
  });
}

if (els.logoutButton) {
  els.logoutButton.addEventListener("click", async () => {
    await logout();
  });
}

if (els.guideCloseButton) {
  els.guideCloseButton.addEventListener("click", hideGuideModal);
}

if (els.guideConfirmButton) {
  els.guideConfirmButton.addEventListener("click", hideGuideModal);
}

if (els.guideModal) {
  els.guideModal.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.guideClose === "true") {
      hideGuideModal();
    }
  });
}

if (els.sentenceModeCloseButton) {
  els.sentenceModeCloseButton.addEventListener("click", () => resolveSentenceMode(false));
}

if (els.sentenceModeDirectButton) {
  els.sentenceModeDirectButton.addEventListener("click", () => resolveSentenceMode(true));
}

if (els.sentenceModeGeneratedButton) {
  els.sentenceModeGeneratedButton.addEventListener("click", () => resolveSentenceMode(false));
}

if (els.sentenceModeModal) {
  els.sentenceModeModal.addEventListener("click", (event) => {
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      target.dataset.sentenceModeClose === "cancel"
    ) {
      resolveSentenceMode(false);
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && els.sentenceModeModal?.classList.contains("is-open")) {
    resolveSentenceMode(false);
  }
});

initTheme();
syncSourceFields();
renderTerminal();
renderResults([]);
void checkAuth().then(async (ok) => {
  if (!ok) {
    showLoginError("请输入账号和密码登录。");
    return;
  }
  await loadBindings();
  await loadConfig();
  await loadHistory();
  if (state.currentRole === "admin") {
    await loadUsers();
  }
  void loadAndShowGuide();
});
