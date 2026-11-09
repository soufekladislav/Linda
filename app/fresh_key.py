from __future__ import annotations

"""Linda adapter for the central Fresh Key service in Fresh AI Engine.

Linda keeps only local work data and non-secret pointers. OAuth tokens remain in
Fresh Key. This module maps Linda users/projects to the global Fresh identity and
project graph and hydrates a connector only for the duration of a publish/test.
"""

import json
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import text

from .config import settings
from .db import db, one, rows

SOCIAL_PROVIDERS = {"facebook", "instagram", "linkedin", "google_business"}


def ensure_schema() -> None:
    # Non-destructive compatibility migration. Existing IDs/content are untouched.
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS fresh_key_identity_id VARCHAR(36) NULL",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS fresh_key_project_id VARCHAR(36) NULL",
        "ALTER TABLE social_connectors ADD COLUMN IF NOT EXISTS fresh_key_connection_id VARCHAR(36) NULL",
        "ALTER TABLE social_connectors ADD COLUMN IF NOT EXISTS fresh_key_managed TINYINT(1) NOT NULL DEFAULT 0",
        "CREATE TABLE IF NOT EXISTS project_members (id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id BIGINT NOT NULL, user_id BIGINT NOT NULL, role VARCHAR(40) NOT NULL DEFAULT 'editor', relationship VARCHAR(24) NOT NULL DEFAULT 'member', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_linda_project_member(project_id,user_id), KEY ix_linda_project_member_user(user_id), KEY ix_linda_project_member_project(project_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS project_invitations (id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id BIGINT NOT NULL, email VARCHAR(190) NOT NULL, role VARCHAR(40) NOT NULL DEFAULT 'editor', relationship VARCHAR(24) NOT NULL DEFAULT 'member', status VARCHAR(24) NOT NULL DEFAULT 'pending', invited_by BIGINT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uq_linda_project_invite(project_id,email), KEY ix_linda_project_invite_email(email,status), KEY ix_linda_project_invite_project(project_id,status)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "ALTER TABLE project_members ADD COLUMN IF NOT EXISTS relationship VARCHAR(24) NOT NULL DEFAULT 'member'",
        "ALTER TABLE project_invitations ADD COLUMN IF NOT EXISTS relationship VARCHAR(24) NOT NULL DEFAULT 'member'",
        "INSERT IGNORE INTO project_members(project_id,user_id,role) SELECT id,user_id,'owner' FROM projects",
    ]
    # Run each additive statement independently.  In 0.4.8.4 one failed ALTER
    # rolled back/skipped the later owner mirror INSERT, which could leave a valid
    # project owner looking like an Editor/unknown role in the Propojení card.
    # Fresh Key is optional, so a single unsupported DDL statement must never stop
    # the remaining compatibility repairs.
    for sql in statements:
        try:
            with db() as conn:
                conn.execute(text(sql))
        except Exception:
            continue


def configured() -> bool:
    return bool(settings.engine_url and settings.engine_api_key)


ROLE_LABELS = {"owner": "Vlastník", "manager": "Správce", "editor": "Editor", "viewer": "Čtenář"}
RELATIONSHIP_LABELS = {"member": "Člen týmu", "client": "Klient", "agency": "Agentura"}


def normalize_role(role: str) -> str:
    value = str(role or "editor").strip().lower()
    # Legacy/system roles are mapped to the four project ACL roles.  Older
    # Linda installations could store `admin`/`superadmin` in project sharing
    # rows; treating those as Editor made an otherwise privileged project user
    # unable to publish/spravit Fresh Key.
    if value in {"admin", "superadmin", "administrator", "spravce", "správce"}:
        return "manager"
    if value in {"member", "client", "agency", "user"}:
        return "editor"
    if value in {"visitor", "reader", "readonly", "read_only"}:
        return "viewer"
    return value if value in ROLE_LABELS else "editor"


def normalize_relationship(value: str, legacy_role: str = "") -> str:
    rel = str(value or "").strip().lower()
    if not rel and str(legacy_role or "").lower() in {"client", "agency"}:
        rel = str(legacy_role).lower()
    return rel if rel in RELATIONSHIP_LABELS else "member"


def manage_url(fresh_key_id: str = "") -> str:
    base = settings.engine_url.rstrip("/") + "/account/fresh-key"
    return base + (("?link=" + str(fresh_key_id)) if fresh_key_id else "")


def _headers(user_id: int | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.engine_api_key}", "X-Fresh-App": "linda", "Accept": "application/json"}
    if user_id:
        headers["X-Fresh-User"] = str(user_id)
    return headers


