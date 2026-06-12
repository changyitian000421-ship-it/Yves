const state = {
  downstreamSectors: {},
  upstreamSectors: {},
  procurementSectors: {},
  direction: "downstream",
  leads: [],
  filtered: [],
  meta: {},
  activeJobId: "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

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
}

function renderSectors() {
  const container = $("#sector-list");
  const sectors = state.direction === "upstream"
    ? state.upstreamSectors
    : state.direction === "procurement"
      ? state.procurementSectors
      : state.downstreamSectors;
  const defaults = new Set(
    state.direction === "upstream"
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
  if ($("#only-phone")?.checked && !lead.phone) return false;
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
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function scoreClass(score) {
  if (score >= 70) return "score";
  if (score >= 50) return "score mid";
  return "score low";
}

function renderMetrics(leads) {
  const procurement = state.meta?.direction === "procurement" || state.direction === "procurement";
  $("#metric-count").textContent = leads.length;
  $("#metric-hot").textContent = leads.filter((lead) => Number(lead.score) >= 70).length;
  $("#metric-phone").textContent = procurement
    ? leads.filter((lead) => lead.phone).length
    : leads.filter((lead) => lead.phone).length;
  $("#metric-count-label").textContent = procurement ? "采购单位" : "线索数量";
  $("#metric-hot-label").textContent = procurement ? "重点项目" : "高潜线索";
  $("#metric-phone-label").textContent = "含电话";
  const modeLabels = {
    amap: "真实企业",
    task: "开发任务",
    need_key: "缺少 Key",
    procurement: "招采监控",
  };
  const mode = state.meta?.direction === "upstream" && state.meta?.mode === "amap"
    ? "上游副产"
    : state.leads.length || state.meta?.mode
      ? modeLabels[state.meta?.mode] || "待开始"
      : "待开始";
  $("#metric-mode").textContent = mode;
}

function renderLeads() {
  const query = $("#filter").value.trim();
  state.filtered = state.leads.filter((lead) => leadMatches(lead, query));
  renderMetrics(state.filtered);

  const tbody = $("#lead-body");
  if (!state.filtered.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="8">没有匹配的线索。</td></tr>`;
    return;
  }

  tbody.innerHTML = state.filtered
    .map((lead, index) => {
      const phone = lead.phone || "待补充";
      const contactOrSource = phone;
      const address = lead.address ? `<div class="subline">${escapeHtml(lead.address)}</div>` : "";
      const type = lead.raw_type ? `<div class="subline">${escapeHtml(lead.raw_type)}</div>` : "";
      const project = lead.project_title
        ? `<div class="subline">${escapeHtml(lead.project_title)}</div>`
        : "";
      const companyWebsiteNotice = lead.source === "企业官网";
      const searchUrl = lead.search_url
        ? `<a href="${escapeHtml(lead.search_url)}" target="_blank" rel="noreferrer">${state.direction === "procurement" ? (companyWebsiteNotice ? "官网公告" : "公告正文") : "搜索"}</a>`
        : "";
      const website = lead.website && lead.website !== lead.search_url
        ? `<a href="${escapeHtml(lead.website)}" target="_blank" rel="noreferrer">${state.direction === "procurement" ? "公告页面" : "核验"}</a>`
        : "";
      const companyWebsite = companyWebsiteNotice && lead.company_website
        ? `<a href="${escapeHtml(lead.company_website)}" target="_blank" rel="noreferrer">企业官网</a>`
        : "";
      const qcc = lead.qcc_url
        ? `<a href="${escapeHtml(lead.qcc_url)}" target="_blank" rel="noreferrer">${state.direction === "procurement" ? "公司核验" : "企查查"}</a>`
        : "";
      const detail = lead.source === "高德 POI" || lead.direction === "procurement"
        ? `<button class="detail-button" type="button" data-detail-index="${index}">详情</button>`
        : "";
      const confidence = lead.confidence
        ? `<span class="confidence confidence-${lead.confidence === "高" ? "high" : lead.confidence === "中" ? "medium" : "review"}">${escapeHtml(lead.confidence)}${lead.confidence.startsWith("官方") ? "" : "相关"}</span>`
        : "";
      return `
        <tr>
          <td><span class="${scoreClass(Number(lead.score))}">${escapeHtml(lead.score)}</span></td>
          <td>
            <div class="company">${escapeHtml(lead.company)}</div>
            ${project}
            ${address}
            ${type}
          </td>
          <td>${escapeHtml(lead.sector)}</td>
          <td>${escapeHtml(lead.region)}</td>
          <td>${escapeHtml(contactOrSource)}</td>
          <td>
            ${escapeHtml(lead.match_reason)}
            ${confidence}
            <div class="subline">${escapeHtml(lead.use_case)}</div>
          </td>
          <td>${escapeHtml(lead.pitch)}</td>
          <td><div class="link-group">${detail}${searchUrl}${website}${companyWebsite}${qcc}</div></td>
        </tr>
      `;
    })
    .join("");
}

async function fetchConfig() {
  const response = await fetch("/api/config");
  if (response.status === 401) {
    window.location.href = "/login?v=sms-login-1";
    return;
  }
  if (!response.ok) throw new Error("配置加载失败");
  const config = await response.json();
  state.downstreamSectors = config.downstreamSectors || config.sectors;
  state.upstreamSectors = config.upstreamSectors || {};
  state.procurementSectors = config.procurementSectors || {};
  renderSectors();
  $("#api-status").textContent = config.hasEnvAmapKey
    ? "企业采集服务已启用"
    : "企业采集服务未配置，请联系管理员";
  $("#api-status").classList.toggle("error", !config.hasEnvAmapKey);
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
    dateWindow: $("#date-window").value,
  };
}

function setDirection(direction) {
  state.direction = ["upstream", "procurement"].includes(direction) ? direction : "downstream";
  const upstream = state.direction === "upstream";
  const procurement = state.direction === "procurement";
  renderSectors();
  $("#sector-title").textContent = upstream
    ? "可能副产液钙的行业"
    : procurement
      ? "监控主题"
      : "下游行业";
  $("#direction-note").textContent = upstream
    ? "查找生产过程中可能形成液体氯化钙的企业；结果属于工艺线索，需要进一步核实。"
    : procurement
      ? "可直接采集公共资源平台公告，也可先找公司再检索企业官网采购公告。"
      : "查找可能采购氯化钙的下游企业。";
  $("#custom-keywords").placeholder = upstream
    ? "例如：副产盐酸, 石灰中和, 湿法冶炼, 飞灰水洗"
    : procurement
      ? "例如：氯化钙框架采购, 液体氯化钙处置"
      : "例如：融雪剂厂家, 集装箱干燥剂, 钻井液";
  $("#exclude-suppliers-wrap").hidden = !upstream;
  $("#strict-upstream-wrap").hidden = !upstream;
  $("#pages-wrap").hidden = procurement;
  $("#only-phone-wrap").hidden = procurement;
  $("#fast-mode-wrap").hidden = procurement;
  $("#procurement-options").hidden = !procurement;
  $("#api-status").hidden = procurement;
  $("#only-phone").checked = !upstream && !procurement;
  $("#result-title").textContent = upstream
    ? "液体氯化钙副产企业线索"
    : procurement
      ? "招投标/采购信息监控"
      : "潜在买家列表";
  $("#reason-heading").textContent = upstream ? "工艺匹配依据" : procurement ? "监控规则" : "匹配原因";
  $("#pitch-heading").textContent = upstream ? "核实重点" : procurement ? "跟进重点" : "跟进话术";
  $("th:nth-child(2)").textContent = procurement ? "采购单位/项目" : "公司/任务";
  $("th:nth-child(3)").textContent = procurement ? "公告类型" : "行业";
  $("th:nth-child(5)").textContent = "电话";
  $("#collect-button-label").textContent = upstream
    ? "采集液钙副产企业"
    : procurement
      ? "采集采购单位和项目信息"
      : "采集具体公司和电话";
  $("#task-button").hidden = procurement;
  $("#quick-filters").innerHTML = upstream
    ? `
      <button type="button" data-filter="">全部</button>
      <button type="button" data-filter="高">高相关</button>
      <button type="button" data-filter="稀土">稀土</button>
      <button type="button" data-filter="环氧氯丙烷">环氧氯丙烷</button>
      <button type="button" data-filter="飞灰">飞灰水洗</button>
      <button type="button" data-filter="钨">钨业</button>
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
  $("#lead-body").innerHTML = `<tr class="empty-row"><td colspan="8">选择地区和行业后开始采集。</td></tr>`;
  renderMetrics([]);
  setNotice(
    upstream
      ? "当前为上游副产液钙企业采集模式。"
      : procurement
        ? "选择监控主题、公告类型和时间范围后采集真实采购单位。"
        : "当前为下游买家采集模式。",
  );
}

async function runSearch(mode = "amap") {
  const button = $(".primary");
  const payload = buildPayload();
  if (state.direction === "procurement" && !payload.procurementSources.length) {
    setNotice("请至少选择一个采集来源。", true);
    return;
  }
  button.disabled = true;
  button.textContent = "正在采集...";
  const scope = mode === "amap" && payload.fastMode ? "快速模式" : "全面模式";
  setNotice(
    state.direction === "procurement"
      ? payload.procurementSources.includes("company_website")
        ? "正在查找目标公司，并检查企业官网采购栏目。"
        : "正在采集公共资源平台的采购单位和公告详情。"
      : mode === "amap"
        ? `正在采集具体公司（${scope}）。`
        : "正在生成开发任务。",
  );
  if (state.direction === "procurement") {
    payload.amapKey = "";
    payload.requireAmap = false;
  } else if (mode === "task") {
    payload.amapKey = "";
    payload.requireAmap = false;
  } else {
    payload.requireAmap = true;
  }

  try {
    showProgress(
      mode === "amap"
        ? state.direction === "procurement"
          ? "正在采集采购单位"
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
      window.location.href = "/login?v=sms-login-1";
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
  $("#progress-requests-label").textContent = procurement ? "查询" : "请求";
  $("#progress-companies-label").textContent = procurement ? "采购单位" : "企业";
  $("#progress-phones-label").textContent = "有电话";
}

async function pollSearchJob(jobId) {
  while (state.activeJobId === jobId) {
    const response = await fetch(`/api/search/status?id=${encodeURIComponent(jobId)}`);
    if (response.status === 401) {
      window.location.href = "/login?v=sms-login-1";
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
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("采集任务已停止");
}

function applySearchResult(data) {
  state.leads = data.leads || [];
  state.meta = data.meta || {};
  renderLeads();
  $("#export-button").disabled = !state.leads.length;
  const warnings = data.errors?.length ? ` ${data.errors[0]}` : "";
  const realCount = data.meta?.companyCount || 0;
  const phoneCount = data.meta?.phoneCount || 0;
  const summary = data.meta?.mode === "amap"
    ? data.meta?.direction === "upstream"
      ? `已发现 ${realCount} 家可能副产液体氯化钙的企业，其中 ${phoneCount} 家有电话；请按相关度核实工艺。`
      : `已采集 ${realCount} 家具体公司，其中 ${phoneCount} 家有电话；完成 ${data.meta?.requestCount || 0} 次查询。`
    : data.meta?.mode === "procurement"
      ? `已采集 ${state.leads.length} 条招采公告，其中 ${phoneCount} 条含采购单位电话；来源：${(data.meta?.procurementSources || []).map((source) => source === "company_website" ? "企业官网" : "公共资源平台").join("、") || "公共资源平台"}。`
      : data.meta?.mode === "need_key"
      ? "未开始采集。"
      : `已生成 ${state.leads.length} 条开发任务。`;
  setNotice(`${summary}${warnings}`, Boolean(data.errors?.length));
}

function detailItem(label, value, isLink = false) {
  if (!value) return "";
  const content = isLink
    ? `<a href="${escapeHtml(value)}" target="_blank" rel="noreferrer">${escapeHtml(value)}</a>`
    : escapeHtml(value);
  return `<div><dt>${escapeHtml(label)}</dt><dd>${content}</dd></div>`;
}

function showCompanyDetail(lead) {
  const procurement = lead.direction === "procurement";
  const companyWebsiteNotice = lead.source === "企业官网";
  $("#detail-company").textContent = lead.company || (procurement ? "采购单位详情" : "企业详情");
  $("#detail-grid").innerHTML = [
    detailItem("采购项目", procurement ? lead.project_title : ""),
    detailItem("公告类型", procurement ? lead.sector : ""),
    detailItem("公告日期", procurement ? lead.notice_date : ""),
    detailItem("企业别名", lead.alias),
    detailItem("联系电话", lead.phone),
    detailItem("项目联系人", procurement ? lead.contact_name : ""),
    detailItem("采购代理机构", procurement ? lead.agency : ""),
    detailItem("公开邮箱", lead.email),
    detailItem("企业官网", lead.company_website, true),
    detailItem("所属地区", lead.region),
    detailItem("详细地址", lead.address),
    detailItem(procurement ? "公告来源平台" : "高德行业类型", lead.raw_type),
    detailItem(
      lead.direction === "upstream" ? "副产工艺依据" : procurement ? "数据依据" : "潜在用途",
      lead.process_basis || lead.use_case,
    ),
    detailItem(
      "线索置信度",
      lead.confidence ? `${lead.confidence}${lead.confidence.startsWith("官方") ? "" : "相关"}` : "",
    ),
    detailItem("预算金额（元）", procurement ? lead.budget : ""),
    detailItem("投标/响应截止", procurement ? lead.deadline : ""),
    detailItem(lead.direction === "upstream" ? "建议核实内容" : "销售跟进重点", lead.pitch),
    detailItem("地图坐标", lead.location),
    detailItem("高德 POI ID", lead.poi_id),
    detailItem("数据更新时间", lead.updated_at),
    detailItem("数据来源", lead.source),
  ].join("") || "<p>暂无更多公开信息。</p>";
  $("#detail-actions").innerHTML = [
    lead.search_url ? `<a href="${escapeHtml(lead.search_url)}" target="_blank" rel="noreferrer">${procurement ? (companyWebsiteNotice ? "官网公告" : "公告正文") : "高德地图"}</a>` : "",
    companyWebsiteNotice && lead.company_website ? `<a href="${escapeHtml(lead.company_website)}" target="_blank" rel="noreferrer">企业官网</a>` : "",
    lead.qcc_url ? `<a href="${escapeHtml(lead.qcc_url)}" target="_blank" rel="noreferrer">工商信息核验</a>` : "",
    lead.website && lead.website !== lead.search_url ? `<a href="${escapeHtml(lead.website)}" target="_blank" rel="noreferrer">${procurement ? "公共资源交易平台" : "网页搜索"}</a>` : "",
  ].join("");
  $("#company-dialog").showModal();
}

async function exportCsv() {
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ leads: state.filtered.length ? state.filtered : state.leads }),
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
  window.location.href = "/login?v=sms-login-1";
}

function bindEvents() {
  $("#lead-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch("amap");
  });

  $("#task-button").addEventListener("click", () => runSearch("task"));
  $("#export-button").addEventListener("click", exportCsv);
  $("#logout-button").addEventListener("click", logout);
  $("#filter").addEventListener("input", renderLeads);
  $("#only-phone").addEventListener("change", renderLeads);
  $$('input[name="direction"]').forEach((input) => {
    input.addEventListener("change", () => setDirection(input.value));
  });
  $("#lead-body").addEventListener("click", (event) => {
    const button = event.target.closest("[data-detail-index]");
    if (!button) return;
    showCompanyDetail(state.filtered[Number(button.dataset.detailIndex)]);
  });
  $("#detail-close").addEventListener("click", () => $("#company-dialog").close());
  $("#company-dialog").addEventListener("click", (event) => {
    if (event.target === $("#company-dialog")) $("#company-dialog").close();
  });

  $("#quick-filters").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    $$("#quick-filters button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    $("#filter").value = button.dataset.filter || "";
    renderLeads();
  });
}

async function init() {
  try {
    await fetchConfig();
    setDirection("downstream");
    bindEvents();
  } catch (error) {
    setNotice(error.message || "初始化失败", true);
  }
}

init();
