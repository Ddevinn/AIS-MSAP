"use strict";
document.addEventListener("DOMContentLoaded", async () => {
  const byId = id => document.getElementById(id);
  const setup = location.pathname === "/setup";
  const change = location.pathname === "/account/password";
  const admin = location.pathname === "/admin/login";
  if (setup) {
    byId("authEyebrow").textContent = "首次使用";
    byId("authTitle").textContent = "创建超级管理员";
    byId("authDescription").textContent = "设置平台管理账号。现有数据将归入该账号的历史数据。";
    byId("authDescription").hidden = false;
    byId("displayNameField").hidden = byId("confirmField").hidden = false;
    byId("confirmPassword").required = true;
    byId("password").minLength = 10;
    byId("password").autocomplete = "new-password";
    byId("password").placeholder = "至少 10 个字符";
    byId("username").value = "admin";
    byId("authSubmit").textContent = "创建管理员账号";
    byId("authHint").textContent = "账号使用 3–40 位字母、数字、下划线、点或短横线。请保存好管理员密码。";
    byId("alternateLogin").hidden = true;
  } else if (change) {
    byId("authEyebrow").textContent = "账号设置";
    byId("authTitle").textContent = "修改登录密码";
    byId("authDescription").textContent = "修改后需要重新登录，已有登录会话将结束。";
    byId("authDescription").hidden = false;
    byId("usernameField").hidden = true;
    byId("username").required = false;
    byId("passwordLabel").textContent = "当前密码";
    byId("newPasswordField").hidden = byId("confirmField").hidden = false;
    byId("newPassword").required = byId("confirmPassword").required = true;
    byId("authSubmit").textContent = "保存新密码";
    byId("authHint").textContent = "新密码至少 10 个字符，请避免使用账号名作为密码。";
    byId("alternateLogin").hidden = true;
    try {
      await AIS.initialize();
      if (!AIS.user.must_change_password) byId("returnWorkbench").hidden = false;
    } catch { return; }
  } else if (admin) {
    byId("authEyebrow").textContent = "平台管理";
    byId("authTitle").textContent = "登录用户管理后台";
    byId("authSubmit").textContent = "登录管理后台";
    byId("alternateLogin").href = "/login";
    byId("alternateLogin").textContent = "进入分析平台";
    byId("authHint").textContent = "此入口仅向超级管理员开放。";
  }
  document.title = `${byId("authTitle").textContent} · AIS数据分析与态势评估平台`;
  byId("authForm").addEventListener("submit", async event => {
    event.preventDefault();
    const error = byId("authError"), button = byId("authSubmit");
    error.hidden = true;
    if ((setup || change) && byId("confirmPassword").value !== byId(change ? "newPassword" : "password").value) {
      error.textContent = "两次输入的密码不一致。"; error.hidden = false; return;
    }
    button.disabled = true;
    try {
      if (setup) {
        await AIS.json("/api/auth/setup", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: byId("username").value.trim(), password: byId("password").value,
            display_name: byId("displayName").value.trim() }) });
      }
      if (change) {
        await AIS.json("/api/auth/password", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_password: byId("password").value, new_password: byId("newPassword").value }) });
        location.replace("/login");
      } else {
        const data = await AIS.json("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Login-Target": admin ? "admin" : "workbench" },
          body: JSON.stringify({ username: byId("username").value.trim(), password: byId("password").value }) });
        location.replace(data.user.must_change_password ? "/account/password" : admin ? "/admin" : "/");
      }
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { button.disabled = false; }
  });
});