def _request(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None, user_id: int | None = None) -> dict:
    if not configured():
        raise RuntimeError("Fresh Key není dostupný: chybí FRESH_ENGINE_URL nebo FRESH_ENGINE_API_KEY.")
    url = settings.engine_url.rstrip("/") + path
    with httpx.Client(timeout=35, follow_redirects=True) as client:
        response = client.request(method, url, headers=_headers(user_id), json=json_body, params=params)
    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "error": response.text[:500]}
    if response.status_code >= 400 or not data.get("ok", False):
        raise RuntimeError(str(data.get("error") or f"Fresh Key HTTP {response.status_code}"))
    return data


def sso_authorize_url(state: str, return_url: str) -> str:
    if not configured():
        raise RuntimeError("Fresh Key není dostupný: chybí FRESH_ENGINE_URL nebo FRESH_ENGINE_API_KEY.")
    return settings.engine_url.rstrip("/") + "/fresh-key/sso/authorize?" + urlencode({
        "app_code": "linda", "state": state, "return_url": return_url,
    })


def sso_exchange(code: str) -> dict[str, Any]:
    return _request("POST", "/v1/fresh-key/sso/exchange", json_body={"app_code": "linda", "code": code})


def sso_bind(binding_token: str, user_id: int) -> str:
    data = _request("POST", "/v1/fresh-key/sso/bind", json_body={
        "app_code": "linda", "binding_token": binding_token, "external_user_id": str(user_id),
    })
    return str(data.get("identity_id") or "")


def ensure_identity(user_id: int) -> str:
    user = one("SELECT id,name,email,fresh_key_identity_id FROM users WHERE id=:id LIMIT 1", {"id": user_id})
    if not user:
        raise RuntimeError("Linda uživatel nenalezen.")
    current = str(user.get("fresh_key_identity_id") or "").strip()
    if current:
        # Older Linda versions could already know the Fresh Key identity while
        # the lightweight AI Engine customer shell did not yet exist. Bootstrap
        # is idempotent, so make sure central management is available too.
        try:
            _request("POST", "/v1/fresh-key/account/bootstrap", json_body={
                "app_code": "linda",
                "external_user_id": str(user_id),
                "email": str(user.get("email") or "").strip().lower(),
                "display_name": str(user.get("name") or user.get("email") or ""),
                "email_verified": True,
            }, user_id=user_id)
        except Exception:
            pass
        return current
    # Fresh Key is free and may be activated directly from any Fresh app. The
    # trusted app provisions a lightweight AI Engine/Fresh Key shell; credits
    # are neither created nor required.
    try:
        data = _request("POST", "/v1/fresh-key/account/bootstrap", json_body={
            "app_code": "linda",
            "external_user_id": str(user_id),
            "email": str(user.get("email") or "").strip().lower(),
            "display_name": str(user.get("name") or user.get("email") or ""),
            "email_verified": True,
        }, user_id=user_id)
        identity_id = str(data.get("identity_id") or "")
    except Exception:
        # Compatibility with an older Engine: resolve a pre-existing binding.
        try:
            data = _request("GET", "/v1/fresh-key/identity/resolve", user_id=user_id)
            identity_id = str((data.get("identity") or {}).get("id") or "")
        except Exception:
            identity_id = ""
    if not identity_id:
        raise RuntimeError("Fresh Key se pro tento účet nepodařilo aktivovat. Otevři AI Engine a přihlas se stejným Fresh účtem.")
    with db() as conn:
        conn.execute(text("UPDATE users SET fresh_key_identity_id=:fk WHERE id=:id"), {"fk": identity_id, "id": user_id})
    return identity_id

def identity_id_for_user(user_id: int) -> str:
    row = one("SELECT fresh_key_identity_id FROM users WHERE id=:id LIMIT 1", {"id": user_id})
    value = str((row or {}).get("fresh_key_identity_id") or "").strip()
    return value or ensure_identity(user_id)


def _local_project(user_id: int, project_id: int) -> dict[str, Any]:
    project = one("SELECT p.id,p.user_id,p.name,p.website_url,p.fresh_key_project_id FROM projects p WHERE p.id=:pid AND (p.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=p.id AND pm.user_id=:uid)) LIMIT 1", {"pid": project_id, "uid": user_id})
    if not project:
        raise RuntimeError("Projekt v Lindě nenalezen.")
    return project


def register_project(user_id: int, project_id: int) -> dict[str, Any]:
    """Report an existing Linda project to Fresh Key inventory without creating a link."""
    ensure_identity(user_id)
    project = _local_project(user_id, project_id)
    data = _request("POST", "/v1/fresh-key/local-projects/sync", json_body={
        "app_code": "linda",
        "external_user_id": str(user_id),
        "external_project_id": str(project_id),
        "name": str(project.get("name") or ""),
        "website_url": str(project.get("website_url") or ""),
        "role": "owner" if int(project.get("user_id") or 0) == int(user_id) else normalize_role(local_project_role(user_id, project_id)),
    }, user_id=user_id)
    remote = data.get("local_project") or {}
    linked_id = str(remote.get("fresh_key_id") or "").strip()
    if linked_id:
        with db() as conn:
            conn.execute(text("UPDATE projects SET fresh_key_project_id=:fk WHERE id=:pid"), {"fk": linked_id, "pid": project_id})
    return remote


