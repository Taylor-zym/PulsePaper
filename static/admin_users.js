const shared = window.PulsePaperShared;
const { apiFetch, bootstrap, state } = shared;

const els = {
  adminPanel: document.getElementById("adminPanel"),
  adminCreateUserForm: document.getElementById("adminCreateUserForm"),
  adminNewUsername: document.getElementById("adminNewUsername"),
  adminNewPassword: document.getElementById("adminNewPassword"),
  adminDailySearchLimit: document.getElementById("adminDailySearchLimit"),
  adminCreateUserButton: document.getElementById("adminCreateUserButton"),
  adminUserStatus: document.getElementById("adminUserStatus"),
  adminUserList: document.getElementById("adminUserList"),
  statusMessage: document.getElementById("statusMessage"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;");
}

function getLimitInputValue(input) {
  if (!input) {
    return 3;
  }
  const parsed = Number.parseInt(input.value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return 3;
  }
  return parsed;
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
    const username = escapeHtml(item.username || "");
    const role = escapeHtml(item.role || "user");
    const status = escapeHtml(item.status || "active");
    const dailySearchLimit = Number.isFinite(Number(item.daily_search_limit)) ? Number(item.daily_search_limit) : 3;
    const dailySearchUsedToday = Number.isFinite(Number(item.daily_search_used_today)) ? Number(item.daily_search_used_today) : 0;
    return `
      <div class="history-item user-item" data-username="${username}">
        <div class="history-top">
          <strong>${username}</strong>
          <span>${role}</span>
        </div>
        <div class="history-meta">状态：${status}</div>
        <div class="history-meta">今日已用：${dailySearchUsedToday}/${dailySearchLimit}</div>
        <div class="terminal-meta-list">
          ${(chips.length ? chips : ["未绑定"]).map((chip) => `<span class="terminal-chip">${escapeHtml(chip)}</span>`).join("")}
        </div>
        <div class="grid two">
          <label class="field">
            <span>每日检索次数</span>
            <input class="admin-limit-input" type="number" min="0" step="1" value="${dailySearchLimit}">
          </label>
          <div class="field">
            <span>操作</span>
            <div class="header-actions">
              <button class="ghost admin-save-limit-button" type="button" data-username="${username}">保存次数</button>
              <button class="ghost admin-delete-user-button" type="button" data-username="${username}">删除用户</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join("");
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
      daily_search_limit: getLimitInputValue(els.adminDailySearchLimit),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  await loadUsers();
  return data;
}

async function updateUserLimit(username, limit) {
  const response = await apiFetch(`/api/admin/users/${encodeURIComponent(username)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ daily_search_limit: limit }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  await loadUsers();
  return data;
}

async function deleteUser(username) {
  const response = await apiFetch(`/api/admin/users/${encodeURIComponent(username)}`, {
    method: "DELETE",
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  await loadUsers();
  return data;
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
      if (els.adminDailySearchLimit) {
        els.adminDailySearchLimit.value = "3";
      }
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

if (els.adminUserList) {
  els.adminUserList.addEventListener("click", async (event) => {
    const saveButton = event.target.closest(".admin-save-limit-button");
    if (saveButton instanceof HTMLButtonElement) {
      saveButton.disabled = true;
      const username = saveButton.dataset.username || "";
      const container = saveButton.closest(".user-item");
      const input = container ? container.querySelector(".admin-limit-input") : null;
      try {
        const updated = await updateUserLimit(username, getLimitInputValue(input));
        if (els.adminUserStatus) {
          els.adminUserStatus.textContent = `已更新 ${updated.username} 的每日次数`;
        }
      } catch (error) {
        if (els.adminUserStatus) {
          els.adminUserStatus.textContent = error.message || "保存失败。";
        }
      } finally {
        saveButton.disabled = false;
      }
      return;
    }

    const deleteButton = event.target.closest(".admin-delete-user-button");
    if (!(deleteButton instanceof HTMLButtonElement)) {
      return;
    }
    const username = deleteButton.dataset.username || "";
    if (!window.confirm(`确认删除用户 ${username} 吗？`)) {
      return;
    }
    deleteButton.disabled = true;
    try {
      await deleteUser(username);
      if (els.adminUserStatus) {
        els.adminUserStatus.textContent = `已删除用户 ${username}`;
      }
    } catch (error) {
      if (els.adminUserStatus) {
        els.adminUserStatus.textContent = error.message || "删除失败。";
      }
    } finally {
      deleteButton.disabled = false;
    }
  });
}

void bootstrap(async () => {
  if (els.statusMessage) {
    els.statusMessage.textContent = "管理员用户管理";
  }
  if (state.currentRole !== "admin") {
    window.location.replace("/workflow");
    return;
  }
  if (els.adminPanel) {
    els.adminPanel.hidden = false;
  }
  await loadUsers();
});
