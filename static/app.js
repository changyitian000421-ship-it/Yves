const state = {
  downstreamSectors: {},
  upstreamSectors: {},
  competitorSectors: {},
  socialSectors: {},
  socialPlatforms: {},
  environmentalSectors: {},
  procurementSectors: {},
  regionPresets: {},
  direction: "downstream",
  leads: [],
  filtered: [],
  meta: {},
  activeJobId: "",
  view: "collect",
  savedLeads: [],
  profiles: [],
  profileFiltered: [],
  dashboard: {},
  monitors: [],
  notifications: [],
  currentLead: null,
  selectedLeadIds: new Set(),
  pageItems: [],
  pageSize: 50,
  pages: { database: 1, profiles: 1 },
  leadStoreLoadedAt: 0,
  runningMonitorPolls: new Set(),
  system: {},
  hasEnvAmapKey: false,
  hasEnvBaiduMapAk: false,
  hasEnvTiandituTk: false,
  hasEnvBaiduSearchApiKey: false,
  tursoConfigured: false,
  collectionStrategy: "precision",
};

const DIRECTION_LABELS = {
  downstream: "下游买家",
  upstream: "上游液钙副产企业",
  procurement: "招投标/采购",
  environmental: "含氟废水企业",
  competitor: "竞品/同行情报",
  social: "社媒线索雷达",
};

const DIRECTION_ORDER = ["downstream", "upstream", "procurement", "environmental", "competitor", "social"];

const SALES_STATUS_LABELS = {
  new: "待核实",
  contacted: "已联系",
  qualified: "有需求",
  quoted: "报价中",
  won: "已成交",
  lost: "无效",
};

const SALES_STATUS_ORDER = ["new", "contacted", "qualified", "quoted", "won", "lost"];

const SOCIAL_FEEDBACK_LABELS = {
  valid: "有效",
  irrelevant: "无关",
  duplicate: "重复",
};

const REGION_LABELS = {
  north: "华北",
  northeast: "东北",
  east: "华东",
  central: "华中",
  south: "华南",
  southwest: "西南",
  northwest: "西北",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const desktopWorkspace = window.matchMedia("(min-width: 981px)");
const SIDEBAR_STORAGE_KEY = "lead-cockpit-sidebar-collapsed";
const SEARCH_DRAFT_STORAGE_KEY = "lead-cockpit-search-drafts-v1";
const LEAD_STORE_TTL_MS = 60000;
let filterRenderTimer = 0;

function replayMotion(element, className) {
  if (!element || reducedMotion.matches) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
}

function revealMotionItems(container, limit = 12) {
  if (!container || reducedMotion.matches) return;
  const count = Math.min(container.children.length, limit);
  for (let index = 0; index < count; index += 1) {
    const item = container.children[index];
    item.style.setProperty("--motion-order", index);
    replayMotion(item, "motion-enter");
  }
}

function animateVisibleWorkspace() {
  const selectors = [
    ".metrics",
    ".filters:not([hidden])",
    ".notice:not([hidden])",
    ".quality-summary:not([hidden])",
    ".table-shell:not([hidden])",
    ".alerts-panel:not([hidden])",
    ".profiles-panel:not([hidden])",
    ".system-panel:not([hidden])",
  ];
  selectors.forEach((selector, index) => {
    const element = $(selector);
    if (!element) return;
    element.style.setProperty("--motion-order", index);
    replayMotion(element, "surface-enter");
  });
}

function showDialogSmooth(dialog) {
  if (!dialog) return;
  dialog.classList.remove("is-closing");
  if (!dialog.open) dialog.showModal();
  replayMotion(dialog, "dialog-refresh");
}

function closeDialogSmooth(dialog) {
  if (!dialog?.open) return Promise.resolve();
  if (reducedMotion.matches) {
    dialog.close();
    return Promise.resolve();
  }
  dialog.classList.add("is-closing");
  return new Promise((resolve) => {
    window.setTimeout(() => {
      if (dialog.open) dialog.close();
      dialog.classList.remove("is-closing");
      resolve();
    }, 170);
  });
}

function setupMotion() {
  document.documentElement.classList.add("motion-ready");
  const dynamicContainers = [
    "#lead-body",
    "#profile-list",
    "#profile-status-board",
    "#monitor-list",
    "#notification-list",
    "#source-health-list",
    "#event-list",
    "#activity-list",
  ].map($).filter(Boolean);
  const observer = new MutationObserver((entries) => {
    entries.forEach((entry) => revealMotionItems(entry.target));
  });
  dynamicContainers.forEach((container) => observer.observe(container, { childList: true }));

  const metricObserver = new MutationObserver((entries) => {
    entries.forEach((entry) => replayMotion(entry.target, "value-pop"));
  });
  $$(".metrics > div > span").forEach((metric) => {
    metricObserver.observe(metric, { childList: true, characterData: true, subtree: true });
  });

  requestAnimationFrame(() => {
    revealMotionItems($(".controls"), 8);
    revealMotionItems($(".metrics"), 4);
    animateVisibleWorkspace();
  });
}

function setSidebarCollapsed(collapsed, persist = true) {
  const app = $(".app");
  const button = $("#sidebar-toggle");
  if (!app || !button) return;
  app.classList.toggle("sidebar-collapsed", Boolean(collapsed));
  const expanded = !collapsed;
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute("aria-label", expanded ? "隐藏采集条件" : "显示采集条件");
  button.title = `${expanded ? "隐藏" : "显示"}采集条件`;
  if (persist) {
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
    } catch (error) {
      // Private browsing can disable storage; the interaction still works for this page.
    }
  }
  replayMotion($(".main"), "workspace-resize");
}