def sync_project_inventory(user_id: int) -> list[dict[str, Any]]:
    ensure_identity(user_id)
    projects = rows(
        "SELECT DISTINCT p.id FROM projects p LEFT JOIN project_members pm ON pm.project_id=p.id "
        "WHERE p.user_id=:uid OR pm.user_id=:uid ORDER BY p.id",
        {"uid": user_id},
    )
    out = []
    for item in projects:
        try:
            out.append(register_project(user_id, int(item.get("id") or 0)))
        except Exception:
            continue
    return out


def ensure_project(user_id: int, project_id: int, fresh_project_id: str = "") -> str:
    """Return an existing Fresh Key link. Never publish a local project implicitly."""
    ensure_identity(user_id)
    project = _local_project(user_id, project_id)
    current = str(project.get("fresh_key_project_id") or "").strip()
    if current and not fresh_project_id:
        return current

    target = str(fresh_project_id or current or "").strip()
    if not target:
        raise RuntimeError("Projekt ještě není odeslaný do Fresh Key. V bloku Propojení ho nejdřív odešli do Fresh Key; potom lze spravovat sdílené konektory a propojení v AI Enginu.")

    # A target ID is supplied only by an explicit Fresh Key linking/OAuth flow.
    # Registering here is therefore not an implicit page-open publish.
    register_project(user_id, project_id)
    data = _request("POST", "/v1/fresh-key/links/sync", json_body={
        "app_code": "linda",
        "external_user_id": str(user_id),
        "external_project_id": str(project_id),
        "name": str(project.get("name") or ""),
        "website_url": str(project.get("website_url") or ""),
        "fresh_project_id": target,
    }, user_id=user_id)
    fresh = str((data.get("link") or data.get("project") or {}).get("id") or "")
    if not fresh:
        raise RuntimeError("Fresh Key nevrátil ID propojení.")
    with db() as conn:
        conn.execute(text("UPDATE projects SET fresh_key_project_id=:fk WHERE id=:pid"), {"fk": fresh, "pid": project_id})
    return fresh


def add_local_project_member(user_id: int, project_id: int, role: str = "editor", relationship: str = "member") -> None:
    with db() as conn:
        conn.execute(text(
            "INSERT INTO project_members(project_id,user_id,role,relationship) VALUES(:pid,:uid,:role,:relationship) "
            "ON DUPLICATE KEY UPDATE role=VALUES(role),relationship=VALUES(relationship)"
        ), {"pid": project_id, "uid": user_id, "role": normalize_role(role), "relationship": normalize_relationship(relationship, role)})


def local_project_role(user_id: int, project_id: int) -> str:
    project = one("SELECT user_id FROM projects WHERE id=:pid LIMIT 1", {"pid": project_id})
    if not project:
        return ""
    if int(project.get("user_id") or 0) == int(user_id):
        return "owner"
    row = one("SELECT role FROM project_members WHERE project_id=:pid AND user_id=:uid LIMIT 1", {"pid": project_id, "uid": user_id})
    if row:
        return normalize_role(str(row.get("role") or ""))
    # Do not grant access only because somebody is an application administrator.
    # But if an old installation already exposes the project to an admin through
    # its legacy ACL and the row was lost during the role migration, repair that
    # explicit access as manager instead of reporting a misleading Fresh Key
    # permission error.  `_local_project` remains the access gate.
    try:
        _local_project(user_id, project_id)
        user = one("SELECT role FROM users WHERE id=:uid LIMIT 1", {"uid": user_id})
        if str((user or {}).get("role") or "").strip().lower() in {"admin", "superadmin"}:
            return "manager"
    except Exception:
        pass
    return ""


def can_manage_local_project(user_id: int, project_id: int) -> bool:
    return local_project_role(user_id, project_id) in {"owner", "manager"}


def can_edit_local_project(user_id: int, project_id: int) -> bool:
    return local_project_role(user_id, project_id) in {"owner", "manager", "editor"}


