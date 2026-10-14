"use strict";
window.AIS = {
  user: null, csrf: "", leaving: false,
  async initialize() {
    const data = await this.json("/api/auth/me");
    this.user = data.user;
    this.csrf = data.csrf_token;
    if (this.user.must_change_password && location.pathname !== "/account/password") {
      location.replace("/account/password");
      throw new Error("请先修改临时密码。");
    }
    return this.user;
  },
  async request(url, options = {}) {
    if (this.leaving) throw new Error("正在退出登录。");
    const headers = new Headers(options.headers || {});
    if (options.method && !["GET", "HEAD", "OPTIONS"].includes(options.method.toUpperCase()) && this.csrf) {
      headers.set("X-CSRF-Token", this.csrf);
    }
    const response = await fetch(url, { ...options, headers, credentials: "same-origin", cache: "no-store" });
    const publicAuth = ["/api/auth/login", "/api/auth/setup"].includes(url);
    if (response.status === 401 && !publicAuth) {
      this.leaving = true;
      location.replace(location.pathname === "/admin" ? "/admin/login" : "/login");
      throw new Error("登录已过期，请重新登录。");
    }
    if (response.status === 403 && !publicAuth) {
      const error = await response.clone().json().catch(() => ({}));
      if (error.code === "PASSWORD_CHANGE_REQUIRED") location.replace("/account/password");
    }
    const account = response.headers.get("X-AIS-User");
    if (account && this.user && String(this.user.id) !== account) {
      this.leaving = true;
      location.reload();
      throw new Error("登录账号已切换，正在刷新。");
    }
    if (this.leaving) throw new Error("登录状态已变化。");
    return response;
  },
  async json(url, options = {}) {
    const response = await this.request(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请检查填写内容后重试。");
    return data;
  },
  async logout() {
    await this.json("/api/auth/logout", { method: "POST" });
    this.leaving = true;
    location.replace("/login");
  },
  formatTime(value) {
    return value ? new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }) : "—";
  },
  escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  },
};