function setupWorkspaceChrome() {
  let collapsed = false;
  try {
    collapsed = window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch (error) {
    collapsed = false;
  }
  setSidebarCollapsed(collapsed, false);
  $("#sidebar-toggle")?.addEventListener("click", () => {
    setSidebarCollapsed(!$(".app").classList.contains("sidebar-collapsed"));
  });
  $("#mobile-results-button")?.addEventListener("click", () => {
    setSidebarCollapsed(true);
    window.scrollTo({ top: 0, behavior: reducedMotion.matches ? "auto" : "smooth" });
  });
  document.addEventListener("keydown", (event) => {
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "b") return;
    if (!desktopWorkspace.matches) return;
    event.preventDefault();
    setSidebarCollapsed(!$(".app").classList.contains("sidebar-collapsed"));
  });
  desktopWorkspace.addEventListener("change", () => setSidebarCollapsed(
    $(".app").classList.contains("sidebar-collapsed"),
    false,
  ));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function selectedValues(name) {
  return $$(`input[name="${name}"]:checked`).map((item) => item.value);
}

function setNotice(message, isError = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  replayMotion(notice, "notice-bump");
}

function selectedCollectionStrategy() {
  return document.querySelector('input[name="collectionStrategy"]:checked')?.value || "precision";
}

function applyCollectionStrategy(strategy = selectedCollectionStrategy()) {
  state.collectionStrategy = strategy;
  const settings = {
    precision: {
      pages: 1,
      fast: true,
      onlyPhone: true,
      strict: true,
      note: "优先返回证据较强、可直接联系的企业。",
    },
    balanced: {
      pages: 2,
      fast: false,
      onlyPhone: false,
      strict: true,
      note: "兼顾准确度和覆盖面，适合每周集中开发。",
    },
    coverage: {
      pages: 3,
      fast: false,
      onlyPhone: false,
      strict: false,
      note: "扩大候选范围，结果中会保留更多待核验企业。",
    },
  }[strategy] || {};
  if ($("#pages")) $("#pages").value = settings.pages || 1;
  if ($("#fast-mode")) $("#fast-mode").checked = Boolean(settings.fast);
  if ($("#only-phone")) {
    $("#only-phone").checked = state.direction === "downstream" && Boolean(settings.onlyPhone);
  }
  if ($("#strict-upstream")) $("#strict-upstream").checked = Boolean(settings.strict);
  if ($("#exclude-suppliers")) $("#exclude-suppliers").checked = strategy !== "coverage";
  if ($("#strategy-note")) $("#strategy-note").textContent = settings.note || "";
  if ($("#date-window") && state.direction === "procurement") {
    $("#date-window").value = strategy === "coverage" ? "90d" : strategy === "balanced" ? "30d" : "10d";
  }
}

function qualityFilterMatches(lead) {
  const value = $("#quality-filter")?.value || "";
  if (!value) return true;
  if (value === "actionable") return Boolean(lead.actionable);
  if (value === "AB") return ["A", "B"].includes(lead.quality_grade);
  if (value === "needs-contact") return !lead.phone && !lead.email;
  return lead.quality_grade === value;
}

function renderQualitySummary(leads) {
  const panel = $("#quality-summary");
  if (!panel) return;
  const visible = ["collect", "database", "profiles"].includes(state.view) && leads.length;
  panel.hidden = !visible;
  if (!visible) return;
  const gradeA = leads.filter((lead) => lead.quality_grade === "A").length;
  const gradeB = leads.filter((lead) => lead.quality_grade === "B").length;
  const actionable = leads.filter((lead) => lead.actionable).length;
  const needsContact = leads.filter((lead) => !lead.phone && !lead.email).length;
  panel.innerHTML = `
    <strong>质量概览</strong>
    <span><b>${gradeA}</b> A级已验证</span>
    <span><b>${gradeB}</b> B级优先核验</span>
    <span><b>${actionable}</b> 可立即跟进</span>
    <span><b>${needsContact}</b> 待补联系方式</span>
  `;
}

function renderSectors() {
  const container = $("#sector-list");
  const sectors = state.direction === "upstream"
    ? state.upstreamSectors
    : state.direction === "competitor"
      ? state.competitorSectors
    : state.direction === "environmental"
      ? state.environmentalSectors
    : state.direction === "procurement"
      ? state.procurementSectors
    : state.direction === "social"
      ? state.socialSectors
      : state.downstreamSectors;
  const defaults = new Set(
    state.direction === "social"
      ? ["liquid_calcium", "byproduct", "fluoride", "downstream", "industry_process"]
      : state.direction === "competitor"
      ? ["liquid", "anhydrous", "dihydrate", "deicing", "desiccant"]
      : state.direction === "environmental"
      ? ["fluorochemicals", "rare_earth", "phosphorus", "surface_treatment", "electronics"]
      : state.direction === "upstream"
      ? ["rare_earth", "epichlorohydrin", "fly_ash", "tungsten", "soda_ash"]
      : state.direction === "procurement"
        ? ["calcium_chloride", "liquid_calcium_chloride", "deicing", "upstream_disposal"]
        : ["snow", "desiccant", "water", "concrete", "trader"],
  );
  container.innerHTML = Object.entries(sectors)
    .map(([id, item]) => {
      const checked = defaults.has(id) ? "checked" : "";
      return `
        <label>
          <input type="checkbox" name="sector" value="${escapeHtml(id)}" ${checked} />
          ${escapeHtml(item.name)}
        </label>
      `;
    })
    .join("");
}

function leadMatches(lead, query) {
  if (
    state.view === "collect"
    && state.meta?.mode !== "task"
    && $("#only-phone")?.checked
    && !lead.phone
  ) return false;
  if (!qualityFilterMatches(lead)) return false;
  if (!query) return true;
  const haystack = [
    lead.company,
    lead.region,
    lead.sector,
    lead.phone,
    lead.address,
    lead.use_case,
    lead.pitch,
    lead.match_reason,
    lead.raw_type,
    lead.process_basis,
    lead.confidence,
    lead.source,
    lead.project_title,
    lead.notice_date,
    lead.contact_name,
    lead.agency,
    lead.deadline,
    lead.budget,
    lead.sales_status_label,
    lead.owner,
    lead.notes,
    lead.next_follow_up,
    lead.opportunity_role,
    lead.liquid_concentration,
    lead.monthly_volume,
    lead.impurity_profile,
    lead.logistics_radius,
    lead.commercial_value,
    lead.competitor_industries,
    lead.competitor_regions,
    lead.competitor_keywords,
    lead.social_platform,
    lead.social_account,
    lead.social_content_type,
    lead.social_matched_keywords,
    lead.social_discovery_method,
    lead.social_intent,
    lead.social_intent_reasons,
    lead.social_positive_hits,
    lead.social_entity_candidate,
    lead.social_entity_status,
    lead.feedback_label,
    lead.competitor_channels,
    lead.quality_grade,
    lead.quality_label,
    lead.quality_reasons,
    lead.quality_issues,
    lead.recommended_action,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function localDateKey(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function workQueueMatches(lead) {
  const queue = $("#work-queue-filter")?.value || "";
  if (!queue || !["database", "profiles"].includes(state.view)) return true;
  const status = lead.sales_status || "new";
  if (queue === "due") {
    return Boolean(lead.next_follow_up)
      && String(lead.next_follow_up).slice(0, 10) <= localDateKey()
      && !["won", "lost"].includes(status);
  }
  if (queue === "hot-new") return Number(lead.score || 0) >= 70 && status === "new";
  if (queue === "unassigned") return !String(lead.owner || "").trim() && !["won", "lost"].includes(status);
  if (queue === "new") return status === "new";
  if (queue === "needs-contact") return !lead.phone && !lead.email;
  return true;
}

function currentPageKey() {
  return state.view === "profiles" ? "profiles" : "database";
}

function resetCurrentPage() {
  if (["database", "profiles"].includes(state.view)) {
    state.pages[currentPageKey()] = 1;
  }
}

function renderPagination(total) {
  const panel = $("#pagination");
  const enabled = ["database", "profiles"].includes(state.view) && total > 0;
  panel.hidden = !enabled;
  if (!enabled) {
    state.pageItems = state.filtered;
    return state.filtered;
  }
  const key = currentPageKey();
  const pageCount = Math.max(1, Math.ceil(total / state.pageSize));
  state.pages[key] = Math.min(Math.max(1, state.pages[key] || 1), pageCount);
  const page = state.pages[key];
  const start = (page - 1) * state.pageSize;
  const end = Math.min(total, start + state.pageSize);
  state.pageItems = state.filtered.slice(start, end);
  $("#pagination-summary").textContent = `共 ${total} 条，当前 ${start + 1}-${end}`;
  $("#page-current").textContent = `${page} / ${pageCount}`;
  $("#page-prev").disabled = page <= 1;
  $("#page-next").disabled = page >= pageCount;
  $("#page-size").value = String(state.pageSize);
  return state.pageItems;
}

function scoreClass(score) {
  if (score >= 70) return "score";
  if (score >= 50) return "score mid";
  return "score low";
}

function splitPhones(value) {
  return String(value || "")
    .split(/[;；/、,，\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function telHref(value) {
  const cleaned = String(value || "").replace(/[^\d+]/g, "");
  return cleaned ? `tel:${cleaned}` : "";
}

async function copyText(value) {
  const text = String(value || "").trim();
  if (!text) return;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (error) {
      // Some embedded browsers block the async Clipboard API; fall back to selection copy.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("copy failed");
}

function renderMetrics(leads) {
  if (state.view === "profiles") {
    const source = state.profileFiltered.length || $("#filter")?.value || $("#status-filter")?.value || $("#direction-filter")?.value || $("#work-queue-filter")?.value
      ? state.profileFiltered
      : state.profiles;
    $("#metric-count").textContent = source.length;
    $("#metric-hot").textContent = source.filter((lead) => ["qualified", "quoted"].includes(lead.sales_status)).length;
    $("#metric-phone").textContent = source.filter((lead) => lead.next_follow_up).length;
    $("#metric-count-label").textContent = "档案数量";
    $("#metric-hot-label").textContent = "重点跟进";
    $("#metric-phone-label").textContent = "已排跟进";
    $("#metric-mode").textContent = source.filter((lead) => lead.sales_status === "won").length;
    $("#metric-mode-label").textContent = "已成交";
    return;
  }
  if (state.view === "database") {
    $("#metric-count").textContent = leads.length;
    $("#metric-hot").textContent = leads.filter((lead) => Number(lead.score || 0) >= 70).length;
    $("#metric-phone").textContent = leads.filter((lead) => (
      lead.next_follow_up
      && String(lead.next_follow_up).slice(0, 10) <= localDateKey()
      && !["won", "lost"].includes(lead.sales_status || "new")
    )).length;
    $("#metric-count-label").textContent = "当前线索";
    $("#metric-hot-label").textContent = "高潜线索";
    $("#metric-phone-label").textContent = "到期跟进";
    $("#metric-mode").textContent = leads.filter((lead) => lead.phone || lead.email).length;
    $("#metric-mode-label").textContent = "可联系";
    return;
  }
  if (state.view !== "collect") {
    $("#metric-count").textContent = state.dashboard.total || 0;
    $("#metric-hot").textContent = state.dashboard.highScore || 0;
    $("#metric-phone").textContent = state.dashboard.dueFollowUps || 0;
    $("#metric-count-label").textContent = "数据库线索";
    $("#metric-hot-label").textContent = "高潜线索";
    $("#metric-phone-label").textContent = "到期跟进";
    $("#metric-mode").textContent = state.dashboard.unreadNotifications || 0;
    $("#metric-mode-label").textContent = "未读提醒";
    return;
  }
  const procurement = state.meta?.direction === "procurement" || state.direction === "procurement";
  const environmental = state.meta?.direction === "environmental" || state.direction === "environmental";
  const competitor = state.meta?.direction === "competitor" || state.direction === "competitor";
  const social = state.meta?.direction === "social" || state.direction === "social";
  $("#metric-count").textContent = leads.length;
  $("#metric-hot").textContent = social
    ? leads.filter((lead) => Number(lead.social_intent_score || 0) >= 60 && !["irrelevant", "duplicate"].includes(lead.feedback_status)).length
    : leads.filter((lead) => ["A", "B"].includes(lead.quality_grade)).length;
  $("#metric-phone").textContent = social
    ? new Set(leads.map((lead) => lead.social_platform).filter(Boolean)).size
    : competitor
    ? leads.filter((lead) => lead.company_website).length
    : environmental
    ? leads.filter((lead) => lead.poi_id).length
    : leads.filter((lead) => lead.phone).length;
  $("#metric-count-label").textContent = social ? "社媒账号/内容" : competitor ? "同行供应商" : procurement ? "采购单位" : environmental ? "含氟企业" : "线索数量";
  $("#metric-hot-label").textContent = social ? "高意向" : competitor ? "重点同行" : procurement ? "重点项目" : "A/B级线索";
  $("#metric-phone-label").textContent = social ? "覆盖平台" : competitor ? "已定位官网" : environmental ? "证据记录" : "含电话";
  const modeLabels = {
    amap: "真实企业",
    baidu: "真实企业",
    tianditu: "真实企业",
    maps: "地图多源",
    task: "开发任务",
    need_key: "缺少 Key",
    procurement: "招采监控",
    environmental: "环保多源",
    competitor: "竞品情报",
    social: "社媒公开索引",
  };
  const mode = state.meta?.direction === "upstream" && ["amap", "baidu", "tianditu", "maps"].includes(state.meta?.mode)
    ? "上游副产"
    : state.leads.length || state.meta?.mode
      ? modeLabels[state.meta?.mode] || "待开始"
      : "待开始";
  $("#metric-mode").textContent = mode;
  $("#metric-mode-label").textContent = "采集模式";
}

function renderLeads() {
  const query = $("#filter").value.trim();
  const source = state.view === "database" ? state.savedLeads : state.leads;
  const status = $("#status-filter")?.value || "";
  const direction = $("#direction-filter")?.value || "";
  state.filtered = source.filter((lead) => {
    if (state.view === "database" && status && lead.sales_status !== status) return false;
    if (state.view === "database" && direction && lead.direction !== direction) return false;
    if (!workQueueMatches(lead)) return false;
    return leadMatches(lead, query);
  });
  renderMetrics(state.filtered);
  renderQualitySummary(state.filtered);

  const tbody = $("#lead-body");
  if (!state.filtered.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="9">没有匹配的线索。</td></tr>`;
    renderPagination(0);
    updateBulkToolbar();
    return;
  }

  const displayedLeads = renderPagination(state.filtered.length);
  const pageStart = state.view === "database"
    ? (state.pages.database - 1) * state.pageSize
    : 0;
  tbody.innerHTML = displayedLeads
    .map((lead, index) => {
      const absoluteIndex = pageStart + index;
      const environmental = lead.direction === "environmental";
      const competitor = lead.direction === "competitor";
      const social = lead.direction === "social";
      const phone = lead.phone || "待补充";
      const phoneContent = lead.phone
        ? `<button class="phone-button" type="button" data-detail-index="${absoluteIndex}" title="查看电话和企业资料">${escapeHtml(lead.phone)}</button>`
        : `<button class="phone-button missing" type="button" data-detail-index="${absoluteIndex}" title="查看企业资料">待补充</button>`;
      const contactOrSource = social
        ? `${lead.social_platform || "未知平台"} · ${lead.evidence_count || 1} 条`
        : competitor
        ? `${lead.evidence_count || 0} 条证据`
        : environmental
          ? (lead.poi_id || "待核验")
          : phoneContent;
      const address = lead.address ? `<div class="subline">${escapeHtml(lead.address)}</div>` : "";
      const type = lead.raw_type ? `<div class="subline">${escapeHtml(lead.raw_type)}</div>` : "";
      const project = lead.project_title
        ? `<div class="subline">${escapeHtml(lead.project_title)}</div>`
        : "";
      const companyWebsiteNotice = lead.source?.includes("企业官网");
      const searchUrl = lead.search_url
        ? `<a href="${escapeHtml(lead.search_url)}" target="_blank" rel="noreferrer">${social ? "打开原内容" : lead.direction === "procurement" ? (companyWebsiteNotice ? "官网公告" : "公告正文") : competitor ? "来源检索" : environmental ? "证据原文" : "搜索"}</a>`
        : "";
      const website = lead.website && lead.website !== lead.search_url
        ? `<a href="${escapeHtml(lead.website)}" target="_blank" rel="noreferrer">${lead.direction === "procurement" ? "公告页面" : competitor ? "证据页" : "核验"}</a>`
        : "";
      const companyWebsite = lead.company_website && (companyWebsiteNotice || competitor)
        ? `<a href="${escapeHtml(lead.company_website)}" target="_blank" rel="noreferrer">企业官网</a>`
        : "";
      const qcc = lead.qcc_url
        ? `<a href="${escapeHtml(lead.qcc_url)}" target="_blank" rel="noreferrer">${lead.direction === "procurement" ? "公司核验" : "企查查"}</a>`
        : "";
      const detail = `<button class="detail-button" type="button" data-detail-index="${absoluteIndex}">详情</button>`;
      const reverseAction = competitor
        ? `<button class="detail-button reverse-button" type="button" data-reverse-index="${absoluteIndex}">反向开发</button>`
        : "";
      const confidence = lead.confidence
        ? `<span class="confidence confidence-${lead.confidence === "高" ? "high" : lead.confidence === "中" ? "medium" : "review"}">${escapeHtml(lead.confidence)}${lead.confidence.startsWith("官方") ? "" : "相关"}</span>`
        : "";
      const qualityGrade = lead.quality_grade
        ? `<span class="quality-badge quality-${escapeHtml(lead.quality_grade.toLowerCase())}" title="${escapeHtml(lead.quality_reasons || "")}">${escapeHtml(lead.quality_grade)}</span>`
        : "";
      const intentBadge = social && lead.social_intent
        ? `<span class="intent-badge" title="${escapeHtml(lead.social_intent_reasons || "")}">${escapeHtml(lead.social_intent)} · ${escapeHtml(lead.social_intent_score || 0)}</span>`
        : "";
      const feedbackBadge = social && lead.feedback_label
        ? `<span class="sales-status status-${lead.feedback_status === "valid" ? "qualified" : "lost"}">${escapeHtml(lead.feedback_label)}</span>`
        : "";
      const scoreDetails = Object.entries(lead.score_details || {})
        .map(([key, value]) => `${key} ${value}`)
        .join(" · ");
      const salesStatus = state.view === "database"
        ? `<span class="sales-status status-${escapeHtml(lead.sales_status || "new")}">${escapeHtml(lead.sales_status_label || "待核实")}</span>`
        : "";
      const salesMeta = state.view === "database"
        ? `<div class="subline">${lead.owner ? `负责人：${escapeHtml(lead.owner)}` : "暂未分配"}${lead.next_follow_up ? ` · 跟进：${escapeHtml(lead.next_follow_up.replace("T", " "))}` : ""}</div>`
        : "";
      const databaseAction = state.view === "database"
        ? `<button class="detail-button" type="button" data-detail-index="${absoluteIndex}">跟进</button>`
        : detail;
      const roleLabels = {
        buyer: "液钙买家",
        supplier: "液钙货源",
        prospect: "工艺候选",
      };
      const role = lead.opportunity_role
        ? `<span class="role-badge role-${escapeHtml(lead.opportunity_role)}">${escapeHtml(competitor ? "竞品同行" : roleLabels[lead.opportunity_role] || lead.opportunity_role)}</span>`
        : "";
      const checked = state.selectedLeadIds.has(Number(lead.id)) ? "checked" : "";
      return `
        <tr>
          <td class="select-column">
            ${state.view === "database" && lead.id
              ? `<input type="checkbox" data-select-lead="${lead.id}" aria-label="选择 ${escapeHtml(lead.company)}" ${checked} />`
              : ""}
          </td>
          <td>
            <div class="score-line"><span class="${scoreClass(Number(lead.score))}" title="${escapeHtml(scoreDetails)}">${escapeHtml(lead.score)}</span>${qualityGrade}</div>
            ${scoreDetails ? `<div class="score-breakdown">${escapeHtml(scoreDetails)}</div>` : ""}
          </td>
          <td>
            <button class="company company-button" type="button" data-detail-index="${absoluteIndex}" title="查看公司信息、电话和证据">
              ${escapeHtml(lead.company)}
            </button>
            ${salesStatus}
            ${role}
            ${salesMeta}
            ${social ? `<span class="platform-badge platform-${escapeHtml(lead.social_platform_id || "other")}">${escapeHtml(lead.social_platform || "社媒")}</span>` : ""}
            ${intentBadge}
            ${feedbackBadge}
            ${project}
            ${address}
            ${type}
          </td>
          <td>${escapeHtml(lead.sector)}</td>
          <td>${escapeHtml(lead.region)}</td>
          <td>${competitor || environmental || social ? escapeHtml(contactOrSource) : contactOrSource}</td>
          <td>
            ${escapeHtml(lead.match_reason)}
            ${confidence}
            <div class="subline">${escapeHtml(lead.use_case)}</div>
          </td>
          <td>${escapeHtml(lead.pitch)}</td>
          <td><div class="link-group">${databaseAction}${reverseAction}${searchUrl}${website}${companyWebsite}${qcc}</div></td>
        </tr>
      `;
    })
    .join("");
  updateBulkToolbar();
}

async function fetchConfig() {
  const response = await fetch("/api/config");
  if (response.status === 401) {
    window.location.href = "/login?v=pnvs-login-1";
    return;
  }
  if (!response.ok) throw new Error("配置加载失败");
  const config = await response.json();
  state.downstreamSectors = config.downstreamSectors || config.sectors;
  state.upstreamSectors = config.upstreamSectors || {};
  state.competitorSectors = config.competitorSectors || {};
  state.socialSectors = config.socialSectors || {};
  state.socialPlatforms = config.socialPlatforms || {};
  state.environmentalSectors = config.environmentalSectors || state.upstreamSectors;
  state.procurementSectors = config.procurementSectors || {};
  state.regionPresets = config.regionPresets || {};
  state.hasEnvAmapKey = Boolean(config.hasEnvAmapKey);
  state.hasEnvBaiduMapAk = Boolean(config.hasEnvBaiduMapAk);
  state.hasEnvTiandituTk = Boolean(config.hasEnvTiandituTk);
  state.hasEnvBaiduSearchApiKey = Boolean(config.hasEnvBaiduSearchApiKey);
  state.tursoConfigured = Boolean(config.tursoConfigured);
  renderSectors();
  const mapSources = [
    state.hasEnvAmapKey ? "高德" : "",
    state.hasEnvBaiduMapAk ? "百度地图" : "",
    state.hasEnvTiandituTk ? "天地图" : "",
  ].filter(Boolean);
  $("#api-status").textContent = mapSources.length
    ? `企业采集服务已启用：${mapSources.join(" + ")}`
    : "企业采集服务未配置：请填写 AMAP_KEY、BAIDU_MAP_AK 或 TIANDITU_TK";
  $("#api-status").classList.toggle("error", !mapSources.length);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.href = "/login?v=pnvs-login-1";
    throw new Error("登录已过期");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function loadDashboard() {
  state.dashboard = await fetchJson("/api/dashboard");
  $("#database-count").textContent = state.dashboard.total || 0;
  $("#profile-count").textContent = state.dashboard.total || 0;
  $("#alert-count").textContent = state.dashboard.unreadNotifications || 0;
  $("#system-count").textContent = state.dashboard.unresolvedEvents || 0;
  if (state.view === "database") renderMetrics(state.filtered);
  else if (state.view === "profiles") renderMetrics(state.profileFiltered);
  else if (state.view !== "collect") renderMetrics([]);
}

async function loadLeadStore(force = false) {
  const fresh = state.leadStoreLoadedAt
    && Date.now() - state.leadStoreLoadedAt < LEAD_STORE_TTL_MS;
  if (!force && fresh) return;
  const data = await fetchJson("/api/leads?limit=5000");
  const leads = data.leads || [];
  state.savedLeads = leads;
  state.profiles = leads;
  state.leadStoreLoadedAt = Date.now();
}

async function loadSavedLeads(force = false) {
  await loadLeadStore(force);
  $("#export-button").disabled = !state.savedLeads.length;
  renderLeads();
}

function renderProfileStatusBoard() {
  const counts = SALES_STATUS_ORDER.reduce((acc, key) => {
    acc[key] = state.profiles.filter((lead) => (lead.sales_status || "new") === key).length;
    return acc;
  }, {});
  $("#profile-status-board").innerHTML = SALES_STATUS_ORDER.map((key) => `
    <button class="profile-status-card" type="button" data-profile-status="${escapeHtml(key)}" title="只看${escapeHtml(SALES_STATUS_LABELS[key])}">
      <span>${counts[key] || 0}</span>
      <p>${escapeHtml(SALES_STATUS_LABELS[key])}</p>
    </button>
  `).join("");
}

function renderProfiles() {
  const query = $("#filter").value.trim();
  const status = $("#status-filter")?.value || "";
  const direction = $("#direction-filter")?.value || "";
  state.profileFiltered = state.profiles.filter((lead) => {
    if (status && lead.sales_status !== status) return false;
    if (direction && lead.direction !== direction) return false;
    if (!workQueueMatches(lead)) return false;
    return leadMatches(lead, query);
  });
  state.filtered = state.profileFiltered;
  renderProfileStatusBoard();
  renderMetrics(state.profileFiltered);
  renderQualitySummary(state.profileFiltered);

  const list = $("#profile-list");
  if (!state.profileFiltered.length) {
    list.innerHTML = `<div class="empty-state">没有匹配的公司档案，可以点击右上角手动新增。</div>`;
    renderPagination(0);
    return;
  }
  const roleLabels = {
    buyer: "液钙买家",
    supplier: "液钙货源",
    prospect: "工艺候选",
  };
  const displayedProfiles = renderPagination(state.profileFiltered.length);
  const pageStart = (state.pages.profiles - 1) * state.pageSize;
  list.innerHTML = displayedProfiles.map((lead, index) => {
    const absoluteIndex = pageStart + index;
    const statusKey = lead.sales_status || "new";
    const phone = lead.phone
      ? `<a href="${escapeHtml(telHref(splitPhones(lead.phone)[0] || lead.phone))}">${escapeHtml(lead.phone)}</a>`
      : "待补充电话";
    const followUp = lead.next_follow_up ? formatDateTime(lead.next_follow_up) : "未设置跟进";
    const note = lead.notes || lead.match_reason || lead.use_case || "暂无备注";
    const tags = [
      DIRECTION_LABELS[lead.direction] || "未分类",
      roleLabels[lead.opportunity_role] || lead.opportunity_role,
      lead.liquid_concentration ? `浓度 ${lead.liquid_concentration}` : "",
      lead.monthly_volume ? `月量 ${lead.monthly_volume}` : "",
      lead.commercial_value ? `价值 ${lead.commercial_value}` : "",
    ].filter(Boolean);
    return `
      <article class="profile-card">
        <div>
          <div class="profile-card-head">
            <h4>${escapeHtml(lead.company)}</h4>
            <span class="sales-status status-${escapeHtml(statusKey)}">${escapeHtml(SALES_STATUS_LABELS[statusKey] || lead.sales_status_label || "待核实")}</span>
          </div>
          <div class="profile-meta">
            ${escapeHtml(lead.region || "地区待补充")} · ${escapeHtml(lead.sector || "行业待补充")} · ${phone}
          </div>
          <div class="profile-meta">
            负责人：${escapeHtml(lead.owner || "未分配")} · 下次跟进：${escapeHtml(followUp)}
          </div>
          <div class="profile-note">${escapeHtml(note)}</div>
          <div class="profile-tags">
            ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
          </div>
        </div>
        <div class="profile-actions">
          <button class="secondary" type="button" data-profile-detail-index="${absoluteIndex}">查看/跟进</button>
        </div>
      </article>
    `;
  }).join("");
}

function renderCurrentResults() {
  if (state.view === "profiles") renderProfiles();
  else renderLeads();
}

async function loadProfiles(force = false) {
  await loadLeadStore(force);
  $("#profile-count").textContent = state.profiles.length;
  $("#export-button").disabled = !state.profiles.length;
  renderProfiles();
}

function openProfileDialog() {
  $("#profile-form").reset();
  $("#profile-direction").value = state.direction || "downstream";
  $("#profile-sales-status").value = "new";
  showDialogSmooth($("#profile-dialog"));
}

async function saveProfile(event) {
  event.preventDefault();
  const button = $("#profile-form button[type='submit']");
  button.disabled = true;
  try {
    await fetchJson("/api/leads/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company: $("#profile-company").value,
        direction: $("#profile-direction").value,
        salesStatus: $("#profile-sales-status").value,
        phone: $("#profile-phone").value,
        region: $("#profile-region").value,
        sector: $("#profile-sector").value,
        owner: $("#profile-owner").value,
        nextFollowUp: $("#profile-next-follow-up").value,
        opportunityRole: $("#profile-opportunity-role").value,
        email: $("#profile-email").value,
        website: $("#profile-website").value,
        companyWebsite: $("#profile-website").value,
        address: $("#profile-address").value,
        liquidConcentration: $("#profile-liquid-concentration").value,
        monthlyVolume: $("#profile-monthly-volume").value,
        logisticsRadius: $("#profile-logistics-radius").value,
        commercialValue: $("#profile-commercial-value").value,
        useCase: $("#profile-use-case").value,
        matchReason: $("#profile-match-reason").value,
        impurityProfile: $("#profile-impurity-profile").value,
        storageCondition: $("#profile-storage-condition").value,
        notes: $("#profile-notes").value,
      }),
    });
    await closeDialogSmooth($("#profile-dialog"));
    await Promise.all([loadProfiles(true), loadDashboard()]);
    setNotice("公司档案已保存，重复公司会自动合并。");
  } catch (error) {
    setNotice(error.message || "档案保存失败", true);
  } finally {
    button.disabled = false;
  }
}

function formatDateTime(value) {
  if (!value) return "尚未运行";
  return String(value).replace("T", " ").slice(0, 16);
}

function monitorDirection(monitor) {
  return monitor.direction || monitor.payload?.direction || "downstream";
}

function renderMonitorCard(monitor) {
  const running = Boolean(monitor.running);
  const stateLabel = running ? "执行中" : monitor.enabled ? "已启用" : "已暂停";
  return `
    <article class="monitor-row">
      <div>
        <div class="monitor-title">
          ${escapeHtml(monitor.name)}
          <span class="monitor-state ${monitor.enabled || running ? "enabled" : ""}">${stateLabel}</span>
        </div>
        <p>${escapeHtml(monitor.summary || `每 ${monitor.intervalHours} 小时 · 下次 ${formatDateTime(monitor.nextRun)}`)}</p>
        <p>${escapeHtml(monitor.lastResult || monitor.lastError || "等待首次检查")}</p>
      </div>
      <div class="row-actions">
        <button type="button" data-monitor-run="${monitor.id}" title="立即运行" ${running ? "disabled" : ""}>${running ? "执行中" : "运行"}</button>
        <button type="button" data-monitor-toggle="${monitor.id}" data-enabled="${monitor.enabled ? "0" : "1"}">${monitor.enabled ? "暂停" : "启用"}</button>
        <button type="button" data-monitor-delete="${monitor.id}" class="danger-link">删除</button>
      </div>
    </article>
  `;
}

function renderAlerts() {
  const grouped = DIRECTION_ORDER.map((direction) => ({
    direction,
    label: DIRECTION_LABELS[direction],
    monitors: state.monitors.filter((monitor) => monitorDirection(monitor) === direction),
  }));
  $("#monitor-list").innerHTML = grouped.map((group) => `
    <section class="monitor-group">
      <div class="monitor-group-head">
        <strong>${escapeHtml(group.label)}</strong>
        <span>${group.monitors.length} 个监控</span>
      </div>
      ${group.monitors.length
        ? group.monitors.map(renderMonitorCard).join("")
        : `<div class="empty-state compact">暂无该方向监控。切换到“${escapeHtml(group.label)}”后点击保存为监控。</div>`}
    </section>
  `).join("");

  $("#notification-list").innerHTML = state.notifications.length
    ? state.notifications.map((item) => `
      <button class="notification-row ${item.isRead ? "read" : ""} ${item.leadId ? "actionable" : ""}" type="button" data-notification-id="${item.id}" data-lead-id="${item.leadId || ""}">
        <span class="notification-dot"></span>
        <span>
          <strong>${escapeHtml(item.title)}</strong>
          <small>${escapeHtml(item.message)}</small>
          ${item.leadId ? `<em>点击查看公司详情</em>` : ""}
          <time>${escapeHtml(formatDateTime(item.createdAt))}</time>
        </span>
      </button>
    `).join("")
    : `<div class="empty-state">暂无提醒。</div>`;
}

async function loadAlerts() {
  const [monitorData, notificationData] = await Promise.all([
    fetchJson("/api/monitors"),
    fetchJson("/api/notifications"),
  ]);
  state.monitors = monitorData.monitors || [];
  state.notifications = notificationData.notifications || [];
  renderAlerts();
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderSystem() {
  const data = state.system || {};
  const versionLabel = data.version === "liquid-calcium-ops-v2"
    ? "液钙运营版 v2"
    : data.version || "未知";
  const databaseMode = data.tursoConfigured
    ? "Turso 云同步"
    : data.tursoEnvConfigured
      ? "SQLite 自动降级"
      : "本地 SQLite";
  $("#system-health").innerHTML = [
    ["系统版本", versionLabel],
    ["数据库", formatBytes(data.databaseSize)],
    ["数据库模式", databaseMode],
    ["高德采集", data.amapConfigured ? "已配置" : "未配置"],
    ["百度地图采集", data.baiduMapConfigured ? "已配置" : "未配置"],
    ["天地图采集", data.tiandituConfigured ? "已配置" : "未配置"],
    [
      "百度千帆网页搜索",
      data.baiduSearchConfigured
        ? `已配置 · 今日 ${data.baiduSearchUsageToday || 0}/${data.baiduSearchDailyLimit || 45}`
        : "未配置",
    ],
    ["短信认证", data.smsConfigured ? "已配置" : "未配置"],
  ].map(([label, value]) => `
    <div><span>${escapeHtml(value)}</span><p>${escapeHtml(label)}</p></div>
  `).join("");

  $("#source-health-list").innerHTML = data.sources?.length
    ? data.sources.map((source) => `
      <div class="health-row">
        <div>
          <strong>${escapeHtml(source.source)}</strong>
          <small>最近记录 ${escapeHtml(formatDateTime(source.lastEvent))}</small>
        </div>
        <span class="${source.errors ? "health-error" : source.warnings ? "health-warning" : "health-ok"}">
          ${source.errors ? `${source.errors} 错误` : source.warnings ? `${source.warnings} 警告` : "正常"}
        </span>
      </div>
    `).join("")
    : `<div class="empty-state">暂时没有数据源异常记录。</div>`;

  $("#event-list").innerHTML = data.events?.length
    ? data.events.map((event) => `
      <article class="event-row ${event.resolved ? "resolved" : ""}">
        <span class="event-level level-${escapeHtml(event.level)}">${event.level === "error" ? "错误" : event.level === "warning" ? "警告" : "信息"}</span>
        <div>
          <strong>${escapeHtml(event.source || event.category)}</strong>
          <p>${escapeHtml(event.message)}</p>
          <time>${escapeHtml(formatDateTime(event.createdAt))}</time>
        </div>
        ${event.resolved ? "" : `<button type="button" data-resolve-event="${event.id}">处理</button>`}
      </article>
    `).join("")
    : `<div class="empty-state">没有错误记录，系统运行正常。</div>`;

  $("#activity-list").innerHTML = data.activity?.length
    ? data.activity.map((item) => `
      <div class="activity-row">
        <span>${escapeHtml(item.summary)}</span>
        <time>${escapeHtml(formatDateTime(item.createdAt))}</time>
      </div>
    `).join("")
    : `<div class="empty-state">暂无操作记录。</div>`;
}

async function loadSystem() {
  state.system = await fetchJson("/api/system");
  renderSystem();
}

async function switchView(view) {
  state.view = ["database", "profiles", "alerts", "system"].includes(view) ? view : "collect";
  $$(".workspace-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
  const alerts = state.view === "alerts";
  const database = state.view === "database";
  const profiles = state.view === "profiles";
  const system = state.view === "system";
  $("#alerts-panel").hidden = !alerts;
  $("#profiles-panel").hidden = !profiles;
  $("#system-panel").hidden = !system;
  $(".table-shell").hidden = alerts || profiles || system;
  $(".filters").hidden = alerts || system;
  $("#notice").hidden = alerts || system;
  $("#quality-summary").hidden = alerts || system;
  $("#pagination").hidden = alerts || system || state.view === "collect";
  $("#bulk-toolbar").hidden = !database || !state.selectedLeadIds.size;
  $("#progress-panel").hidden = alerts || system || !state.activeJobId;
  $("#quick-filters").hidden = database || profiles;
  $("#crm-filters").hidden = !(database || profiles);
  $("#select-all-leads").hidden = !database;
  $("#save-monitor-button").hidden = state.view !== "collect";
  $("#task-button").hidden = state.view !== "collect" || ["procurement", "environmental", "competitor"].includes(state.direction);
  $("#export-button").hidden = alerts || system;
  $("#result-title").textContent = database
    ? "销售线索数据库"
    : profiles
      ? "公司档案"
    : alerts
      ? "监控与跟进提醒"
      : system
        ? "系统运行中心"
      : state.direction === "competitor"
        ? "竞品/同行客户挖掘"
      : state.direction === "upstream"
        ? "液体氯化钙副产企业线索"
        : state.direction === "environmental"
          ? "含氟废水企业雷达"
          : state.direction === "procurement"
            ? "招投标/采购信息监控"
            : "潜在买家列表";
  $("#filter").placeholder = database || profiles ? "搜索公司、负责人、备注、电话" : "输入公司、行业、地区、用途";
  if (database) await loadSavedLeads();
  else if (profiles) await loadProfiles();
  else if (alerts) await loadAlerts();
  else if (system) await loadSystem();
  else renderLeads();
  if (database) {
    setNotice("数据库会自动合并重复企业，并保留销售状态、负责人、备注和跟进计划。");
  } else if (profiles) {
    setNotice("公司档案用于沉淀已跟进企业、手动新增客户和液钙供需参数。");
  }
  await loadDashboard();
  animateVisibleWorkspace();
}

function buildPayload() {
  const presets = selectedValues("regionPreset");
  const customRegions = $("#regions").value.trim();
  const regions = [...presets];
  if (customRegions) {
    regions.push(...customRegions.split(/[,，、;\s]+/).filter(Boolean));
  }

  return {
    regions,
    direction: state.direction,
    sectors: selectedValues("sector"),
    customKeywords: $("#custom-keywords").value.trim(),
    pages: Number($("#pages").value || 1),
    fastMode: $("#fast-mode").checked,
    excludeSuppliers: $("#exclude-suppliers").checked,
    strictUpstream: $("#strict-upstream").checked,
    noticeTypes: selectedValues("noticeType"),
    procurementSources: selectedValues("procurementSource"),
    environmentalSources: selectedValues("environmentalSource"),
    competitorSources: selectedValues("competitorSource"),
    socialPlatforms: selectedValues("socialPlatform"),
    socialLinks: $("#social-links")?.value || "",
    socialPositiveKeywords: $("#social-positive-keywords")?.value || "",
    socialNegativeKeywords: $("#social-negative-keywords")?.value || "",
    competitorDeepScan: $("#competitor-deep-scan").checked,
    dateWindow: $("#date-window").value,
    collectionStrategy: selectedCollectionStrategy(),
  };
}

function setCheckedValues(name, values = []) {
  const selected = new Set(values);
  $$(`input[name="${name}"]`).forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function readSearchDrafts() {
  try {
    return JSON.parse(localStorage.getItem(SEARCH_DRAFT_STORAGE_KEY) || "{}");
  } catch (error) {
    return {};
  }
}

function saveSearchDraft(payload) {
  try {
    const drafts = readSearchDrafts();
    drafts[payload.direction] = { ...payload, savedAt: new Date().toISOString() };
    localStorage.setItem(SEARCH_DRAFT_STORAGE_KEY, JSON.stringify(drafts));
  } catch (error) {
    // Private browsing can block local storage; search still works without draft recovery.
  }
}

function restoreSearchDraft(direction) {
  const draft = readSearchDrafts()[direction];
  if (!draft) return false;
  const strategy = ["precision", "balanced", "coverage"].includes(draft.collectionStrategy)
    ? draft.collectionStrategy
    : "precision";
  const strategyInput = document.querySelector(`input[name="collectionStrategy"][value="${strategy}"]`);
  if (strategyInput) strategyInput.checked = true;
  applyCollectionStrategy(strategy);

  const regionPresets = (draft.regions || []).filter((region) => REGION_LABELS[region]);
  const customRegions = (draft.regions || []).filter((region) => !REGION_LABELS[region]);
  setCheckedValues("regionPreset", regionPresets);
  $("#regions").value = customRegions.join(", ");
  setCheckedValues("sector", draft.sectors || []);
  setCheckedValues("noticeType", draft.noticeTypes || []);
  setCheckedValues("procurementSource", draft.procurementSources || []);
  setCheckedValues("environmentalSource", draft.environmentalSources || []);
  setCheckedValues("competitorSource", draft.competitorSources || []);
  setCheckedValues("socialPlatform", draft.socialPlatforms || []);
  $("#custom-keywords").value = draft.customKeywords || "";
  $("#pages").value = String(draft.pages || 1);
  $("#fast-mode").checked = Boolean(draft.fastMode);
  $("#exclude-suppliers").checked = Boolean(draft.excludeSuppliers);
  $("#strict-upstream").checked = Boolean(draft.strictUpstream);
  $("#competitor-deep-scan").checked = Boolean(draft.competitorDeepScan);
  $("#date-window").value = draft.dateWindow || $("#date-window").value;
  if ($("#social-links")) $("#social-links").value = draft.socialLinks || "";
  if ($("#social-positive-keywords")) $("#social-positive-keywords").value = draft.socialPositiveKeywords || "";
  if ($("#social-negative-keywords")) $("#social-negative-keywords").value = draft.socialNegativeKeywords || "";
  return true;
}

function sectorLibraryForDirection(direction) {
  if (direction === "upstream") return state.upstreamSectors;
  if (direction === "procurement") return state.procurementSectors;
  if (direction === "environmental") return state.environmentalSectors;
  if (direction === "competitor") return state.competitorSectors;
  if (direction === "social") return state.socialSectors;
  return state.downstreamSectors;
}

function defaultSectorsForDirection(direction) {
  if (direction === "competitor") return ["liquid", "anhydrous", "dihydrate", "deicing", "desiccant"];
  if (direction === "social") return ["liquid_calcium", "byproduct", "fluoride", "downstream", "industry_process"];
  if (direction === "environmental") return ["fluorochemicals", "rare_earth", "phosphorus", "surface_treatment", "electronics"];
  if (direction === "upstream") return ["rare_earth", "epichlorohydrin", "fly_ash", "tungsten", "soda_ash"];
  if (direction === "procurement") return ["calcium_chloride", "liquid_calcium_chloride", "deicing", "upstream_disposal"];
  return ["snow", "desiccant", "water", "concrete", "trader"];
}

function monitorSelectedValues(name) {
  return $$(`#monitor-form input[name="${name}"]:checked`).map((item) => item.value);
}

function renderMonitorRegions(selected = []) {
  const selectedSet = new Set(selected);
  $("#monitor-region-list").innerHTML = Object.entries(REGION_LABELS).map(([id, label]) => `
    <label>
      <input type="checkbox" name="monitorRegionPreset" value="${escapeHtml(id)}" ${selectedSet.has(id) ? "checked" : ""} />
      ${escapeHtml(label)}
    </label>
  `).join("");
}

function renderMonitorSectors(direction, selected = []) {
  const sectors = sectorLibraryForDirection(direction);
  const selectedSet = new Set(selected.length ? selected : defaultSectorsForDirection(direction));
  $("#monitor-sector-title").textContent = direction === "procurement"
    ? "监控主题"
    : direction === "competitor"
      ? "竞品产品/应用方向"
      : direction === "social"
        ? "社媒监控主题"
      : direction === "environmental"
        ? "含氟废水候选行业"
        : direction === "upstream"
          ? "可能副产液钙的行业"
          : "下游行业";
  $("#monitor-sector-list").innerHTML = Object.entries(sectors)
    .map(([id, item]) => `
      <label>
        <input type="checkbox" name="monitorSector" value="${escapeHtml(id)}" ${selectedSet.has(id) ? "checked" : ""} />
        ${escapeHtml(item.name)}
      </label>
    `).join("");
}

function updateMonitorSummary() {
  const direction = $("#monitor-direction").value;
  const regions = monitorSelectedValues("monitorRegionPreset");
  const customRegions = $("#monitor-regions").value.trim();
  const sectorCount = monitorSelectedValues("monitorSector").length;
  const customKeywordCount = $("#monitor-custom-keywords").value
    .split(/[,，、;\s]+/)
    .filter(Boolean).length;
  const expandedRegions = regions.flatMap((region) => state.regionPresets[region] || [REGION_LABELS[region] || region]);
  const platformCount = direction === "social" ? selectedValues("socialPlatform").length : 0;
  $("#monitor-summary").textContent = [
    `类型：${DIRECTION_LABELS[direction] || "下游买家"}`,
    `地区：${expandedRegions.join("、") || "未选择"}${customRegions ? `；自定义：${customRegions}` : ""}`,
    `行业/主题：${sectorCount} 项${customKeywordCount ? `；自定义关键词：${customKeywordCount} 个` : ""}`,
    platformCount ? `平台：${platformCount} 个` : "",
  ].filter(Boolean).join("；");
}

function buildMonitorPayload() {
  const base = buildPayload();
  const regions = monitorSelectedValues("monitorRegionPreset");
  const customRegions = $("#monitor-regions").value.trim();
  if (customRegions) {
    regions.push(...customRegions.split(/[,，、;\s]+/).filter(Boolean));
  }
  return {
    ...base,
    direction: $("#monitor-direction").value,
    regions,
    sectors: monitorSelectedValues("monitorSector"),
    customKeywords: $("#monitor-custom-keywords").value.trim(),
  };
}

function setDirection(direction) {
  state.direction = ["upstream", "procurement", "environmental", "competitor", "social"].includes(direction) ? direction : "downstream";
  const upstream = state.direction === "upstream";
  const procurement = state.direction === "procurement";
  const environmental = state.direction === "environmental";
  const competitor = state.direction === "competitor";
  const social = state.direction === "social";
  renderSectors();
  $("#sector-title").textContent = social
    ? "社媒监控主题"
    : competitor
    ? "竞品产品/应用方向"
    : upstream
    ? "可能副产液钙的行业"
    : environmental
      ? "含氟废水候选行业"
    : procurement
      ? "监控主题"
      : "下游行业";
  $("#direction-note").textContent = social
    ? "选择平台、地区和业务主题即可直接检索；公开链接只是可选补充。"
    : competitor
    ? "检索同行官网和公开平台供应商，整理其重点行业、地区、应用词和公开证据，再反向开发同类客户。"
    : upstream
    ? "查找生产过程中可能形成液体氯化钙的企业；结果属于工艺线索，需要进一步核实。"
    : environmental
      ? "交叉检索排污许可、环评验收、自行监测和处罚整改，只保留有具体企业及含氟废水证据的结果。"
    : procurement
      ? "自动聚合全国、中央及已接入的省级官方采购平台，也可检查企业官网。"
      : "查找可能采购氯化钙的下游企业。";
  $("#custom-keywords").placeholder = social
    ? "例如：液钙求购, 副产液钙处置, 含氟废水, 除氟改造"
    : competitor
    ? "例如：液钙槽车, 山东融雪剂, 食品级氯化钙, 出口"
    : upstream
    ? "例如：副产盐酸, 石灰中和, 湿法冶炼, 飞灰水洗"
    : environmental
      ? "例如：氟化工, 电镀, 光伏, 磷肥, 铝业"
    : procurement
      ? "例如：氯化钙框架采购, 液体氯化钙处置"
      : "例如：融雪剂厂家, 集装箱干燥剂, 钻井液";
  $("#exclude-suppliers-wrap").hidden = !upstream;
  $("#strict-upstream-wrap").hidden = !upstream;
  $("#pages-wrap").hidden = procurement || competitor || social;
  $("#only-phone-wrap").hidden = procurement || environmental || competitor || social;
  $("#fast-mode-wrap").hidden = procurement || environmental || competitor || social;
  $("#procurement-options").hidden = !procurement;
  $("#environmental-options").hidden = !environmental;
  $("#competitor-options").hidden = !competitor;
  $("#social-options").hidden = !social;
  $("#api-status").hidden = procurement || environmental || competitor || social;
  $("#only-phone").checked = !upstream && !procurement && !environmental && !competitor && !social;
  $("#result-title").textContent = social
    ? "社媒公开线索"
    : competitor
    ? "竞品/同行客户挖掘"
    : upstream
    ? "液体氯化钙副产企业线索"
    : environmental
      ? "含氟废水企业雷达"
    : procurement
      ? "招投标/采购信息监控"
      : "潜在买家列表";
  $("#reason-heading").textContent = social ? "命中依据" : competitor ? "同行公开证据" : upstream ? "工艺匹配依据" : environmental ? "含氟证据依据" : procurement ? "监控规则" : "匹配原因";
  $("#pitch-heading").textContent = social ? "核验建议" : competitor ? "反向开发建议" : upstream ? "核实重点" : environmental ? "处理核实重点" : procurement ? "跟进重点" : "跟进话术";
  $("th:nth-child(3)").textContent = social ? "账号/内容" : competitor ? "同行供应商" : procurement ? "采购单位/项目" : environmental ? "含氟企业" : "公司/任务";
  $("th:nth-child(4)").textContent = social ? "主题" : competitor ? "重点产品" : procurement ? "公告类型" : "行业";
  $("th:nth-child(6)").textContent = social ? "平台/证据" : competitor ? "证据数量" : environmental ? "证据编号/类型" : "电话";
  $("#collect-button-label").textContent = social
    ? "开始公开索引检索"
    : competitor
    ? "采集同行供应商并生成画像"
    : upstream
    ? "采集液钙副产企业"
    : environmental
      ? "扫描含氟废水企业"
    : procurement
      ? "采集采购单位和项目信息"
      : "采集具体公司和电话";
  $("#task-button").hidden = procurement || environmental || competitor || social;
  $("#quick-filters").innerHTML = social
    ? `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="求购/采购">求购</button>
      <button type="button" data-filter="副产出售/处置">副产处置</button>
      <button type="button" data-filter="含氟废水处理需求">含氟需求</button>
      <button type="button" data-filter="抖音">抖音</button>
      <button type="button" data-filter="快手">快手</button>
      <button type="button" data-filter="小红书">小红书</button>
      <button type="button" data-filter="哔哩哔哩">B站</button>
      <button type="button" data-filter="微博">微博</button>
      <button type="button" data-filter="微信公众号">微信</button>
      <button type="button" data-filter="今日头条">头条</button>
      <button type="button" data-filter="知乎">知乎</button>
    `
    : competitor
    ? `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="液体氯化钙">液钙</button>
      <button type="button" data-filter="融雪">融雪</button>
      <button type="button" data-filter="干燥剂">干燥剂</button>
      <button type="button" data-filter="水处理">水处理</button>
      <button type="button" data-filter="同行企业官网">已定位官网</button>
    `
    : upstream
    ? `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="高">高相关</button>
      <button type="button" data-filter="稀土">稀土</button>
      <button type="button" data-filter="环氧氯丙烷">环氧氯丙烷</button>
      <button type="button" data-filter="飞灰">飞灰水洗</button>
      <button type="button" data-filter="钨">钨业</button>
    `
    : environmental
      ? `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="官方许可/废水含氟">废水含氟</button>
      <button type="button" data-filter="稀土">稀土</button>
      <button type="button" data-filter="氟化工">氟化工</button>
      <button type="button" data-filter="电镀">电镀</button>
      <button type="button" data-filter="电子">电子</button>
      <button type="button" data-filter="重点管理">重点管理</button>
    `
    : procurement
      ? `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="氯化钙">氯化钙</button>
      <button type="button" data-filter="液体氯化钙">液体氯化钙</button>
      <button type="button" data-filter="融雪">融雪</button>
      <button type="button" data-filter="中标">中标结果</button>
      <button type="button" data-filter="副产">副产处置</button>
    `
      : `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="融雪">融雪</button>
      <button type="button" data-filter="干燥剂">干燥剂</button>
      <button type="button" data-filter="水处理">水处理</button>
      <button type="button" data-filter="化工">化工贸易</button>
    `;
  state.leads = [];
  state.filtered = [];
  state.meta = {};
  $("#filter").value = "";
  $("#progress-panel").hidden = true;
  $("#export-button").disabled = true;
  $("#lead-body").innerHTML = `<tr class="empty-row"><td colspan="9">选择地区和行业后开始采集。</td></tr>`;
  renderMetrics([]);
  renderQualitySummary([]);
  setNotice(
    social
      ? "无需粘贴链接，选择地区、主题和平台后即可开始公开索引检索。"
      : competitor
      ? "选择地区、产品方向和公开来源后，系统会生成同行画像及反向开发建议。"
      : upstream
      ? "当前为上游副产液钙企业采集模式。"
      : environmental
        ? "选择地区、行业和证据来源后，交叉扫描含氟废水企业。"
      : procurement
        ? "选择监控主题、公告类型和时间范围后采集真实采购单位。"
        : "当前为下游买家采集模式。",
  );
  restoreSearchDraft(state.direction) || applyCollectionStrategy();
  replayMotion($(".controls"), "direction-refresh");
}

async function runSearch(mode = "amap") {
  const button = $("#lead-form button[type='submit']");
  const payload = buildPayload();
  if (state.direction === "procurement" && !payload.procurementSources.length) {
    setNotice("请至少选择一个采集来源。", true);
    return;
  }
  if (state.direction === "environmental" && !payload.environmentalSources.length) {
    setNotice("请至少选择一个环保证据来源。", true);
    return;
  }
  if (state.direction === "competitor" && !payload.competitorSources.length) {
    setNotice("请至少选择一个竞品信息来源。", true);
    return;
  }
  if (state.direction === "social" && !payload.socialPlatforms.length && !payload.socialLinks.trim()) {
    setNotice("请至少选择一个社媒平台，或粘贴一个公开链接。", true);
    return;
  }
  if (
    mode === "amap"
    && !["procurement", "environmental", "competitor", "social"].includes(state.direction)
    && !state.hasEnvAmapKey
    && !state.hasEnvBaiduMapAk
    && !state.hasEnvTiandituTk
  ) {
    setNotice("企业地图采集尚未配置。请添加 AMAP_KEY、BAIDU_MAP_AK 或 TIANDITU_TK。", true);
    return;
  }
  saveSearchDraft(payload);
  button.disabled = true;
  button.textContent = "正在采集...";
  const scope = mode === "amap" && payload.fastMode ? "快速模式" : "全面模式";
  setNotice(
    state.direction === "social"
      ? "正在识别导入链接，并按平台检索公开索引。"
      : state.direction === "competitor"
      ? "正在聚合同行官网和平台公开页面，并分析行业、地区与关键词布局。"
      : state.direction === "procurement"
      ? payload.procurementSources.includes("company_website")
        ? "正在聚合官方公告，并检查目标企业官网采购栏目。"
        : "正在聚合多个官方平台的采购单位和公告详情。"
      : state.direction === "environmental"
      ? "正在交叉检索排污许可、环评验收、自行监测和处罚整改证据。"
      : mode === "amap"
        ? `正在采集具体公司（${scope}）。`
        : "正在生成开发任务。",
  );
  if (["procurement", "environmental", "competitor", "social"].includes(state.direction)) {
    payload.amapKey = "";
    payload.baiduMapAk = "";
    payload.tiandituTk = "";
    payload.requireMap = false;
  } else if (mode === "task") {
    payload.amapKey = "";
    payload.baiduMapAk = "";
    payload.tiandituTk = "";
    payload.requireMap = false;
    payload.disableAmap = true;
    payload.disableBaiduMap = true;
    payload.disableTianditu = true;
  } else {
    payload.requireMap = true;
  }

  try {
    showProgress(
      mode === "amap"
        ? state.direction === "social"
          ? "正在扫描社媒公开线索"
          : state.direction === "competitor"
          ? "正在构建同行画像"
          : state.direction === "procurement"
          ? "正在采集采购单位"
          : state.direction === "environmental"
            ? "正在扫描含氟废水企业"
          : state.direction === "upstream"
            ? "正在采集副产液钙企业"
            : "正在采集买家企业"
        : "正在生成开发任务",
    );
    const response = await fetch("/api/search/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (response.status === 401) {
      window.location.href = "/login?v=pnvs-login-1";
      return;
    }
    if (!response.ok) throw new Error("无法启动采集任务");
    const started = await response.json();
    state.activeJobId = started.jobId;
    const data = await pollSearchJob(started.jobId);
    applySearchResult(data);
  } catch (error) {
    setNotice(error.message || "采集失败", true);
    $("#progress-title").textContent = "采集失败";
  } finally {
    button.disabled = false;
    button.innerHTML = `<span id="collect-button-label">${
      state.direction === "procurement"
        ? "采集采购单位和项目信息"
        : state.direction === "social"
          ? "开始公开索引检索"
        : state.direction === "environmental"
          ? "扫描含氟废水企业"
        : state.direction === "competitor"
          ? "采集同行供应商并生成画像"
        : state.direction === "upstream"
          ? "采集液钙副产企业"
          : "采集具体公司和电话"
    }</span>`;
    state.activeJobId = "";
  }
}

function showProgress(title) {
  $("#progress-panel").hidden = false;
  $("#progress-title").textContent = title;
  replayMotion($("#progress-panel"), "surface-enter");
  updateProgress({
    completed: 0,
    total: 0,
    percent: 0,
    companyCount: 0,
    phoneCount: 0,
    current: "正在准备采集任务",
  });
}

function updateProgress(job) {
  $("#progress-percent").textContent = `${job.percent || 0}%`;
  $("#progress-bar").style.width = `${job.percent || 0}%`;
  $("#progress-current").textContent = job.current || "正在采集";
  $("#progress-requests").textContent = `${job.completed || 0} / ${job.total || 0}`;
  $("#progress-companies").textContent = job.companyCount || 0;
  $("#progress-phones").textContent = job.phoneCount || 0;
  const procurement = state.direction === "procurement";
  const environmental = state.direction === "environmental";
  const competitor = state.direction === "competitor";
  const social = state.direction === "social";
  $("#progress-requests-label").textContent = procurement || environmental || competitor || social ? "查询" : "请求";
  $("#progress-companies-label").textContent = social ? "已识别账号" : competitor ? "同行供应商" : procurement ? "采购单位" : environmental ? "含氟企业" : "企业";
  $("#progress-phones-label").textContent = competitor ? "已定位官网" : environmental ? "证据记录" : "有电话";
}

async function pollSearchJob(jobId) {
  const startedAt = Date.now();
  while (state.activeJobId === jobId) {
    const response = await fetch(`/api/search/status?id=${encodeURIComponent(jobId)}`);
    if (response.status === 401) {
      window.location.href = "/login?v=pnvs-login-1";
      throw new Error("登录已过期");
    }
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "读取采集进度失败");
    updateProgress(job);
    if (job.status === "completed") {
      $("#progress-title").textContent = "采集完成";
      return job.result;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "采集失败");
    }
    const elapsed = Date.now() - startedAt;
    const delay = elapsed > 60000 ? 2500 : elapsed > 20000 ? 1500 : 800;
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
  throw new Error("采集任务已停止");
}

function applySearchResult(data) {
  state.leads = data.leads || [];
  state.meta = data.meta || {};
  if (data.persistence?.total) state.leadStoreLoadedAt = 0;
  renderLeads();
  $("#export-button").disabled = !state.leads.length;
  const warnings = data.errors?.length ? ` ${data.errors[0]}` : "";
  const realCount = data.meta?.companyCount || 0;
  const phoneCount = data.meta?.phoneCount || 0;
  const summary = ["amap", "baidu", "tianditu", "maps"].includes(data.meta?.mode)
    ? data.meta?.direction === "upstream"
      ? `已通过${(data.meta?.mapSources || []).join("、") || "地图服务"}发现 ${realCount} 家可能副产液体氯化钙的企业，其中 ${phoneCount} 家有电话；请按相关度核实工艺。`
      : `已通过${(data.meta?.mapSources || []).join("、") || "地图服务"}采集 ${realCount} 家具体公司，其中 ${phoneCount} 家有电话；完成 ${data.meta?.requestCount || 0} 次查询。`
    : data.meta?.mode === "social"
      ? `已发现 ${state.leads.length} 条社媒公开内容，识别 ${realCount} 个企业账号，${phoneCount} 条含公开电话；覆盖 ${data.meta?.platformCount || 0} 个平台。`
    : data.meta?.mode === "competitor"
      ? `已整理 ${realCount} 家同行供应商，定位 ${phoneCount} 个企业官网；已生成重点行业、地区、关键词和反向开发建议。`
    : data.meta?.mode === "environmental"
      ? `已发现 ${realCount} 家含氟废水企业；证据来源：${(data.meta?.environmentalSources || []).map((source) => ({
        permit: "排污许可",
        eia: "环评审批",
        acceptance: "竣工验收",
        monitoring: "自行监测",
        enforcement: "处罚整改",
        company_website: "企业官网",
      })[source] || source).join("、")}。`
    : data.meta?.mode === "procurement"
      ? `已采集 ${state.leads.length} 条招采公告，其中 ${phoneCount} 条含采购单位电话；来源：${(data.meta?.procurementSources || []).map((source) => ({
        ggzy: "全国公共资源",
        ccgp: "中国政府采购网",
        zycg: "中央政府采购网",
        shandong: "山东省平台",
        sichuan: "四川省平台",
        company_website: "企业官网",
      })[source] || source).join("、") || "官方平台"}。`
      : data.meta?.mode === "need_key"
      ? "未开始采集。"
      : `已生成 ${state.leads.length} 条开发任务。`;
  const persistence = data.persistence?.total
    ? ` 已存入线索库：新增 ${data.persistence.created || 0} 条，更新 ${data.persistence.updated || 0} 条。`
    : "";
  const quality = data.meta?.qualitySummary || {};
  const qualityText = state.leads.length
    ? ` 其中 A/B 级 ${Number(quality.gradeA || 0) + Number(quality.gradeB || 0)} 条，可立即跟进 ${quality.actionable || 0} 条。`
    : "";
  setNotice(`${summary}${qualityText}${persistence}${warnings}`, Boolean(data.errors?.length && !state.leads.length));
  loadDashboard().catch(() => {});
}

function detailItem(label, value, isLink = false) {
  if (!value) return "";
  const content = isLink
    ? `<a href="${escapeHtml(value)}" target="_blank" rel="noreferrer">${escapeHtml(value)}</a>`
    : escapeHtml(value);
  return `<div><dt>${escapeHtml(label)}</dt><dd>${content}</dd></div>`;
}

function detailPhoneItem(value) {
  if (!value) return detailItem("联系电话", "待补充");
  const phones = splitPhones(value);
  const content = phones.length
    ? phones.map((phone) => {
      const href = telHref(phone);
      return href
        ? `<a class="phone-link" href="${escapeHtml(href)}">${escapeHtml(phone)}</a>`
        : `<span>${escapeHtml(phone)}</span>`;
    }).join(" ")
    : escapeHtml(value);
  return `<div class="detail-highlight"><dt>联系电话</dt><dd>${content}</dd></div>`;
}

function detailScoreItem(lead) {
  const scoreDetails = Object.entries(lead.score_details || {})
    .map(([key, value]) => `${key} ${value}`)
    .join(" · ");
  return detailItem("智能评分", `${lead.score || 0} 分${scoreDetails ? `（${scoreDetails}）` : ""}`);
}

function showCompanyDetail(lead) {
  state.currentLead = lead;
  const procurement = lead.direction === "procurement";
  const environmental = lead.direction === "environmental";
  const competitor = lead.direction === "competitor";
  const social = lead.direction === "social";
  const companyWebsiteNotice = lead.source?.includes("企业官网");
  $("#detail-company").textContent = lead.company || (procurement ? "采购单位详情" : "企业详情");
  $("#detail-grid").innerHTML = [
    detailScoreItem(lead),
    detailItem("质量等级", lead.quality_label),
    detailItem("质量依据", lead.quality_reasons),
    detailItem("待补信息", lead.quality_issues),
    detailItem("建议下一步", lead.recommended_action),
    detailPhoneItem(lead.phone),
    detailItem("采购项目", procurement ? lead.project_title : ""),
    detailItem("社媒平台", social ? lead.social_platform : ""),
    detailItem("公开账号", social ? lead.social_account : ""),
    detailItem("内容标题", social ? lead.project_title : ""),
    detailItem("内容类型", social ? lead.social_content_type : ""),
    detailItem("发现方式", social ? lead.social_discovery_method : ""),
    detailItem("意向分类", social ? lead.social_intent : ""),
    detailItem("意向强度", social ? `${lead.social_intent_score || 0} 分` : ""),
    detailItem("意向依据", social ? lead.social_intent_reasons : ""),
    detailItem("正向命中", social ? lead.social_positive_hits : ""),
    detailItem("企业主体状态", social ? lead.social_entity_status : ""),
    detailItem("企业主体候选", social ? lead.social_entity_candidate : ""),
    detailItem("人工反馈", social ? lead.feedback_label : ""),
    detailItem("命中关键词", social ? lead.social_matched_keywords : ""),
    detailItem("公开互动信息", social ? lead.social_engagement : ""),
    detailItem("公告类型", procurement ? lead.sector : ""),
    detailItem("公告日期", procurement ? lead.notice_date : ""),
    detailItem("企业别名", lead.alias),
    detailItem("项目联系人", procurement ? lead.contact_name : ""),
    detailItem("采购代理机构", procurement ? lead.agency : ""),
    detailItem("公开邮箱", lead.email),
    detailItem("企业官网", lead.company_website, true),
    detailItem("官网/证据页", lead.website && lead.website !== lead.company_website ? lead.website : "", true),
    detailItem("所属地区", lead.region),
    detailItem("详细地址", lead.address),
    detailItem("公司/项目名称", lead.company),
    detailItem("匹配原因", lead.match_reason),
    detailItem(social ? "索引类型" : competitor ? "情报类型" : procurement ? "公告来源平台" : environmental ? "环保文件类型/许可行业" : "地图行业类型", lead.raw_type),
    detailItem(
      social ? "公开内容依据" : competitor ? "同行公开证据" : lead.direction === "upstream" ? "副产工艺依据" : environmental ? "含氟证据依据" : procurement ? "数据依据" : "潜在用途",
      lead.process_basis || lead.use_case,
    ),
    detailItem(
      "线索置信度",
      lead.confidence ? `${lead.confidence}${lead.confidence.startsWith("官方") ? "" : "相关"}` : "",
    ),
    detailItem("预算金额（元）", procurement ? lead.budget : ""),
    detailItem("投标/响应截止", procurement ? lead.deadline : ""),
    detailItem(competitor ? "反向开发建议" : lead.direction === "upstream" ? "建议核实内容" : environmental ? "废水处理核实重点" : "销售跟进重点", lead.pitch),
    detailItem("重点服务行业", competitor ? lead.competitor_industries : ""),
    detailItem("重点地区布局", competitor ? lead.competitor_regions : ""),
    detailItem("产品/投放关键词", competitor ? lead.competitor_keywords : ""),
    detailItem("公开渠道", competitor ? lead.competitor_channels : ""),
    detailItem("公开证据数量", competitor ? lead.evidence_count : ""),
    detailItem("地图坐标", lead.location),
    detailItem(environmental ? "证据编号/类型" : "地图 POI ID", lead.poi_id),
    detailItem(environmental ? "发证日期" : "数据更新时间", lead.updated_at),
    detailItem("数据来源", lead.source),
    detailItem("商机角色", ({
      buyer: "液钙买家",
      supplier: "液钙货源",
      prospect: "工艺候选",
    })[lead.opportunity_role] || lead.opportunity_role),
    detailItem("液钙浓度", lead.liquid_concentration),
    detailItem("月供/用量", lead.monthly_volume),
    detailItem("杂质与质量指标", lead.impurity_profile),
    detailItem("运输半径", lead.logistics_radius),
    detailItem("储运条件", lead.storage_condition),
    detailItem("预估商业价值", lead.commercial_value),
  ].join("") || "<p>暂无更多公开信息。</p>";
  $("#detail-actions").innerHTML = [
    lead.company ? `<button type="button" data-copy-company="${escapeHtml(lead.company)}">复制公司名</button>` : "",
    lead.phone ? `<button type="button" data-copy-phone="${escapeHtml(lead.phone)}">复制电话</button>` : "",
    competitor ? `<button type="button" data-reverse-current="1">反向开发同类客户</button>` : "",
    lead.search_url ? `<a href="${escapeHtml(lead.search_url)}" target="_blank" rel="noreferrer">${social ? "打开社媒原内容" : competitor ? "来源检索" : procurement ? (companyWebsiteNotice ? "官网公告" : "公告正文") : environmental ? "环保证据原文" : "地图查看"}</a>` : "",
    lead.company_website && (companyWebsiteNotice || competitor) ? `<a href="${escapeHtml(lead.company_website)}" target="_blank" rel="noreferrer">企业官网</a>` : "",
    lead.qcc_url ? `<a href="${escapeHtml(lead.qcc_url)}" target="_blank" rel="noreferrer">工商信息核验</a>` : "",
    lead.website && lead.website !== lead.search_url ? `<a href="${escapeHtml(lead.website)}" target="_blank" rel="noreferrer">${procurement ? "公告页面" : "网页搜索"}</a>` : "",
  ].join("");
  const socialReview = $("#social-review-panel");
  socialReview.hidden = !social || !lead.id;
  if (social && lead.id) {
    $("#social-feedback-state").textContent = lead.feedback_label
      ? `已标记：${lead.feedback_label}`
      : "尚未反馈";
    $$("#social-review-panel [data-social-feedback]").forEach((button) => {
      button.classList.toggle("active", button.dataset.socialFeedback === lead.feedback_status);
    });
    $("#social-entity-candidate").value = lead.confirmed_company
      || lead.social_entity_candidate
      || (lead.company === "待识别账号" ? "" : lead.company)
      || "";
    $("#social-entity-confirm").textContent = lead.social_entity_status === "已确认"
      ? "更新企业主体"
      : "确认并进入公司档案";
  }
  const salesForm = $("#sales-form");
  salesForm.hidden = !lead.id;
  if (lead.id) {
    $("#sales-lead-id").value = lead.id;
    $("#sales-status").value = lead.sales_status || "new";
    $("#sales-owner").value = lead.owner || "";
    $("#opportunity-role").value = lead.opportunity_role || "";
    $("#sales-follow-up").value = (lead.next_follow_up || "").slice(0, 16);
    $("#sales-notes").value = lead.notes || "";
    $("#liquid-concentration").value = lead.liquid_concentration || "";
    $("#monthly-volume").value = lead.monthly_volume || "";
    $("#impurity-profile").value = lead.impurity_profile || "";
    $("#logistics-radius").value = lead.logistics_radius || "";
    $("#storage-condition").value = lead.storage_condition || "";
    $("#commercial-value").value = lead.commercial_value || "";
  }
  showDialogSmooth($("#company-dialog"));
}

function replaceLeadEverywhere(updated) {
  if (!updated?.id) return;
  ["leads", "savedLeads", "profiles", "filtered", "profileFiltered"].forEach((key) => {
    state[key] = (state[key] || []).map((lead) => Number(lead.id) === Number(updated.id) ? updated : lead);
  });
  state.currentLead = updated;
}

async function submitSocialFeedback(status) {
  const lead = state.currentLead;
  if (!lead?.id) {
    setNotice("该结果尚未写入线索库，请稍后重试。", true);
    return;
  }
  const data = await fetchJson("/api/leads/social-feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: Number(lead.id), status }),
  });
  replaceLeadEverywhere(data.lead);
  renderCurrentResults();
  showCompanyDetail(data.lead);
  setNotice(`已标记为“${SOCIAL_FEEDBACK_LABELS[status]}”，后续相同公开内容会参考本次反馈。`);
}

async function confirmSocialEntityFromDetail() {
  const lead = state.currentLead;
  const company = $("#social-entity-candidate").value.trim();
  if (!lead?.id) {
    setNotice("该结果尚未写入线索库，请稍后重试。", true);
    return;
  }
  if (!company) {
    setNotice("请填写企业主体名称。", true);
    return;
  }
  const button = $("#social-entity-confirm");
  button.disabled = true;
  try {
    const data = await fetchJson("/api/leads/social-entity", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: Number(lead.id), company }),
    });
    replaceLeadEverywhere(data.lead);
    renderCurrentResults();
    showCompanyDetail(data.lead);
    await loadDashboard();
    setNotice(`已确认企业主体“${company}”，并更新到公司档案。`);
  } finally {
    button.disabled = false;
  }
}

function reverseDevelop(lead) {
  if (!lead) return;
  const industryText = lead.competitor_industries || lead.use_case || "";
  const keywordText = lead.competitor_keywords || "";
  const sectorMap = {
    snow: ["融雪", "除冰", "道路"],
    desiccant: ["干燥", "吸湿", "防潮"],
    water: ["水处理", "污水"],
    concrete: ["混凝土", "建材", "砂浆"],
    oilfield: ["油田", "钻井", "完井"],
    coldchain: ["制冷", "冷库", "冷链"],
    trader: ["贸易", "经销", "供应链"],
  };
  const downstreamRadio = document.querySelector('input[name="direction"][value="downstream"]');
  downstreamRadio.checked = true;
  setDirection("downstream");
  $$('input[name="sector"]').forEach((input) => {
    input.checked = (sectorMap[input.value] || []).some((word) => industryText.includes(word));
  });
  if (!selectedValues("sector").length) {
    ["snow", "desiccant", "water", "trader"].forEach((id) => {
      const input = document.querySelector(`input[name="sector"][value="${id}"]`);
      if (input) input.checked = true;
    });
  }
  $("#regions").value = lead.competitor_regions || lead.region || "";
  $("#custom-keywords").value = keywordText
    .split("、")
    .filter(Boolean)
    .slice(0, 8)
    .join(", ");
  closeDialogSmooth($("#company-dialog"));
  setNotice(`已根据“${lead.company}”生成反向开发条件，可调整后开始采集同类客户。`);
  $(".sidebar").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveSalesRecord(event) {
  event.preventDefault();
  const button = $("#sales-form button[type='submit']");
  button.disabled = true;
  try {
    await fetchJson("/api/leads/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: Number($("#sales-lead-id").value),
        salesStatus: $("#sales-status").value,
        owner: $("#sales-owner").value,
        opportunityRole: $("#opportunity-role").value,
        nextFollowUp: $("#sales-follow-up").value,
        notes: $("#sales-notes").value,
        liquidConcentration: $("#liquid-concentration").value,
        monthlyVolume: $("#monthly-volume").value,
        impurityProfile: $("#impurity-profile").value,
        logisticsRadius: $("#logistics-radius").value,
        storageCondition: $("#storage-condition").value,
        commercialValue: $("#commercial-value").value,
      }),
    });
    await closeDialogSmooth($("#company-dialog"));
    await Promise.all([
      state.view === "profiles" ? loadProfiles(true) : loadSavedLeads(true),
      loadDashboard(),
    ]);
    setNotice("跟进记录已保存。");
  } catch (error) {
    setNotice(error.message || "保存失败", true);
  } finally {
    button.disabled = false;
  }
}

function openMonitorDialog() {
  const payload = buildPayload();
  $("#monitor-name").value = `${DIRECTION_LABELS[state.direction]}监控`;
  $("#monitor-direction").value = state.direction;
  renderMonitorRegions(payload.regions.filter((region) => REGION_LABELS[region]));
  $("#monitor-regions").value = $("#regions").value.trim();
  renderMonitorSectors(state.direction, payload.sectors);
  $("#monitor-custom-keywords").value = payload.customKeywords || "";
  updateMonitorSummary();
  showDialogSmooth($("#monitor-dialog"));
}

async function saveMonitor(event) {
  event.preventDefault();
  const button = $("#monitor-form button[type='submit']");
  button.disabled = true;
  try {
    await fetchJson("/api/monitors/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("#monitor-name").value,
        intervalHours: Number($("#monitor-interval").value),
        payload: buildMonitorPayload(),
      }),
    });
    await closeDialogSmooth($("#monitor-dialog"));
    setNotice("自动监控已启用，系统将按设定频率复查并只提醒新增线索。");
    await loadDashboard();
  } catch (error) {
    setNotice(error.message || "保存监控失败", true);
  } finally {
    button.disabled = false;
  }
}