def local_project_people(user_id: int, project_id: int) -> list[dict[str, Any]]:
    _local_project(user_id, project_id)
    owner = one("SELECT u.id,u.name,u.email FROM projects p JOIN users u ON u.id=p.user_id WHERE p.id=:pid LIMIT 1", {"pid": project_id})
    members = rows(
        "SELECT u.id,u.name,u.email,pm.role,pm.relationship FROM project_members pm JOIN users u ON u.id=pm.user_id "
        "WHERE pm.project_id=:pid AND pm.role<>'owner' ORDER BY u.name,u.email",
        {"pid": project_id},
    )
    invites = rows(
        "SELECT id,email,role,relationship,status FROM project_invitations WHERE project_id=:pid AND status='pending' ORDER BY email",
        {"pid": project_id},
    )
    out=[]
    if owner:
        out.append({"kind":"user","user_id":int(owner["id"]),"name":owner.get("name") or owner.get("email"),"email":str(owner.get("email") or ""),"role":"owner","status":"active"})
    owner_id=int(owner["id"]) if owner else 0
    for m in members:
        if int(m.get("id") or 0)==owner_id: continue
        out.append({"kind":"user","user_id":int(m["id"]),"name":m.get("name") or m.get("email"),"email":str(m.get("email") or ""),"role":normalize_role(str(m.get("role") or "editor")),"relationship":normalize_relationship(str(m.get("relationship") or ""), str(m.get("role") or "")),"status":"active"})
    active_emails={str(x.get("email") or "").lower() for x in out}
    for inv in invites:
        if str(inv.get("email") or "").lower() in active_emails: continue
        out.append({"kind":"invite","invite_id":int(inv["id"]),"name":str(inv.get("email") or ""),"email":str(inv.get("email") or ""),"role":normalize_role(str(inv.get("role") or "editor")),"relationship":normalize_relationship(str(inv.get("relationship") or ""), str(inv.get("role") or "")),"status":"pending"})
    return out


def notify_project_share(user_id: int, project_id: int, email: str, role: str) -> dict[str, Any]:
    project = _local_project(user_id, project_id)
    return _request("POST", "/v1/fresh-key/project-share/notify", json_body={
        "app_code":"linda", "external_user_id":str(user_id), "email":str(email or "").strip().lower(),
        "project_name":str(project.get("name") or "Projekt"), "role":str(role or "member"),
        "action_url": settings.app_url.rstrip("/") + "/?" + urlencode({"page":"projects"}),
    }, user_id=user_id)


def _central_member_for_email(user_id: int, project_id: int, email: str) -> dict[str, Any] | None:
    project = _local_project(user_id, project_id)
    fresh_id = str(project.get("fresh_key_project_id") or "").strip()
    if not fresh_id:
        return None
    try:
        items = _request("GET", f"/v1/fresh-key/links/{fresh_id}/members", user_id=user_id).get("members") or []
        return next((x for x in items if str(x.get("email") or "").strip().lower() == str(email or "").strip().lower()), None)
    except Exception:
        return None


def _sync_member_to_fresh_key(user_id: int, project_id: int, email: str, role: str, *, notify: bool = False) -> None:
    project = _local_project(user_id, project_id)
    fresh_id = str(project.get("fresh_key_project_id") or "").strip()
    if not fresh_id:
        return
    _request("POST", f"/v1/fresh-key/links/{fresh_id}/members", json_body={
        "app_code":"linda", "external_user_id":str(user_id), "email":str(email or "").strip().lower(),
        "role":str(role or "member"), "can_manage_connections":False, "notify":bool(notify),
    }, user_id=user_id)


def share_local_project(user_id: int, project_id: int, email: str, role: str = "editor", relationship: str = "member") -> dict[str, Any]:
    project = _local_project(user_id, project_id)
    if not can_manage_local_project(user_id, project_id):
        raise RuntimeError("Nemáš oprávnění sdílet tento projekt.")
    email=str(email or "").strip().lower()
    legacy_role=str(role or "editor").strip().lower()
    role=normalize_role(legacy_role)
    relationship=normalize_relationship(relationship, legacy_role)
    if not email or "@" not in email: raise RuntimeError("Doplň platný e-mail.")
    owner = one("SELECT u.email FROM projects p JOIN users u ON u.id=p.user_id WHERE p.id=:pid LIMIT 1", {"pid":project_id})
    if owner and str(owner.get("email") or "").strip().lower()==email:
        raise RuntimeError("Vlastník projektu už k projektu přístup má.")
    target=one("SELECT id,name,email FROM users WHERE LOWER(email)=:email LIMIT 1", {"email":email})
    if target:
        add_local_project_member(int(target["id"]), project_id, role, relationship)
        with db() as conn:
            conn.execute(text("UPDATE project_invitations SET status='accepted',updated_at=NOW() WHERE project_id=:pid AND LOWER(email)=:email"), {"pid":project_id,"email":email})
        status="active"
    else:
        with db() as conn:
            conn.execute(text(
                "INSERT INTO project_invitations(project_id,email,role,relationship,status,invited_by) VALUES(:pid,:email,:role,:relationship,'pending',:uid) "
                "ON DUPLICATE KEY UPDATE role=VALUES(role),relationship=VALUES(relationship),status='pending',invited_by=VALUES(invited_by),updated_at=NOW()"
            ), {"pid":project_id,"email":email,"role":role,"relationship":relationship,"uid":user_id})
        status="pending"
    notice={}
    try: notice=notify_project_share(user_id, project_id, email, role)
    except Exception: notice={"email_sent":False}
    try: _sync_member_to_fresh_key(user_id, project_id, email, role, notify=False)
    except Exception: pass
    return {"status":status,"email_sent":bool(notice.get("email_sent")),"recipient_status":notice.get("recipient_status") or ""}


