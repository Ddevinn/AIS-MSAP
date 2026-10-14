from __future__ import annotations

import hashlib
import ipaddress
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from pwdlib import PasswordHash

from backend.app import storage
from backend.app.config import FRONTEND_DIR
from backend.app.context import DatasetContext, dataset_context

router = APIRouter()
password_hash = PasswordHash.recommended()
COOKIE = "ais_session"
SESSION_SECONDS = 12 * 3600
_dummy_hash = password_hash.hash(secrets.token_urlsafe(24))


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class CreateUser(Credentials):
    display_name: str = Field(default="", max_length=80)
    role: str = Field(default="user", pattern=r"^(user|superadmin)$")


class UpdateUser(BaseModel):
    display_name: str = Field(max_length=80)
    enabled: bool
    role: str = Field(pattern=r"^(user|superadmin)$")


class ResetPassword(BaseModel):
    password: str = Field(min_length=10, max_length=128)
    must_change_password: bool = True


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_local(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(client_ip(request))
        local_host = request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        return address.is_loopback and local_host
    except ValueError:
        return False


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        incoming = urlsplit(origin)
        expected = urlsplit(str(request.base_url))
        def port(url):
            return url.port or (443 if url.scheme == "https" else 80)
        return (incoming.scheme, incoming.hostname, port(incoming)) == (expected.scheme, expected.hostname, port(expected))
    except ValueError:
        return False


def identity(request: Request):
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 128:
        return None
    with storage.connection() as con:
        row = con.execute("""SELECT s.*, u.username, u.display_name, u.role, u.enabled,
            u.must_change_password FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1""", (token_hash(token), time.time())).fetchone()
    return dict(row) if row else None


def require_admin(request: Request) -> dict:
    user = getattr(request.state, "identity", None)
    if not user or user["role"] != "superadmin":
        raise HTTPException(403, "仅超级管理员可以使用用户管理后台。")
    return user


def new_session(con, user_id: int, request: Request) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    stamp = storage.now()
    con.execute("DELETE FROM sessions WHERE expires_at<=?", (time.time(),))
    con.execute("""INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,last_seen_at,expires_at,ip,user_agent)
        VALUES(?,?,?,?,?,?,?,?)""", (token_hash(token), user_id, csrf, stamp, stamp,
                                    time.time() + SESSION_SECONDS, client_ip(request), request.headers.get("user-agent", "")[:500]))
    return token, csrf


def session_response(token: str, user: dict, csrf: str, request: Request):
    response = JSONResponse({"user": user, "csrf_token": csrf})
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


def install_security(app: FastAPI) -> None:
    def initialize_accounts():
        storage.initialize()
        storage.recover_jobs()
    app.on_event("startup")(initialize_accounts)
    app.include_router(router)

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        mutating = request.method not in {"GET", "HEAD", "OPTIONS"}
        if mutating and not same_origin(request):
            return JSONResponse({"detail": "请求来源不匹配。"}, status_code=403)
        protected = path.startswith("/api/") and path not in {"/api/auth/login", "/api/auth/setup", "/api/auth/setup-status"}
        static_page = path.lower().startswith("/static/") and path.lower().endswith(".html") and not path.lower().endswith("/login.html")
        protected_page = path in {"/", "/admin", "/account/password"} or static_page
        docs = path in {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
        user = identity(request) if (protected or protected_page or docs) else None
        request.state.identity = user
        if (protected or protected_page or docs) and not user:
            if protected:
                return JSONResponse({"detail": "请先登录。"}, status_code=401, headers={"Cache-Control": "no-store"})
            return RedirectResponse("/login" if storage.has_users() else "/setup", status_code=303)
        admin_page = path == "/admin" or (static_page and path.lower().endswith("/admin.html"))
        if user and (path.startswith("/api/admin/") or admin_page or docs) and user["role"] != "superadmin":
            return JSONResponse({"detail": "仅超级管理员可以进入后台。"}, status_code=403)
        if user and mutating and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf_token"]):
            return JSONResponse({"detail": "登录校验已过期，请刷新页面。"}, status_code=403)
        allowed_change = {"/api/auth/me", "/api/auth/password", "/api/auth/logout", "/account/password"}
        if user and user["must_change_password"] and path not in allowed_change:
            if protected:
                return JSONResponse({"detail": "请先修改临时密码。", "code": "PASSWORD_CHANGE_REQUIRED"}, status_code=403)
            return RedirectResponse("/account/password", status_code=303)
        context_token = None
        if user:
            selected = user["active_dataset_id"]
            explicit = request.query_params.get("dataset_id")
            if explicit:
                with storage.connection() as con:
                    owned = con.execute("SELECT 1 FROM datasets WHERE id=? AND owner_id=?", (explicit, user["user_id"])).fetchone()
                if not owned:
                    return JSONResponse({"detail": "数据不存在或无权访问。"}, status_code=404)
                selected = explicit
            context_token = dataset_context.set(DatasetContext(user["user_id"], user["token_hash"], selected))
            stamp = storage.now()
            with storage.connection() as con:
                # Avoid a write for every tile and every status poll.
                if (datetime.now(timezone.utc) - datetime.fromisoformat(user["last_seen_at"])).total_seconds() >= 30:
                    con.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (stamp, user["token_hash"]))
                    con.execute("UPDATE users SET last_access_at=? WHERE id=?", (stamp, user["user_id"]))
                if protected_page and request.method == "GET":
                    con.execute("INSERT INTO access_events(user_id,path,created_at,ip) VALUES(?,?,?,?)",
                                (user["user_id"], path, stamp, client_ip(request)))
        try:
            response = await call_next(request)
            if protected or protected_page or docs:
                response.headers["Cache-Control"] = "no-store"
                response.headers["Vary"] = "Cookie"
                if user:
                    response.headers["X-AIS-User"] = str(user["user_id"])
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
            return response
        finally:
            if context_token is not None:
                dataset_context.reset(context_token)


@router.get("/login")
@router.get("/admin/login")
def login_page():
    if not storage.has_users():
        return RedirectResponse("/setup", status_code=303)
    return FileResponse(FRONTEND_DIR / "login.html", headers={"Cache-Control": "no-store"})


@router.get("/setup")
def setup_page(request: Request):
    if storage.has_users():
        return RedirectResponse("/login", status_code=303)
    if not is_local(request):
        raise HTTPException(403, "首次初始化请在服务器本机打开 127.0.0.1。")
    return FileResponse(FRONTEND_DIR / "login.html", headers={"Cache-Control": "no-store"})


@router.get("/api/auth/setup-status")
def setup_status(request: Request):
    return {"setup_required": not storage.has_users(), "local_setup_allowed": is_local(request)}


@router.post("/api/auth/setup")
def setup(payload: CreateUser, request: Request):
    if not is_local(request):
        raise HTTPException(403, "仅本机可以初始化超级管理员。")
    if len(payload.password) < 10:
        raise HTTPException(422, "密码至少需要 10 个字符。")
    hashed = password_hash.hash(payload.password)
    with storage.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "系统已经初始化，请登录。")
        stamp = storage.now()
        user_id = con.execute("""INSERT INTO users(username,display_name,password_hash,role,created_at,password_changed_at)
            VALUES(?,?,?,'superadmin',?,?)""", (payload.username, payload.display_name.strip() or payload.username, hashed, stamp, stamp)).lastrowid
        count = storage.migrate_legacy(con, user_id)
        storage.audit(con, user_id, "setup", user_id, client_ip(request), migrated_datasets=count)
    return {"created": True, "migrated_datasets": count}