async function waitForMonitorCompletion(id) {
  if (state.runningMonitorPolls.has(id)) return;
  state.runningMonitorPolls.add(id);
  const deadline = Date.now() + 180000;
  try {
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2500));
      await loadAlerts();
      const monitor = state.monitors.find((item) => Number(item.id) === id);
      if (!monitor?.running) {
        state.leadStoreLoadedAt = 0;
        await loadDashboard();
        return;
      }
    }
    setNotice("监控仍在后台运行，可稍后在本页查看结果。");
  } finally {
    state.runningMonitorPolls.delete(id);
  }
}

async function monitorAction(event) {
  const runButton = event.target.closest("[data-monitor-run]");
  const toggleButton = event.target.closest("[data-monitor-toggle]");
  const deleteButton = event.target.closest("[data-monitor-delete]");
  if (!runButton && !toggleButton && !deleteButton) return;
  if (runButton) {
    const monitorId = Number(runButton.dataset.monitorRun);
    await fetchJson("/api/monitors/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: monitorId }),
    });
    runButton.textContent = "执行中";
    runButton.disabled = true;
    await loadAlerts();
    waitForMonitorCompletion(monitorId)
      .catch((error) => setNotice(error.message || "监控状态更新失败", true));
  } else if (toggleButton) {
    await fetchJson("/api/monitors/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: Number(toggleButton.dataset.monitorToggle),
        enabled: toggleButton.dataset.enabled === "1",
      }),
    });
    await loadAlerts();
  } else {
    await fetchJson("/api/monitors/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: Number(deleteButton.dataset.monitorDelete) }),
    });
    await loadAlerts();
  }
}