def update_local_project_share(user_id: int, project_id: int, email: str, role: str, relationship: str = "member") -> None:
    if not can_manage_local_project(user_id, project_id): raise RuntimeError("Nemáš oprávnění upravit sdílení.")
    email=str(email or "").strip().lower(); legacy_role=str(role or "editor").strip().lower(); role=normalize_role(legacy_role); relationship=normalize_relationship(relationship, legacy_role)
    target=one("SELECT id FROM users WHERE LOWER(email)=:email LIMIT 1", {"email":email})
    if target:
        add_local_project_member(int(target["id"]), project_id, role, relationship)
    with db() as conn:
        conn.execute(text("UPDATE project_invitations SET role=:role,relationship=:relationship,updated_at=NOW() WHERE project_id=:pid AND LOWER(email)=:email"), {"role":role,"relationship":relationship,"pid":project_id,"email":email})
    central=_central_member_for_email(user_id, project_id, email)
    if central:
        project=_local_project(user_id,project_id); fresh_id=str(project.get("fresh_key_project_id") or "")
        try:
            _request("POST", f"/v1/fresh-key/links/{fresh_id}/members", json_body={"app_code":"linda","external_user_id":str(user_id),"action":"update","identity_id":str(central.get("identity_id") or ""),"role":role,"can_manage_connections":False}, user_id=user_id)
        except Exception: pass


def remove_local_project_share(user_id: int, project_id: int, email: str) -> None:
    project=_local_project(user_id,project_id)
    if not can_manage_local_project(user_id, project_id): raise RuntimeError("Nemáš oprávnění upravit sdílení.")
    email=str(email or "").strip().lower()
    target=one("SELECT id FROM users WHERE LOWER(email)=:email LIMIT 1", {"email":email})
    if target and int(target.get("id") or 0)!=int(project.get("user_id") or 0):
        with db() as conn: conn.execute(text("DELETE FROM project_members WHERE project_id=:pid AND user_id=:uid"), {"pid":project_id,"uid":int(target["id"])})
    with db() as conn: conn.execute(text("DELETE FROM project_invitations WHERE project_id=:pid AND LOWER(email)=:email"), {"pid":project_id,"email":email})
    central=_central_member_for_email(user_id, project_id, email)
    fresh_id=str(project.get("fresh_key_project_id") or "")
    if central and fresh_id:
        try: _request("POST", f"/v1/fresh-key/links/{fresh_id}/members", json_body={"app_code":"linda","external_user_id":str(user_id),"action":"remove","identity_id":str(central.get("identity_id") or "")}, user_id=user_id)
        except Exception: pass


def accept_local_invitations(user_id: int, email: str) -> int:
    email=str(email or "").strip().lower()
    if not email: return 0
    pending=rows("SELECT project_id,role,relationship FROM project_invitations WHERE LOWER(email)=:email AND status='pending'", {"email":email})
    for item in pending:
        add_local_project_member(user_id, int(item["project_id"]), str(item.get("role") or "editor"), str(item.get("relationship") or "member"))
    if pending:
        with db() as conn: conn.execute(text("UPDATE project_invitations SET status='accepted',updated_at=NOW() WHERE LOWER(email)=:email AND status='pending'"), {"email":email})
    return len(pending)


def local_project_for_fresh(fresh_project_id: str) -> dict[str, Any] | None:
    if not fresh_project_id:
        return None
    return one("SELECT * FROM projects WHERE fresh_key_project_id=:fk ORDER BY id LIMIT 1", {"fk": fresh_project_id})



