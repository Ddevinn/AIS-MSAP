"use strict";
const adminState = { users: [], tab: "users", offset: 0, limit: 50, total: 0, editId: null, resetId: null, requestId: 0 };
const el = id => document.getElementById(id);
const esc = value => AIS.escape(value);
const date = value => AIS.formatTime(value);
const roleName = role => role === "superadmin" ? "超级管理员" : "普通用户";
const actions = { setup: "初始化平台", create_user: "创建用户", update_user: "修改用户", reset_password: "重置密码", change_password: "修改本人密码", revoke_session: "结束登录会话" };
const send = (url, method, body) => AIS.json(url, { method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });

function status(message, error = false) {
  el("adminStatus").textContent = message;
  el("adminStatus").classList.toggle("error", error);
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const user = await AIS.initialize();
    if (user.role !== "superadmin") { location.replace("/"); return; }
    el("adminIdentity").textContent = `${user.display_name} · 超级管理员`;
    el("adminLogout").addEventListener("click", () => AIS.logout().catch(error => status(error.message, true)));
    el("createUserBtn").addEventListener("click", () => openUser());
    el("adminRefresh").addEventListener("click", async () => { try { await loadUsers(); await loadContent(); status("信息已更新。"); } catch (error) { status(error.message, true); } });
    document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
    document.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => el(button.dataset.closeDialog).close()));
    el("userSearch").addEventListener("input", renderUsers);
    el("logUserFilter").addEventListener("change", () => { adminState.offset = 0; safeLoad(); });
    el("previousPage").addEventListener("click", () => { adminState.offset = Math.max(0, adminState.offset - adminState.limit); safeLoad(); });
    el("nextPage").addEventListener("click", () => { adminState.offset += adminState.limit; safeLoad(); });
    el("userForm").addEventListener("submit", saveUser);
    el("resetPasswordForm").addEventListener("submit", savePassword);
    el("adminTableBody").addEventListener("click", tableAction);
    await loadUsers();
    renderUsers();
  } catch (error) { status(error.message, true); }
});

async function loadUsers() {
  const data = await AIS.json("/api/admin/users");
  adminState.users = data.users;
  el("totalUsers").textContent = data.users.length;
  el("enabledUsers").textContent = data.users.filter(user => user.enabled).length;
  el("superAdmins").textContent = data.users.filter(user => user.role === "superadmin" && user.enabled).length;
  el("totalDatasets").textContent = data.users.reduce((sum, user) => sum + user.dataset_count, 0);
  const selected = el("logUserFilter").value;
  el("logUserFilter").innerHTML = '<option value="">全部用户</option>' + data.users.map(user => `<option value="${user.id}">${esc(user.username)} · ${esc(user.display_name)}</option>`).join("");
  el("logUserFilter").value = selected;
}

function head(columns) { el("adminTableHead").innerHTML = `<tr>${columns.map(column => `<th>${column}</th>`).join("")}</tr>`; }
function empty(columns, text) { el("adminTableBody").innerHTML = `<tr><td colspan="${columns}" class="admin-empty">${esc(text)}</td></tr>`; }

function renderUsers() {
  if (adminState.tab !== "users") return;
  const search = el("userSearch").value.trim().toLowerCase();
  const users = adminState.users.filter(user => `${user.username} ${user.display_name}`.toLowerCase().includes(search));
  head(["用户账号", "角色 / 状态", "创建时间", "最后登录 / 最近访问", "登录次数 / 数据", "密码状态", "操作"]);
  el("recordCount").textContent = `${users.length} 个用户`;
  if (!users.length) { empty(7, "没有匹配的用户。"); return; }
  el("adminTableBody").innerHTML = users.map(user => `<tr>
    <td class="account-cell">${esc(user.username)}<small>${esc(user.display_name)} · ID ${user.id}</small></td>
    <td><span class="status-tag ${user.role === "superadmin" ? "admin" : ""}">${roleName(user.role)}</span><small><span class="status-tag ${user.enabled ? "" : "off"}">${user.enabled ? "已启用" : "已禁用"}</span></small></td>
    <td>${date(user.created_at)}</td><td>${date(user.last_login_at)}<small>访问：${date(user.last_access_at)}</small></td>
    <td>${user.login_count} 次登录<small>${user.dataset_count} 个数据文件</small></td>
    <td>${user.must_change_password ? "下次登录需修改" : "已设置"}<small>修改：${date(user.password_changed_at)}</small></td>
    <td><div class="admin-actions"><button data-action="edit" data-id="${user.id}">编辑</button>${user.id === AIS.user.id ? '<a class="account-button" href="/account/password">修改密码</a>' : `<button data-action="password" data-id="${user.id}">重置密码</button>`}<button data-action="logs" data-id="${user.id}">记录</button></div></td>
  </tr>`).join("");
}