async function markNotificationsRead(id = 0) {
  await fetchJson("/api/notifications/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  await Promise.all([loadAlerts(), loadDashboard()]);
}

async function openNotification(item) {
  const notificationId = Number(item.dataset.notificationId || 0);
  const leadId = Number(item.dataset.leadId || 0);
  if (!leadId) {
    await markNotificationsRead(notificationId);
    return;
  }
  try {
    const data = await fetchJson(`/api/leads/detail?id=${encodeURIComponent(leadId)}`);
    await fetchJson("/api/notifications/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: notificationId }),
    });
    item.classList.add("read");
    showCompanyDetail(data.lead);
    await Promise.all([loadAlerts(), loadDashboard()]);
  } catch (error) {
    setNotice(error.message || "线索详情加载失败", true);
  }
}

function updateBulkToolbar() {
  const count = state.selectedLeadIds.size;
  $("#selected-count").textContent = count;
  $("#bulk-toolbar").hidden = state.view !== "database" || !count;
  const visibleIds = state.pageItems.map((lead) => Number(lead.id)).filter(Boolean);
  $("#select-all-leads").checked = Boolean(
    visibleIds.length && visibleIds.every((id) => state.selectedLeadIds.has(id)),
  );
}

async function applyBulkUpdate() {
  const ids = [...state.selectedLeadIds];
  const payload = { ids };
  if ($("#bulk-status").value) payload.salesStatus = $("#bulk-status").value;
  if ($("#bulk-owner").value.trim()) payload.owner = $("#bulk-owner").value.trim();
  if ($("#bulk-follow-up").value) payload.nextFollowUp = $("#bulk-follow-up").value;
  if (Object.keys(payload).length === 1) {
    setNotice("请选择批量状态、负责人或跟进时间。", true);
    return;
  }
  const result = await fetchJson("/api/leads/bulk-update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.selectedLeadIds.clear();
  $("#bulk-status").value = "";
  $("#bulk-owner").value = "";
  $("#bulk-follow-up").value = "";
  await Promise.all([loadSavedLeads(true), loadDashboard()]);
  setNotice(`已批量更新 ${result.updated || 0} 条线索。`);
}

async function resolveSystemEvent(id = 0) {
  await fetchJson("/api/system/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  await Promise.all([loadSystem(), loadDashboard()]);
}

async function downloadBackup() {
  const response = await fetch("/api/backup");
  if (!response.ok) {
    setNotice("数据库备份失败。", true);
    return;
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/);
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = match?.[1] || "liquid-calcium-backup.db";
  document.body.appendChild(anchor);
  anchor.click();
  URL.revokeObjectURL(anchor.href);
  anchor.remove();
}

async function exportCsv() {
  const leads = state.view === "profiles"
    ? state.profileFiltered
    : state.view === "database"
    ? state.filtered
    : state.filtered.length
      ? state.filtered
      : state.leads;
  if (!leads.length) {
    setNotice("当前没有可导出的线索。", true);
    return;
  }
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ leads }),
  });
  if (!response.ok) {
    setNotice("导出失败", true);
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "calcium-chloride-leads.csv";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function logout() {
  await fetch("/api/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  window.location.href = "/login?v=pnvs-login-1";
}

function scheduleFilterRender() {
  resetCurrentPage();
  window.clearTimeout(filterRenderTimer);
  filterRenderTimer = window.setTimeout(renderCurrentResults, 120);
}

function bindEvents() {
  $("#lead-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch("amap");
  });

  $("#task-button").addEventListener("click", () => runSearch("task"));
  $("#save-monitor-button").addEventListener("click", openMonitorDialog);
  $("#export-button").addEventListener("click", exportCsv);
  $("#logout-button").addEventListener("click", logout);
  $("#filter").addEventListener("input", scheduleFilterRender);
  $("#only-phone").addEventListener("change", scheduleFilterRender);
  $("#quality-filter").addEventListener("change", scheduleFilterRender);
  $("#status-filter").addEventListener("change", scheduleFilterRender);
  $("#direction-filter").addEventListener("change", scheduleFilterRender);
  $("#work-queue-filter").addEventListener("change", scheduleFilterRender);
  $("#page-size").addEventListener("change", (event) => {
    state.pageSize = Number(event.target.value) || 50;
    state.pages.database = 1;
    state.pages.profiles = 1;
    renderCurrentResults();
  });
  $("#page-prev").addEventListener("click", () => {
    const key = currentPageKey();
    state.pages[key] = Math.max(1, state.pages[key] - 1);
    renderCurrentResults();
    $("#pagination").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  $("#page-next").addEventListener("click", () => {
    const key = currentPageKey();
    state.pages[key] += 1;
    renderCurrentResults();
    $("#pagination").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  $$(".workspace-tabs button").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $$("input[name='collectionStrategy']").forEach((input) => {
    input.addEventListener("change", () => applyCollectionStrategy(input.value));
  });
  $$('input[name="direction"]').forEach((input) => {
    input.addEventListener("change", () => setDirection(input.value));
  });
  $("#lead-body").addEventListener("click", (event) => {
    const reverseButton = event.target.closest("[data-reverse-index]");
    if (reverseButton) {
      reverseDevelop(state.filtered[Number(reverseButton.dataset.reverseIndex)]);
      return;
    }
    const select = event.target.closest("[data-select-lead]");
    if (select) {
      const id = Number(select.dataset.selectLead);
      if (select.checked) state.selectedLeadIds.add(id);
      else state.selectedLeadIds.delete(id);
      updateBulkToolbar();
      return;
    }
    const button = event.target.closest("[data-detail-index]");
    if (!button) return;
    showCompanyDetail(state.filtered[Number(button.dataset.detailIndex)]);
  });
  $("#detail-close").addEventListener("click", () => closeDialogSmooth($("#company-dialog")));
  $("#detail-actions").addEventListener("click", (event) => {
    const copyCompany = event.target.closest("[data-copy-company]");
    const copyPhone = event.target.closest("[data-copy-phone]");
    if (copyCompany || copyPhone) {
      const value = copyCompany?.dataset.copyCompany || copyPhone?.dataset.copyPhone || "";
      copyText(value)
        .then(() => setNotice(copyCompany ? "公司名已复制。" : "电话已复制。"))
        .catch(() => setNotice("复制失败，请手动选择复制。", true));
      return;
    }
    if (event.target.closest("[data-reverse-current]")) reverseDevelop(state.currentLead);
  });
  $("#social-review-panel").addEventListener("click", (event) => {
    const feedbackButton = event.target.closest("[data-social-feedback]");
    if (feedbackButton) {
      submitSocialFeedback(feedbackButton.dataset.socialFeedback)
        .catch((error) => setNotice(error.message || "反馈保存失败", true));
    }
  });
  $("#social-entity-confirm").addEventListener("click", () => {
    confirmSocialEntityFromDetail()
      .catch((error) => setNotice(error.message || "企业主体确认失败", true));
  });
  $("#sales-form").addEventListener("submit", saveSalesRecord);
  $("#company-dialog").addEventListener("click", (event) => {
    if (event.target === $("#company-dialog")) closeDialogSmooth($("#company-dialog"));
  });
  $("#profile-create-button").addEventListener("click", openProfileDialog);
  $("#profile-close").addEventListener("click", () => closeDialogSmooth($("#profile-dialog")));
  $("#profile-dialog").addEventListener("click", (event) => {
    if (event.target === $("#profile-dialog")) closeDialogSmooth($("#profile-dialog"));
  });
  $("#profile-form").addEventListener("submit", saveProfile);
  $("#profile-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-profile-detail-index]");
    if (!button) return;
    showCompanyDetail(state.profileFiltered[Number(button.dataset.profileDetailIndex)]);
  });
  $("#profile-status-board").addEventListener("click", (event) => {
    const button = event.target.closest("[data-profile-status]");
    if (!button) return;
    $("#status-filter").value = button.dataset.profileStatus;
    $("#work-queue-filter").value = "";
    scheduleFilterRender();
  });
  $("#monitor-close").addEventListener("click", () => closeDialogSmooth($("#monitor-dialog")));
  $("#monitor-dialog").addEventListener("click", (event) => {
    if (event.target === $("#monitor-dialog")) closeDialogSmooth($("#monitor-dialog"));
  });
  $("#monitor-direction").addEventListener("change", () => {
    const direction = $("#monitor-direction").value;
    renderMonitorSectors(direction);
    if (!$("#monitor-name").value.trim() || DIRECTION_ORDER.some((item) => $("#monitor-name").value === `${DIRECTION_LABELS[item]}监控`)) {
      $("#monitor-name").value = `${DIRECTION_LABELS[direction]}监控`;
    }
    updateMonitorSummary();
  });
  $("#monitor-region-list").addEventListener("change", updateMonitorSummary);
  $("#monitor-sector-list").addEventListener("change", updateMonitorSummary);
  $("#monitor-regions").addEventListener("input", updateMonitorSummary);
  $("#monitor-custom-keywords").addEventListener("input", updateMonitorSummary);
  $("#monitor-form").addEventListener("submit", saveMonitor);
  $("#monitor-list").addEventListener("click", (event) => {
    monitorAction(event).catch((error) => setNotice(error.message || "操作失败", true));
  });
  $("#notification-list").addEventListener("click", (event) => {
    const item = event.target.closest("[data-notification-id]");
    if (item) openNotification(item);
  });
  $("#read-all-button").addEventListener("click", () => markNotificationsRead());
  $("#select-all-leads").addEventListener("change", (event) => {
    state.pageItems.forEach((lead) => {
      if (!lead.id) return;
      if (event.target.checked) state.selectedLeadIds.add(Number(lead.id));
      else state.selectedLeadIds.delete(Number(lead.id));
    });
    renderLeads();
  });
  $("#bulk-apply-button").addEventListener("click", () => {
    applyBulkUpdate().catch((error) => setNotice(error.message || "批量更新失败", true));
  });
  $("#bulk-clear-button").addEventListener("click", () => {
    state.selectedLeadIds.clear();
    renderLeads();
  });
  $("#backup-button").addEventListener("click", downloadBackup);
  $("#resolve-all-button").addEventListener("click", () => resolveSystemEvent());
  $("#event-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-resolve-event]");
    if (button) resolveSystemEvent(Number(button.dataset.resolveEvent));
  });

  $("#quick-filters").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    $$("#quick-filters button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    $("#filter").value = button.dataset.filter || "";
    resetCurrentPage();
    renderLeads();
  });
}

async function init() {
  try {
    setupMotion();
    setupWorkspaceChrome();
    await fetchConfig();
    setDirection("downstream");
    bindEvents();
    await loadDashboard();
  } catch (error) {
    setNotice(error.message || "初始化失败", true);
  }
}

init();
