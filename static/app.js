const state = {
  sectors: {},
  leads: [],
  filtered: [],
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
  const defaults = new Set(["snow", "desiccant", "water", "concrete", "trader"]);
  container.innerHTML = Object.entries(state.sectors)
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
  $("#metric-count").textContent = leads.length;
  $("#metric-hot").textContent = leads.filter((lead) => Number(lead.score) >= 70).length;
  $("#metric-phone").textContent = leads.filter((lead) => lead.phone).length;
  const modeLabels = {
    amap: "真实企业",
    task: "开发任务",
    need_key: "缺少 Key",
  };
  const mode = state.leads.length || state.meta?.mode
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
    .map((lead) => {
      const phone = lead.phone || "待补充";
      const address = lead.address ? `<div class="subline">${escapeHtml(lead.address)}</div>` : "";
      const type = lead.raw_type ? `<div class="subline">${escapeHtml(lead.raw_type)}</div>` : "";
      const searchUrl = lead.search_url
        ? `<a href="${escapeHtml(lead.search_url)}" target="_blank" rel="noreferrer">搜索</a>`
        : "";
      const website = lead.website
        ? `<a href="${escapeHtml(lead.website)}" target="_blank" rel="noreferrer">核验</a>`
        : "";
      const qcc = lead.qcc_url
        ? `<a href="${escapeHtml(lead.qcc_url)}" target="_blank" rel="noreferrer">企查查</a>`
        : "";
      return `
        <tr>
          <td><span class="${scoreClass(Number(lead.score))}">${escapeHtml(lead.score)}</span></td>
          <td>
            <div class="company">${escapeHtml(lead.company)}</div>
            ${address}
            ${type}
          </td>
          <td>${escapeHtml(lead.sector)}</td>
          <td>${escapeHtml(lead.region)}</td>
          <td>${escapeHtml(phone)}</td>
          <td>
            ${escapeHtml(lead.match_reason)}
            <div class="subline">${escapeHtml(lead.use_case)}</div>
          </td>
          <td>${escapeHtml(lead.pitch)}</td>
          <td><div class="link-group">${searchUrl}${website}${qcc}</div></td>
        </tr>
      `;
    })
    .join("");
}

async function fetchConfig() {
  const response = await fetch("/api/config");
  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  if (!response.ok) throw new Error("配置加载失败");
  const config = await response.json();
  state.sectors = config.sectors;
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
  if (customRegions) regions.push(customRegions);

  return {
    regions,
    sectors: selectedValues("sector"),
    customKeywords: $("#custom-keywords").value.trim(),
    pages: Number($("#pages").value || 1),
    includeProcurement: $("#include-procurement").checked,
    fastMode: $("#fast-mode").checked,
  };
}

async function runSearch(mode = "amap") {
  const button = $(".primary");
  button.disabled = true;
  button.textContent = "正在采集...";
    const scope = mode === "amap" && payload.fastMode ? "快速模式" : "全面模式";
    setNotice(mode === "amap" ? `正在采集具体公司（${scope}）。` : "正在生成开发任务。");

  const payload = buildPayload();
  if (mode === "task") {
    payload.amapKey = "";
    payload.requireAmap = false;
  } else {
    payload.requireAmap = true;
  }

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error("采集请求失败");
    const data = await response.json();
    state.leads = data.leads || [];
    state.meta = data.meta || {};
    renderLeads();
    $("#export-button").disabled = !state.leads.length;
    const warnings = data.errors?.length ? ` ${data.errors[0]}` : "";
    const realCount = data.meta?.companyCount || 0;
    const phoneCount = data.meta?.phoneCount || 0;
    const summary = data.meta?.mode === "amap"
      ? `已采集 ${realCount} 家具体公司，其中 ${phoneCount} 家有电话；完成 ${data.meta?.requestCount || 0} 次查询。`
      : data.meta?.mode === "need_key"
        ? "未开始采集。"
        : `已生成 ${state.leads.length} 条开发任务。`;
    setNotice(`${summary}${warnings}`, Boolean(data.errors?.length));
  } catch (error) {
    setNotice(error.message || "采集失败", true);
  } finally {
    button.disabled = false;
    button.textContent = "采集具体公司和电话";
  }
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
  window.location.href = "/login";
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
    bindEvents();
  } catch (error) {
    setNotice(error.message || "初始化失败", true);
  }
}

init();