@router.post("/api/auth/login")
def login(payload: Credentials, request: Request):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    with storage.connection() as con:
        attempts = con.execute("SELECT COUNT(*) FROM login_events WHERE success=0 AND ip=? AND created_at>?",
                               (client_ip(request), cutoff)).fetchone()[0]
        if attempts >= 20:
            raise HTTPException(429, "登录尝试过于频繁，请 15 分钟后再试。")
        row = con.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (payload.username,)).fetchone()
    valid = password_hash.verify(payload.password, row["password_hash"] if row else _dummy_hash)
    success = bool(row and valid and row["enabled"])
    admin_login = request.headers.get("x-login-target") == "admin"
    if admin_login and row and row["role"] != "superadmin":
        success = False
    with storage.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        if success:
            current = con.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            # A password reset/disable between verification and issuance must win.
            success = bool(current and current["enabled"] and current["password_hash"] == row["password_hash"]
                           and (not admin_login or current["role"] == "superadmin"))
        stamp = storage.now()
        con.execute("INSERT INTO login_events(user_id,username,success,created_at,ip,user_agent) VALUES(?,?,?,?,?,?)",
                    (row["id"] if row else None, payload.username, int(success), stamp, client_ip(request), request.headers.get("user-agent", "")[:500]))
        if success:
            old = request.cookies.get(COOKIE)
            if old:
                con.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(old),))
            con.execute("UPDATE users SET last_login_at=?,last_access_at=?,login_count=login_count+1 WHERE id=?",
                        (stamp, stamp, row["id"]))
            token, csrf = new_session(con, row["id"], request)
            row = con.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    if not success:
        raise HTTPException(401, "账号或密码不正确，或账号不可用。")
    return session_response(token, storage.public_user(row), csrf, request)