def publish_project(user_id: int, project_id: int) -> dict[str, Any]:
    """Explicitly send this Linda project to Fresh Key.

    Fresh Key 0.8 prevents duplicates: when it finds an existing candidate,
    Linda does not create a second link. It sends the user to the central
    Propojení screen to confirm attach-vs-separate.
    """
    project = _local_project(user_id, project_id)
    if int(project.get("user_id") or 0) == int(user_id):
        try:
            add_local_project_member(user_id, project_id, "owner", "member")
        except Exception:
            pass
    role = local_project_role(user_id, project_id)
    if role not in {"owner", "manager"}:
        raise RuntimeError(f"Projekt může do Fresh Key odeslat pouze Vlastník nebo Správce. Tvoje role je {ROLE_LABELS.get(role, role or 'bez oprávnění')}.")
    ensure_identity(user_id)
    project = _local_project(user_id, project_id)
    current = str(project.get("fresh_key_project_id") or "").strip()
    if current:
        return {"status": "linked", "fresh_key_id": current, "manage_url": manage_url(current)}
    register_project(user_id, project_id)
    data = _request("POST", "/v1/fresh-key/links/sync", json_body={
        "app_code": "linda",
        "external_user_id": str(user_id),
        "external_project_id": str(project_id),
        "name": str(project.get("name") or ""),
        "website_url": str(project.get("website_url") or ""),
        "linking_version": 2,
        "resolve_duplicates": True,
    }, user_id=user_id)
    status = str(data.get("status") or "")
    if status == "match_required":
        return {
            "status": status,
            "fresh_key_id": "",
            "manage_url": str(data.get("manage_url") or manage_url()),
            "candidates": data.get("candidates") or [],
        }
    fresh = str((data.get("link") or data.get("project") or {}).get("id") or "")
    if not fresh:
        raise RuntimeError("Fresh Key nevrátil ID propojení ani návrh existujícího projektu.")
    with db() as conn:
        conn.execute(text("UPDATE projects SET fresh_key_project_id=:fk WHERE id=:pid"), {"fk": fresh, "pid": project_id})
    for person in local_project_people(user_id, project_id):
        if person.get("role") == "owner":
            continue
        try:
            _sync_member_to_fresh_key(user_id, project_id, str(person.get("email") or ""), str(person.get("role") or "member"), notify=False)
        except Exception:
            pass
    return {"status": status or "created", "fresh_key_id": fresh, "manage_url": manage_url(fresh)}


def pending_link_requests(user_id: int) -> list[dict[str, Any]]:
    ensure_identity(user_id)
    data = _request("GET", "/v1/fresh-key/link-requests", params={"status": "pending"}, user_id=user_id)
    return data.get("requests") or []


def accept_link_request(user_id: int, request_id: str, project_id: int) -> dict[str, Any]:
    ensure_identity(user_id)
    project = _local_project(user_id, project_id)
    data = _request("POST", f"/v1/fresh-key/link-requests/{request_id}/accept", json_body={
        "app_code": "linda",
        "external_user_id": str(user_id),
        "external_project_id": str(project_id),
        "name": str(project.get("name") or ""),
        "website_url": str(project.get("website_url") or ""),
        "role": "owner" if int(project.get("user_id") or 0) == int(user_id) else normalize_role(local_project_role(user_id, project_id)),
    }, user_id=user_id)
    req = data.get("request") or {}
    fresh = str(req.get("fresh_key_id") or "")
    if fresh:
        with db() as conn:
            conn.execute(text("UPDATE projects SET fresh_key_project_id=:fk WHERE id=:pid"), {"fk": fresh, "pid": project_id})
        # Mirror Linda-local collaborators only for Engine's read-only overview.
        for person in local_project_people(user_id, project_id):
            if person.get("role") == "owner":
                continue
            try:
                _sync_member_to_fresh_key(user_id, project_id, str(person.get("email") or ""), str(person.get("role") or "member"), notify=False)
            except Exception:
                pass
    return req


def decline_link_request(user_id: int, request_id: str) -> dict[str, Any]:
    ensure_identity(user_id)
    return (_request("POST", f"/v1/fresh-key/link-requests/{request_id}/decline", json_body={
        "app_code": "linda", "external_user_id": str(user_id),
    }, user_id=user_id).get("request") or {})

def available_projects(user_id: int) -> list[dict[str, Any]]:
    ensure_identity(user_id)
    data = _request("GET", "/v1/fresh-key/links", user_id=user_id)
    return data.get("links") or data.get("projects") or []


def start_oauth(user_id: int, project_id: int, provider: str) -> str:
    ensure_identity(user_id)
    fresh_project = ensure_project(user_id, project_id)
    return_url = settings.app_url.rstrip("/") + "/?" + urlencode({"page": "fresh_key_return", "project_id": project_id})
    data = _request("POST", f"/v1/fresh-key/oauth/{provider}/start", json_body={
        "app_code": "linda", "external_user_id": str(user_id), "project_id": fresh_project, "return_url": return_url,
    }, user_id=user_id)
    return str(data.get("authorization_url") or "")


def project_connections(user_id: int, project_id: int) -> list[dict[str, Any]]:
    ensure_identity(user_id)
    fresh_project = ensure_project(user_id, project_id)
    return _request("GET", f"/v1/fresh-key/projects/{fresh_project}/connections", user_id=user_id).get("connections") or []


def project_members(user_id: int, project_id: int) -> list[dict[str, Any]]:
    ensure_identity(user_id)
    fresh_project = ensure_project(user_id, project_id)
    return _request("GET", f"/v1/fresh-key/projects/{fresh_project}/members", user_id=user_id).get("members") or []


