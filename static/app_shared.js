(() => {
  const els = {
    authGate: document.getElementById("authGate"),
    loginForm: document.getElementById("loginForm"),
    loginUser: document.getElementById("loginUser"),
    loginPass: document.getElementById("loginPass"),
    loginSubmit: document.getElementById("loginSubmit"),
    loginError: document.getElementById("loginError"),
    logoutButton: document.getElementById("logoutButton"),
    themeSwitch: document.getElementById("themeSwitch"),
    adminNavLink: document.getElementById("adminNavLink"),
    accountMeta: document.getElementById("accountMeta"),
  };

  const THEME_STORAGE_KEY = "workflow-theme";
  const DEFAULT_REQUEST_TIMEOUT_MS = 15000;
  const state = {
    authenticated: false,
    currentUser: null,
    currentRole: null,
  };

  function applyTheme(theme) {
    const isLight = theme === "light";
    document.body.classList.toggle("theme-light", isLight);
    if (els.themeSwitch) {
      els.themeSwitch.checked = isLight;
    }
  }

  function initTheme() {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    const theme = saved === "dark" ? "dark" : "light";
    applyTheme(theme);
  }

  function showLoginError(message) {
    if (els.loginError) {
      els.loginError.textContent = message || "";
    }
  }

  function refreshAdminNav() {
    if (!els.adminNavLink) {
      return;
    }
    els.adminNavLink.hidden = state.currentRole !== "admin";
  }

  function refreshAccountMeta() {
    if (!els.accountMeta) {
      return;
    }
    if (!state.authenticated || !state.currentUser) {
      els.accountMeta.textContent = "当前账号未登录";
      return;
    }
    const role = state.currentRole === "admin" ? "管理员" : "用户";
    els.accountMeta.textContent = `${state.currentUser} · ${role}`;
  }

  function setAuthenticated(value) {
    state.authenticated = Boolean(value);
    document.body.classList.toggle("is-authenticated", state.authenticated);
    if (!state.authenticated) {
      state.currentUser = null;
      state.currentRole = null;
    }
    refreshAdminNav();
    refreshAccountMeta();
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
    return data;
  }

  async function logout() {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      setAuthenticated(false);
      showLoginError("已退出，请重新登录。");
    }
  }

  function requireAdminPage() {
    if (document.body.dataset.page !== "admin-users") {
      return;
    }
    if (state.currentRole === "admin") {
      return;
    }
    window.location.replace("/workflow");
  }

  async function bootstrap(pageInit) {
    initTheme();
    if (els.themeSwitch) {
      els.themeSwitch.addEventListener("change", () => {
        const theme = els.themeSwitch.checked ? "light" : "dark";
        window.localStorage.setItem(THEME_STORAGE_KEY, theme);
        applyTheme(theme);
      });
    }
    if (els.logoutButton) {
      els.logoutButton.addEventListener("click", async () => {
        await logout();
      });
    }
    if (els.loginForm) {
      els.loginForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        showLoginError("");
        if (els.loginSubmit) {
          els.loginSubmit.disabled = true;
        }
        try {
          await login(els.loginUser?.value.trim() || "", els.loginPass?.value || "");
          if (els.loginPass) {
            els.loginPass.value = "";
          }
          requireAdminPage();
          if (typeof pageInit === "function") {
            await pageInit();
          }
        } catch (error) {
          showLoginError(error.message || "登录失败，请重试。");
        } finally {
          if (els.loginSubmit) {
            els.loginSubmit.disabled = false;
          }
        }
      });
    }

    const ok = await checkAuth();
    if (!ok) {
      showLoginError("请输入账号和密码登录。");
      return;
    }
    requireAdminPage();
    if (typeof pageInit === "function") {
      await pageInit();
    }
  }

  window.PulsePaperShared = {
    els,
    state,
    apiFetch,
    bootstrap,
    showLoginError,
  };
})();