function switchTab(tab) {
  adminState.tab = tab; adminState.offset = 0; adminState.requestId++;
  document.querySelectorAll("[data-tab]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.tab === tab)));
  el("userSearch").hidden = tab !== "users";
  el("logUserFilter").hidden = tab === "users";
  el("adminPagination").hidden = tab === "users" || tab === "sessions";
  status(""); safeLoad();
}

function safeLoad() { loadContent().catch(error => status(error.message, true)); }

async function loadContent() {
  if (adminState.tab === "users") { renderUsers(); return; }
  const tab = adminState.tab, requestId = ++adminState.requestId;
  const filter = el("logUserFilter").value;
  const params = new URLSearchParams({ ...(filter ? { user_id: filter } : {}) });
  if (tab === "sessions") {
    const data = await AIS.json(`/api/admin/sessions?${params}`);
    if (requestId !== adminState.requestId) return;
    head(["用户账号", "登录时间", "最近活动", "访问 IP", "浏览器信息", "操作"]);
    el("recordCount").textContent = `${data.sessions.length} 个有效会话`;
    if (!data.sessions.length) { empty(6, "没有有效登录会话。"); return; }
    el("adminTableBody").innerHTML = data.sessions.map(session => `<tr><td class="account-cell">${esc(session.username)}${session.current ? '<small>当前会话</small>' : ""}</td><td>${date(session.created_at)}</td><td>${date(session.last_seen_at)}</td><td>${esc(session.ip)}</td><td class="admin-log-detail">${esc(session.user_agent || "—")}</td><td><button class="account-button danger" data-action="revoke" data-id="${esc(session.id)}">结束会话</button></td></tr>`).join("");
    return;
  }
  params.set("kind", tab); params.set("offset", adminState.offset); params.set("limit", adminState.limit);
  const data = await AIS.json(`/api/admin/logs?${params}`);
  if (requestId !== adminState.requestId) return;
  adminState.total = data.total;
  head(tab === "login" ? ["用户账号", "登录时间", "结果", "访问 IP", "浏览器信息"] : tab === "access" ? ["用户账号", "访问时间", "访问页面", "访问 IP"] : ["操作人", "操作时间", "操作", "目标用户", "访问 IP", "详情"]);
  el("recordCount").textContent = `${data.total} 条记录`;
  if (!data.records.length) empty(tab === "login" ? 5 : tab === "access" ? 4 : 6, "暂无记录，新的访问将在这里显示。");
  else el("adminTableBody").innerHTML = data.records.map(record => {
    const account = esc(record.account || record.username || "已删除用户");
    if (tab === "login") return `<tr><td class="account-cell">${esc(record.username)}</td><td>${date(record.created_at)}</td><td><span class="status-tag ${record.success ? "" : "off"}">${record.success ? "成功" : "失败"}</span></td><td>${esc(record.ip)}</td><td class="admin-log-detail">${esc(record.user_agent || "—")}</td></tr>`;
    if (tab === "access") return `<tr><td>${account}</td><td>${date(record.created_at)}</td><td>${esc(({ "/": "分析工作台", "/admin": "用户管理后台", "/account/password": "修改密码" })[record.path] || record.path)}</td><td>${esc(record.ip)}</td></tr>`;
    const target = adminState.users.find(user => user.id === record.target_id);
    return `<tr><td>${account}</td><td>${date(record.created_at)}</td><td>${esc(actions[record.action] || record.action)}</td><td>${esc(target?.username || record.target_id || "—")}</td><td>${esc(record.ip)}</td><td class="admin-log-detail">${esc(record.details)}</td></tr>`;
  }).join("");
  el("previousPage").disabled = adminState.offset === 0;
  el("nextPage").disabled = adminState.offset + adminState.limit >= data.total;
  el("pageDescription").textContent = data.total ? `${adminState.offset + 1}–${Math.min(adminState.offset + adminState.limit, data.total)} / ${data.total}` : "0 条记录";
}

function openUser(user = null) {
  adminState.editId = user?.id ?? null;
  el("userForm").reset(); el("userDialogError").hidden = true;
  el("userDialogTitle").textContent = user ? `编辑 ${user.username}` : "创建用户";
  el("userDialogHint").textContent = user ? "禁用账号或修改角色会结束该用户已有的登录会话。" : "新用户第一次登录时需要修改临时密码。";
  el("editUsernameField").hidden = el("editPasswordField").hidden = Boolean(user);
  el("editUsername").required = el("editPassword").required = !user;
  el("editEnabledField").hidden = !user;
  el("editUsername").value = user?.username || "";
  el("editDisplayName").value = user?.display_name || "";
  el("editRole").value = user?.role || "user";
  el("editEnabled").checked = user ? Boolean(user.enabled) : true;
  el("userDialog").showModal();
}

async function saveUser(event) {
  event.preventDefault(); el("saveUserBtn").disabled = true; el("userDialogError").hidden = true;
  try {
    const common = { display_name: el("editDisplayName").value.trim(), role: el("editRole").value };
    if (adminState.editId) await send(`/api/admin/users/${adminState.editId}`, "PATCH", { ...common, enabled: el("editEnabled").checked });
    else await send("/api/admin/users", "POST", { ...common, username: el("editUsername").value.trim(), password: el("editPassword").value });
    el("editPassword").value = ""; el("userDialog").close();
    await loadUsers(); await loadContent(); status("账号信息已保存。");
  } catch (error) { el("userDialogError").textContent = error.message; el("userDialogError").hidden = false; }
  finally { el("saveUserBtn").disabled = false; }
}

async function savePassword(event) {
  event.preventDefault(); el("resetPasswordSubmit").disabled = true; el("resetDialogError").hidden = true;
  try {
    await send(`/api/admin/users/${adminState.resetId}/password`, "POST", { password: el("resetPasswordInput").value, must_change_password: el("resetForceChange").checked });
    el("resetPasswordInput").value = ""; el("passwordDialog").close();
    await loadUsers(); await loadContent(); status("密码已重置，该用户已有的登录会话已结束。");
  } catch (error) { el("resetDialogError").textContent = error.message; el("resetDialogError").hidden = false; }
  finally { el("resetPasswordSubmit").disabled = false; }
}

async function tableAction(event) {
  const button = event.target.closest("[data-action]"); if (!button) return;
  const user = adminState.users.find(item => item.id === Number(button.dataset.id));
  if (button.dataset.action === "edit") openUser(user);
  if (button.dataset.action === "password") {
    adminState.resetId = user.id; el("resetPasswordForm").reset(); el("resetDialogError").hidden = true;
    el("resetPasswordHint").textContent = `为 ${user.username} 设置新密码，并结束已有登录会话。`;
    el("passwordDialog").showModal();
  }
  if (button.dataset.action === "logs") { el("logUserFilter").value = String(user.id); switchTab("login"); }
  if (button.dataset.action === "revoke" && confirm("结束此登录会话？用户需要重新登录。")) {
    try { await send(`/api/admin/sessions/${button.dataset.id}`, "DELETE"); await loadContent(); status("登录会话已结束。"); }
    catch (error) { status(error.message, true); }
  }
}