def invite_project_member(user_id: int, project_id: int, email: str, role: str = "editor", can_manage_connections: bool = False) -> dict[str, Any]:
    ensure_identity(user_id)
    fresh_project = ensure_project(user_id, project_id)
    normalized_email = str(email or "").strip().lower()
    data = _request("POST", f"/v1/fresh-key/links/{fresh_project}/members", json_body={
        "app_code": "linda",
        "external_user_id": str(user_id),
        "email": normalized_email,
        "role": str(role or "member"),
        "can_manage_connections": bool(can_manage_connections),
    }, user_id=user_id)
    member = data.get("member") or {}
    # If the invited person already has a Linda account, make the local project
    # immediately visible too. Fresh Key remains the shared source of membership.
    local_user = one("SELECT id FROM users WHERE LOWER(email)=:email LIMIT 1", {"email": normalized_email}) if normalized_email else None
    if local_user:
        # Existing Linda account can see the local project immediately. Fresh Key
        # membership may remain pending until that e-mail is centrally verified.
        add_local_project_member(int(local_user["id"]), project_id, str(member.get("role") or role or "member"))
    return member



def project_link_summary(user_id: int, project_id: int) -> dict[str, Any]:
    """Compact Fresh Key state used by the shared Propojení block.

    A central detach/merge may change the Fresh Key ID while Linda still has the
    old pointer cached. Re-register the local project and reconcile that pointer
    instead of breaking the project page.
    """
    ensure_identity(user_id)
    project = _local_project(user_id, project_id)
    fresh_id = str(project.get("fresh_key_project_id") or "").strip()
    if fresh_id:
        links = available_projects(user_id)
        link = next((x for x in links if str(x.get("id") or "") == fresh_id), None)
        if not link:
            remote = register_project(user_id, project_id)
            repaired = str(remote.get("fresh_key_id") or "").strip()
            with db() as conn:
                conn.execute(text("UPDATE projects SET fresh_key_project_id=:fk WHERE id=:pid"), {"fk": repaired or None, "pid": project_id})
            fresh_id = repaired
            if not fresh_id:
                return {"linked": False, "fresh_key_id": "", "applications": [], "members": [], "can_manage": False, "manage_url": manage_url()}
            links = available_projects(user_id)
            link = next((x for x in links if str(x.get("id") or "") == fresh_id), None) or {"id": fresh_id, "bindings": []}
    else:
        return {"linked": False, "fresh_key_id": "", "applications": [], "members": [], "can_manage": False, "manage_url": manage_url()}
    members = _request("GET", f"/v1/fresh-key/links/{fresh_id}/members", user_id=user_id).get("members") or []
    return {
        "linked": True,
        "fresh_key_id": fresh_id,
        "name": str(link.get("name") or ""),
        "applications": [b for b in (link.get("bindings") or []) if str(b.get("app_code") or "") != "ai-engine"],
        "members": members,
        "can_manage": bool(link.get("can_manage_connections")),
        "manage_url": manage_url(fresh_id),
    }


def update_project_member(user_id: int, project_id: int, identity_id: str, role: str = "member", can_manage_connections: bool = False) -> dict[str, Any]:
    fresh_project = ensure_project(user_id, project_id)
    before = project_members(user_id, project_id)
    target = next((m for m in before if str(m.get("identity_id") or "") == str(identity_id or "")), None)
    data = _request("POST", f"/v1/fresh-key/links/{fresh_project}/members", json_body={
        "app_code": "linda", "external_user_id": str(user_id), "action": "update",
        "identity_id": str(identity_id or ""), "role": str(role or "member"),
        "can_manage_connections": bool(can_manage_connections),
    }, user_id=user_id)
    member = data.get("member") or {}
    email = str((target or {}).get("email") or "").strip().lower()
    if email:
        local_user = one("SELECT id FROM users WHERE LOWER(email)=:email LIMIT 1", {"email": email})
        if local_user:
            add_local_project_member(int(local_user["id"]), project_id, str(member.get("role") or role or "member"))
    return member


def remove_project_member(user_id: int, project_id: int, identity_id: str) -> None:
    fresh_project = ensure_project(user_id, project_id)
    # Resolve the target e-mail first so Linda can remove its local membership too.
    members = project_members(user_id, project_id)
    target = next((m for m in members if str(m.get("identity_id") or "") == str(identity_id or "")), None)
    _request("POST", f"/v1/fresh-key/links/{fresh_project}/members", json_body={
        "app_code": "linda", "external_user_id": str(user_id), "action": "remove", "identity_id": str(identity_id or ""),
    }, user_id=user_id)
    email = str((target or {}).get("email") or "").strip().lower()
    if email:
        local_user = one("SELECT id FROM users WHERE LOWER(email)=:email LIMIT 1", {"email": email})
        if local_user:
            project = _local_project(user_id, project_id)
            if int(local_user.get("id") or 0) != int(project.get("user_id") or 0):
                with db() as conn:
                    conn.execute(text("DELETE FROM project_members WHERE project_id=:pid AND user_id=:uid"), {"pid": project_id, "uid": int(local_user["id"])})