@router.get("/api/auth/me")
def me(request: Request):
    user = request.state.identity
    with storage.connection() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    return {"user": storage.public_user(row), "csrf_token": user["csrf_token"]}


@router.post("/api/auth/logout")
def logout(request: Request):
    with storage.connection() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (request.state.identity["token_hash"],))
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/account/password")
def password_page():
    return FileResponse(FRONTEND_DIR / "login.html")


@router.post("/api/auth/password")
def change_password(payload: ChangePassword, request: Request):
    user = request.state.identity
    with storage.connection() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id=?", (user["user_id"],)).fetchone()
    if not password_hash.verify(payload.current_password, row["password_hash"]):
        raise HTTPException(400, "当前密码不正确。")
    if payload.current_password == payload.new_password:
        raise HTTPException(400, "新密码需要与当前密码不同。")
    hashed = password_hash.hash(payload.new_password)
    with storage.connection() as con:
        con.execute("UPDATE users SET password_hash=?,must_change_password=0,password_changed_at=? WHERE id=?",
                    (hashed, storage.now(), user["user_id"]))
        con.execute("DELETE FROM sessions WHERE user_id=?", (user["user_id"],))
        storage.audit(con, user["user_id"], "change_password", user["user_id"], client_ip(request))
    response = JSONResponse({"changed": True, "login_required": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/admin")
def admin_page(request: Request):
    require_admin(request)
    return FileResponse(FRONTEND_DIR / "admin.html")


@router.get("/api/admin/users")
def users(request: Request):
    require_admin(request)
    with storage.connection() as con:
        rows = con.execute("SELECT * FROM users ORDER BY id").fetchall()
        counts = {r["owner_id"]: r["total"] for r in con.execute("SELECT owner_id,COUNT(*) total FROM datasets GROUP BY owner_id")}
    return {"users": [{**storage.public_user(row), "dataset_count": counts.get(row["id"], 0)} for row in rows]}


@router.post("/api/admin/users")
def create_user(payload: CreateUser, request: Request):
    actor = require_admin(request)
    if len(payload.password) < 10:
        raise HTTPException(422, "密码至少需要 10 个字符。")
    hashed = password_hash.hash(payload.password)
    try:
        with storage.connection() as con:
            stamp = storage.now()
            user_id = con.execute("""INSERT INTO users(username,display_name,password_hash,role,must_change_password,created_at,password_changed_at)
                VALUES(?,?,?,?,1,?,?)""", (payload.username, payload.display_name.strip() or payload.username, hashed, payload.role, stamp, stamp)).lastrowid
            storage.audit(con, actor["user_id"], "create_user", user_id, client_ip(request), username=payload.username, role=payload.role)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该账号已存在。") from exc
    return {"created": True, "user_id": user_id}


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, payload: UpdateUser, request: Request):
    actor = require_admin(request)
    with storage.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在。")
        if actor["user_id"] == user_id and (not payload.enabled or payload.role != "superadmin"):
            raise HTTPException(400, "不能禁用或降级当前登录的超级管理员。")
        admin_count = con.execute("SELECT COUNT(*) FROM users WHERE role='superadmin' AND enabled=1").fetchone()[0]
        if row["role"] == "superadmin" and row["enabled"] and admin_count <= 1 and (not payload.enabled or payload.role != "superadmin"):
            raise HTTPException(400, "至少保留一个启用的超级管理员。")
        con.execute("UPDATE users SET display_name=?,enabled=?,role=? WHERE id=?",
                    (payload.display_name.strip() or row["username"], int(payload.enabled), payload.role, user_id))
        if not payload.enabled or payload.role != row["role"]:
            con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        storage.audit(con, actor["user_id"], "update_user", user_id, client_ip(request), enabled=payload.enabled, role=payload.role)
    return {"updated": True}


@router.post("/api/admin/users/{user_id}/password")
def reset_password(user_id: int, payload: ResetPassword, request: Request):
    actor = require_admin(request)
    hashed = password_hash.hash(payload.password)
    with storage.connection() as con:
        if not con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "用户不存在。")
        con.execute("UPDATE users SET password_hash=?,must_change_password=?,password_changed_at=? WHERE id=?",
                    (hashed, int(payload.must_change_password), storage.now(), user_id))
        con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        storage.audit(con, actor["user_id"], "reset_password", user_id, client_ip(request))
    return {"reset": True}


