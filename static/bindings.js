const shared = window.PulsePaperShared;
const { apiFetch, bootstrap, state } = shared;

const els = {
  accountMeta: document.getElementById("accountMeta"),
  bindingsStatus: document.getElementById("bindingsStatus"),
  bindingZoteroId: document.getElementById("bindingZoteroId"),
  bindingZoteroKey: document.getElementById("bindingZoteroKey"),
  bindingSpringerKey: document.getElementById("bindingSpringerKey"),
  bindingElsevierKey: document.getElementById("bindingElsevierKey"),
  bindingIEEEKey: document.getElementById("bindingIEEEKey"),
  saveBindingsButton: document.getElementById("saveBindingsButton"),
  statusMessage: document.getElementById("statusMessage"),
};

function applyBindings(bindings) {
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
    els.bindingSpringerKey.placeholder = configured.springer_api_key ? "已绑定，留空表示不修改" : "可选，未绑定则回退全局配置";
  }
  if (els.bindingElsevierKey) {
    els.bindingElsevierKey.value = "";
    els.bindingElsevierKey.placeholder = configured.elsevier_api_key ? "已绑定，留空表示不修改" : "可选，未绑定则回退全局配置";
  }
  if (els.bindingIEEEKey) {
    els.bindingIEEEKey.value = "";
    els.bindingIEEEKey.placeholder = configured.ieee_api_key ? "已绑定，留空表示不修改" : "可选，未绑定则回退全局配置";
  }
  if (els.accountMeta) {
    const username = state.currentUser || "未登录";
    const role = state.currentRole === "admin" ? "管理员" : "用户";
    els.accountMeta.textContent = `${username} · ${role}`;
  }
  if (els.bindingsStatus) {
    const chips = [];
    if (configured.zotero_key) chips.push("已绑定 Zotero Key");
    if (configured.springer_api_key) chips.push("已绑定 Springer Key");
    if (configured.elsevier_api_key) chips.push("已绑定 Elsevier Key");
    if (configured.ieee_api_key) chips.push("已绑定 IEEE Key");
    els.bindingsStatus.textContent = chips.length
      ? chips.join(" / ")
      : "Zotero 不会回退到全局配置；Springer / Elsevier / IEEE 为可选覆盖项，未绑定时可回退到全局配置。";
  }
}

async function loadBindings() {
  const response = await apiFetch("/api/users/me/bindings", {
    headers: { "Cache-Control": "no-store" },
  });
  const data = await response.json();
  applyBindings(data);
}

function buildBindingsPayload() {
  const payload = {
    zotero_id: els.bindingZoteroId.value.trim(),
  };
  const keyFields = [
    ["zotero_key", els.bindingZoteroKey],
    ["springer_api_key", els.bindingSpringerKey],
    ["elsevier_api_key", els.bindingElsevierKey],
    ["ieee_api_key", els.bindingIEEEKey],
  ];
  keyFields.forEach(([field, input]) => {
    if (input && input.value) {
      payload[field] = input.value;
    }
  });
  return payload;
}

async function saveBindings() {
  const response = await apiFetch("/api/users/me/bindings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildBindingsPayload()),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  applyBindings(data);
  return data;
}

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

void bootstrap(async () => {
  if (els.statusMessage) {
    els.statusMessage.textContent = "管理当前账号绑定";
  }
  await loadBindings();
});