def sync_local_connectors(user_id: int, project_id: int) -> list[dict[str, Any]]:
    remote = project_connections(user_id, project_id)
    remote_ids = set()
    with db() as conn:
        for item in remote:
            provider = str(item.get("provider") or "")
            if provider not in SOCIAL_PROVIDERS:
                continue
            connection_id = str(item.get("id") or "")
            if not connection_id:
                continue
            remote_ids.add(connection_id)
            notes = json.dumps({
                "source": "fresh_key", "fresh_key_connection_id": connection_id,
                "provider_group": item.get("provider_group"), "asset_type": item.get("asset_type"),
                "external_id": item.get("external_id"),
            }, ensure_ascii=False)
            existing = conn.execute(text(
                "SELECT id FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND fresh_key_connection_id=:fk LIMIT 1"
            ), {"uid": user_id, "pid": project_id, "fk": connection_id}).mappings().first()
            params = {
                "uid": user_id, "pid": project_id, "provider": provider,
                "name": str(item.get("account_name") or item.get("external_id") or provider)[:190],
                "auth": str(item.get("external_id") or "")[:255], "notes": notes,
                "fk": connection_id, "status": str(item.get("status") or "connected"),
            }
            if existing:
                conn.execute(text(
                    "UPDATE social_connectors SET provider=:provider,account_name=:name,status=:status,auth_user=:auth,"
                    "endpoint_url=CONCAT('freshkey://',:fk),notes=:notes,fresh_key_managed=1,updated_at=NOW() WHERE id=:id"
                ), {**params, "id": int(existing["id"])})
            else:
                conn.execute(text(
                    "INSERT INTO social_connectors(user_id,project_id,provider,account_name,status,client_id,client_secret,access_token,refresh_token,expires_at,endpoint_url,auth_user,notes,fresh_key_connection_id,fresh_key_managed) "
                    "VALUES(:uid,:pid,:provider,:name,:status,NULL,NULL,NULL,NULL,NULL,CONCAT('freshkey://',:fk),:auth,:notes,:fk,1)"
                ), params)
        # Fresh Key pointers that disappeared remotely are marked disconnected,
        # not hard-deleted, so Linda publication history remains intact.
        local = conn.execute(text(
            "SELECT id,fresh_key_connection_id FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND fresh_key_managed=1"
        ), {"uid": user_id, "pid": project_id}).mappings().all()
        for row in local:
            if str(row.get("fresh_key_connection_id") or "") not in remote_ids:
                conn.execute(text("UPDATE social_connectors SET status='disconnected',last_test_status='error',last_test_message='Odpojeno ve Fresh Key',updated_at=NOW() WHERE id=:id"), {"id": int(row["id"])})
    return remote


def runtime_connection(user_id: int, connection_id: str) -> dict[str, Any]:
    identity_id_for_user(user_id)
    data = _request("GET", f"/v1/fresh-key/connections/{connection_id}/runtime", user_id=user_id)
    return data.get("connection") or {}


def hydrate_connector(connector: dict[str, Any]) -> dict[str, Any]:
    if not connector or not int(connector.get("fresh_key_managed") or 0):
        return connector
    connection_id = str(connector.get("fresh_key_connection_id") or "").strip()
    user_id = int(connector.get("user_id") or 0)
    if not connection_id or not user_id:
        return connector
    runtime = runtime_connection(user_id, connection_id)
    out = dict(connector)
    out.update({
        "access_token": runtime.get("access_token") or "",
        "refresh_token": "",  # refresh token never leaves Fresh Key
        "expires_at": runtime.get("expires_at") or connector.get("expires_at"),
        "auth_user": runtime.get("auth_user") or connector.get("auth_user"),
        "client_id": "",
        "client_secret": "",  # provider secrets never leave Fresh Key
        "account_name": runtime.get("account_name") or connector.get("account_name"),
        "provider": runtime.get("provider") or connector.get("provider"),
    })
    return out


def disconnect(user_id: int, connector: dict[str, Any]) -> None:
    connection_id = str(connector.get("fresh_key_connection_id") or "").strip()
    if not connection_id:
        return
    identity_id_for_user(user_id)
    _request("POST", f"/v1/fresh-key/connections/{connection_id}/disconnect", json_body={"external_user_id": str(user_id), "app_code": "linda"}, user_id=user_id)