@router.get("/api/admin/sessions")
def sessions(request: Request, user_id: int | None = None):
    actor = require_admin(request)
    with storage.connection() as con:
        rows = con.execute("""SELECT s.*,u.username,u.display_name FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.expires_at>? AND (? IS NULL OR s.user_id=?) ORDER BY s.last_seen_at DESC""", (time.time(), user_id, user_id)).fetchall()
    # Expose an opaque revocation handle, never a usable cookie or CSRF token.
    return {"sessions": [{"id": row["token_hash"], "user_id": row["user_id"], "username": row["username"],
                          "created_at": row["created_at"], "last_seen_at": row["last_seen_at"], "expires_at": row["expires_at"],
                          "ip": row["ip"], "user_agent": row["user_agent"], "current": row["token_hash"] == actor["token_hash"]}
                         for row in rows]}


@router.delete("/api/admin/sessions/{session_id}")
def revoke_session(session_id: str, request: Request):
    actor = require_admin(request)
    with storage.connection() as con:
        row = con.execute("SELECT user_id FROM sessions WHERE token_hash=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在或已退出。")
        con.execute("DELETE FROM sessions WHERE token_hash=?", (session_id,))
        storage.audit(con, actor["user_id"], "revoke_session", row["user_id"], client_ip(request))
    return {"revoked": True}


@router.get("/api/admin/logs")
def logs(request: Request, kind: str = "login", user_id: int | None = None,
         offset: int = 0, limit: int = 50):
    require_admin(request)
    if kind not in {"login", "access", "audit"} or offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, "无效的记录查询参数。")
    table = {"login": "login_events", "access": "access_events", "audit": "audit_events"}[kind]
    column = "actor_id" if kind == "audit" else "user_id"
    with storage.connection() as con:
        total = con.execute(f"SELECT COUNT(*) FROM {table} WHERE (? IS NULL OR {column}=?)", (user_id, user_id)).fetchone()[0]
        rows = con.execute(f"""SELECT e.*, u.username AS account FROM {table} e LEFT JOIN users u ON u.id=e.{column}
            WHERE (? IS NULL OR e.{column}=?) ORDER BY e.id DESC LIMIT ? OFFSET ?""", (user_id, user_id, limit, offset)).fetchall()
    return {"records": [dict(row) for row in rows], "total": total, "offset": offset, "limit": limit}
