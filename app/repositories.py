from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from .db import db, one, rows


def user_by_email(email: str) -> dict[str, Any] | None:
    return one(
        "SELECT id,name,email,password_hash,role,status,created_at FROM users WHERE email=:email LIMIT 1",
        {"email": email},
    )


def user_by_id(user_id: int) -> dict[str, Any] | None:
    return one(
        "SELECT id,name,email,password_hash,role,status,created_at FROM users WHERE id=:id LIMIT 1",
        {"id": user_id},
    )


def list_projects(user_id: int) -> list[dict[str, Any]]:
    return rows(
        "SELECT DISTINCT p.* FROM projects p LEFT JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=:uid "
        "WHERE p.user_id=:uid OR pm.user_id=:uid ORDER BY p.id DESC",
        {"uid": user_id},
    )


def project(user_id: int, project_id: int) -> dict[str, Any] | None:
    return one(
        "SELECT p.* FROM projects p WHERE p.id=:pid AND (p.user_id=:uid OR EXISTS "
        "(SELECT 1 FROM project_members pm WHERE pm.project_id=p.id AND pm.user_id=:uid)) LIMIT 1",
        {"pid": project_id, "uid": user_id},
    )


def project_options(user_id: int) -> list[dict[str, Any]]:
    return rows(
        "SELECT DISTINCT p.id,p.name,p.website_url FROM projects p LEFT JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=:uid "
        "WHERE p.user_id=:uid OR pm.user_id=:uid ORDER BY p.name,p.id",
        {"uid": user_id},
    )


def create_project(user_id: int, values: dict[str, str]) -> int:
    sql = text(
        "INSERT INTO projects(user_id,name,website_url,brand_tone,target_audience,main_offer,"
        "visual_style,content_goals) VALUES(:uid,:name,:website_url,:brand_tone,:target_audience,"
        ":main_offer,:visual_style,:content_goals)"
    )
    with db() as conn:
        result = conn.execute(sql, {"uid": user_id, **values})
        project_id = int(result.lastrowid)
        try:
            conn.execute(text("INSERT IGNORE INTO project_members(project_id,user_id,role) VALUES(:pid,:uid,'owner')"), {"pid": project_id, "uid": user_id})
        except Exception:
            pass
        return project_id


def queue_job(
    user_id: int,
    project_id: int | None,
    job_type: str,
    title: str,
    payload: dict[str, Any] | None = None,
    *,
    worker_group: str = "text",
    priority: int = 50,
    notify_user: bool = True,
    parent_job_id: int | None = None,
    depends_on_job_id: int | None = None,
    dedupe_key: str | None = None,
) -> int:
    payload_obj = payload or {}
    payload_json = json.dumps(payload_obj, ensure_ascii=False)
    with db() as conn:
        # Target-level protection for media generation. It also catches active jobs
        # created by older Linda versions whose dedupe_key used a different format.
        if job_type in {"image_generate_post","image_generate_blog","carousel_slide","image_generate_carousel_slide","free_image_generate"}:
            target_fields = ("post_id", "blog_id", "asset_id", "slide_index")
            target = tuple(payload_obj.get(k) for k in target_fields)
            active = conn.execute(text(
                "SELECT id,type,payload FROM task_queue WHERE user_id=:uid "
                "AND (:pid IS NULL OR project_id=:pid) AND status IN ('queued','running') "
                "AND type IN ('image_generate_post','image_generate_blog','carousel_slide','image_generate_carousel_slide','free_image_generate') "
                "ORDER BY id DESC LIMIT 80"
            ), {"uid": user_id, "pid": project_id}).mappings().all()
            for row in active:
                try:
                    other = json.loads(str(row.get("payload") or "{}"))
                except Exception:
                    other = {}
                if not isinstance(other, dict):
                    continue
                other_target = tuple(other.get(k) for k in target_fields)
                # Carousel jobs are equivalent across the two legacy type names when
                # they point at the same post + slide. Other image jobs must keep type.
                same_type = str(row.get("type") or "") == job_type or ({str(row.get("type") or ""), job_type} <= {"carousel_slide","image_generate_carousel_slide"})
                if same_type and target == other_target and any(v not in (None, "", 0) for v in target):
                    return int(row["id"])
        if dedupe_key:
            existing = conn.execute(
                text(
                    "SELECT id FROM task_queue WHERE user_id=:uid AND dedupe_key=:dk "
                    "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1"
                ),
                {"uid": user_id, "dk": dedupe_key},
            ).scalar()
            if existing:
                return int(existing)
        result = conn.execute(
            text(
                "INSERT INTO task_queue("
                "user_id,project_id,type,title,payload,status,message,progress,priority,worker_group,"
                "notify_user,parent_job_id,depends_on_job_id,attempts,max_attempts,available_at,dedupe_key"
                ") VALUES("
                ":uid,:pid,:type,:title,:payload,'queued','',0,:priority,:worker_group,:notify,"
                ":parent,:depends,0,3,NOW(),:dedupe_key)"
            ),
            {
                "uid": user_id, "pid": project_id, "type": job_type, "title": title,
                "payload": payload_json, "priority": priority, "worker_group": worker_group,
                "notify": 1 if notify_user else 0, "parent": parent_job_id,
                "depends": depends_on_job_id, "dedupe_key": dedupe_key,
            },
        )
        return int(result.lastrowid)


def log_ai_event(user_id: int, project_id: int | None, event: str, message: str, context: Any = None) -> None:
    with db() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_events(user_id,project_id,event,message,context) "
                "VALUES(:uid,:pid,:event,:message,:context)"
            ),
            {
                "uid": user_id,
                "pid": project_id,
                "event": event,
                "message": message,
                "context": json.dumps(context, ensure_ascii=False, default=str) if context is not None else None,
            },
        )


def queue_rows(user_id: int, limit: int = 120) -> list[dict[str, Any]]:
    return rows(
        "SELECT q.*,p.name project_name FROM task_queue q "
        "LEFT JOIN projects p ON p.id=q.project_id "
        "WHERE q.user_id=:uid ORDER BY "
        "FIELD(q.status,'running','queued','failed','done'), q.priority DESC, q.id DESC LIMIT :limit",
        {"uid": user_id, "limit": limit},
    )


def queue_summary(user_id: int) -> dict[str, int]:
    data = rows(
        "SELECT status,COUNT(*) c FROM task_queue WHERE user_id=:uid GROUP BY status",
        {"uid": user_id},
    )
    out = {"queued": 0, "running": 0, "failed": 0, "done": 0}
    for row in data:
        out[str(row["status"])] = int(row["c"])
    return out


def dashboard_stats(user_id: int) -> dict[str, int]:
    statements = {
        "projects": "SELECT COUNT(DISTINCT p.id) FROM projects p LEFT JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=:uid WHERE p.user_id=:uid OR pm.user_id=:uid",
        "activities": "SELECT COUNT(*) FROM marketing_activities WHERE user_id=:uid",
        "draft_posts": "SELECT COUNT(*) FROM posts p JOIN projects pr ON pr.id=p.project_id "
                       "WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid)) AND p.status='draft'",
        "scheduled_posts": "SELECT COUNT(*) FROM posts p JOIN projects pr ON pr.id=p.project_id "
                           "WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid)) AND p.status='scheduled'",
        "open_actions": "SELECT COUNT(*) FROM action_items WHERE user_id=:uid AND status='open'",
        "unread_notifications": "SELECT COUNT(*) FROM notifications WHERE user_id=:uid AND is_read=0",
    }
    out: dict[str, int] = {}
    with db() as conn:
        for key, sql in statements.items():
            try:
                out[key] = int(conn.execute(text(sql), {"uid": user_id}).scalar() or 0)
            except Exception:
                out[key] = 0
    return out


def list_activities(user_id: int, project_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT ma.*,pr.name project_name,"
        "(SELECT COUNT(*) FROM posts p WHERE p.activity_id=ma.id) post_count "
        "FROM marketing_activities ma JOIN projects pr ON pr.id=ma.project_id "
        "WHERE ma.user_id=:uid"
    )
    params: dict[str, Any] = {"uid": user_id}
    if project_id:
        sql += " AND ma.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY ma.id DESC LIMIT 100"
    return rows(sql, params)


def list_connectors(user_id: int, project_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT c.*,pr.name project_name FROM social_connectors c "
        "LEFT JOIN projects pr ON pr.id=c.project_id WHERE c.user_id=:uid"
    )
    params: dict[str, Any] = {"uid": user_id}
    if project_id:
        sql += " AND c.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY pr.name,c.provider,c.id DESC"
    return rows(sql, params)


def connector(user_id: int, connector_id: int) -> dict[str, Any] | None:
    return one(
        "SELECT * FROM social_connectors WHERE id=:id AND user_id=:uid LIMIT 1",
        {"id": connector_id, "uid": user_id},
    )


def update_connector_test(connector_id: int, user_id: int, ok: bool, message: str, capabilities: Any = None) -> None:
    with db() as conn:
        conn.execute(
            text(
                "UPDATE social_connectors SET status=:status,last_test_at=NOW(),"
                "last_test_status=:test_status,last_test_message=:message,capabilities=:capabilities "
                "WHERE id=:id AND user_id=:uid"
            ),
            {
                "status": "connected" if ok else "error",
                "test_status": "ok" if ok else "error",
                "message": message[:2000],
                "capabilities": json.dumps(capabilities, ensure_ascii=False, default=str) if capabilities is not None else None,
                "id": connector_id,
                "uid": user_id,
            },
        )


def content_rows(user_id: int, project_id: int | None = None, limit: int = 120) -> list[dict[str, Any]]:
    # Preserve the legacy Linda relation between posts and generated media.
    # The PHP 3.8 UI always selected the latest generated media asset per post;
    # without this subquery the files existed on disk but post cards had no image id.
    sql = (
        "SELECT p.*,pr.name project_name,"
        "(SELECT m.id FROM media_assets m WHERE m.post_id=p.id AND m.file_path IS NOT NULL AND m.file_path<>'' ORDER BY m.id DESC LIMIT 1) image_id,"
        "(SELECT m.kind FROM media_assets m WHERE m.post_id=p.id AND m.file_path IS NOT NULL AND m.file_path<>'' ORDER BY m.id DESC LIMIT 1) image_kind "
        "FROM posts p JOIN projects pr ON pr.id=p.project_id WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))"
    )
    params: dict[str, Any] = {"uid": user_id, "limit": limit}
    if project_id:
        sql += " AND p.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY COALESCE(p.scheduled_at,p.created_at) DESC,p.id DESC LIMIT :limit"
    return rows(sql, params)


def knowledge_rows(user_id: int, project_id: int | None = None, limit: int = 150) -> list[dict[str, Any]]:
    sql = (
        "SELECT k.*,pr.name project_name FROM project_knowledge k "
        "JOIN projects pr ON pr.id=k.project_id WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))"
    )
    params: dict[str, Any] = {"uid": user_id, "limit": limit}
    if project_id:
        sql += " AND k.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY k.status='active' DESC,k.modified_at DESC,k.id DESC LIMIT :limit"
    return rows(sql, params)


def media_rows(user_id: int, project_id: int | None = None, limit: int = 120) -> list[dict[str, Any]]:
    sql = (
        "SELECT m.*,pr.name project_name,p.title post_title,b.title blog_title FROM media_assets m "
        "JOIN projects pr ON pr.id=m.project_id "
        "LEFT JOIN posts p ON p.id=m.post_id LEFT JOIN blog_posts b ON b.id=m.blog_post_id "
        "WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))"
    )
    params: dict[str, Any] = {"uid": user_id, "limit": limit}
    if project_id:
        sql += " AND m.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY m.id DESC LIMIT :limit"
    return rows(sql, params)


def reference_rows(user_id: int, project_id: int | None = None, limit: int = 120) -> list[dict[str, Any]]:
    sql = (
        "SELECT r.*,pr.name project_name FROM visual_references r "
        "JOIN projects pr ON pr.id=r.project_id WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))"
    )
    params: dict[str, Any] = {"uid": user_id, "limit": limit}
    if project_id:
        sql += " AND r.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY r.id DESC LIMIT :limit"
    return rows(sql, params)


def recent_metrics(user_id: int, project_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT m.*,pr.name project_name FROM content_metrics m "
        "JOIN projects pr ON pr.id=m.project_id WHERE m.user_id=:uid"
    )
    params: dict[str, Any] = {"uid": user_id}
    if project_id:
        sql += " AND m.project_id=:pid"
        params["pid"] = project_id
    sql += " ORDER BY m.metric_date DESC,m.id DESC LIMIT 200"
    try:
        return rows(sql, params)
    except Exception:
        return []

# ---- CRUD/parity helpers ------------------------------------------------------

def update_project(user_id:int, project_id:int, values:dict[str,Any]) -> bool:
    allowed=["name","website_url","brand_tone","target_audience","main_offer","visual_style","content_goals","autopilot_mode","web_publish_mode","brand_style_overrides"]
    parts=[];params={"uid":user_id,"pid":project_id}
    for key in allowed:
        if key in values:
            parts.append(f"{key}=:{key}");params[key]=values[key]
    if not parts:return False
    with db() as conn:
        r=conn.execute(text(f"UPDATE projects SET {', '.join(parts)} WHERE id=:pid AND (user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=projects.id AND pm.user_id=:uid))"),params)
        return bool(r.rowcount)


def delete_project(user_id:int, project_id:int) -> bool:
    # Keep legacy data safety: project is deletable only when it has no generated content.
    counts=one("SELECT (SELECT COUNT(*) FROM posts WHERE project_id=:pid)+(SELECT COUNT(*) FROM blog_posts WHERE project_id=:pid)+(SELECT COUNT(*) FROM marketing_activities WHERE project_id=:pid) c",{"pid":project_id})
    if counts and int(counts.get("c") or 0)>0:
        raise RuntimeError("Projekt obsahuje aktivity nebo obsah a nelze ho bezpečně smazat. Archivuj jeho obsah nebo projekt ponech.")
    with db() as conn:
        r=conn.execute(text("DELETE FROM projects WHERE id=:pid AND user_id=:uid"),{"pid":project_id,"uid":user_id})
        return bool(r.rowcount)


def post_row(user_id:int, post_id:int) -> dict[str,Any]|None:
    return one("SELECT p.*,pr.name project_name FROM posts p JOIN projects pr ON pr.id=p.project_id WHERE p.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))",{"id":post_id,"uid":user_id})


def blog_row(user_id:int, blog_id:int) -> dict[str,Any]|None:
    return one("SELECT b.*,pr.name project_name FROM blog_posts b JOIN projects pr ON pr.id=b.project_id WHERE b.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))",{"id":blog_id,"uid":user_id})


def blog_rows(user_id:int, project_id:int|None=None, limit:int=120)->list[dict[str,Any]]:
    sql=("SELECT b.*,pr.name project_name,"
         "COALESCE(b.featured_media_id,(SELECT m.id FROM media_assets m WHERE m.blog_post_id=b.id AND m.file_path IS NOT NULL AND m.file_path<>'' ORDER BY m.id DESC LIMIT 1)) image_id "
         "FROM blog_posts b JOIN projects pr ON pr.id=b.project_id WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))")
    params={"uid":user_id,"limit":limit}
    if project_id:sql+=" AND b.project_id=:pid";params["pid"]=project_id
    sql+=" ORDER BY b.id DESC LIMIT :limit"
    return rows(sql,params)


def action_rows(user_id:int, limit:int=100)->list[dict[str,Any]]:
    try:return rows("SELECT a.*,pr.name project_name FROM action_items a LEFT JOIN projects pr ON pr.id=a.project_id WHERE a.user_id=:uid AND a.status='open' ORDER BY FIELD(a.severity,'critical','warning','info'),a.id DESC LIMIT :limit",{"uid":user_id,"limit":limit})
    except Exception:return []


def notification_rows(user_id:int,limit:int=150)->list[dict[str,Any]]:
    try:return rows("SELECT * FROM notifications WHERE user_id=:uid ORDER BY id DESC LIMIT :limit",{"uid":user_id,"limit":limit})
    except Exception:return []


def mark_notifications_read(user_id:int)->None:
    with db() as conn:conn.execute(text("UPDATE notifications SET is_read=1 WHERE user_id=:uid"),{"uid":user_id})


def update_post(user_id:int, post_id:int, values:dict[str,Any])->bool:
    allowed=["title","platform","post_type","body","image_prompt","hashtags","status","scheduled_at","destination_url","notes"]
    parts=[];params={"uid":user_id,"id":post_id}
    for key in allowed:
        if key in values:parts.append(f"p.{key}=:{key}");params[key]=values[key]
    if not parts:return False
    with db() as conn:
        r=conn.execute(text("UPDATE posts p JOIN projects pr ON pr.id=p.project_id SET "+", ".join(parts)+" WHERE p.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))"),params)
        return bool(r.rowcount)


def duplicate_post(user_id:int,post_id:int)->int:
    p=post_row(user_id,post_id)
    if not p:raise RuntimeError("Příspěvek nenalezen.")
    with db() as conn:
        r=conn.execute(text("INSERT INTO posts(project_id,plan_id,topic_id,activity_id,platform,post_type,title,body,image_prompt,carousel_slides,hashtags,recommended_time,status,scheduled_at,destination_url,destination_type,destination_ref_id,notes) VALUES(:project_id,:plan_id,:topic_id,:activity_id,:platform,:post_type,:title,:body,:image_prompt,:carousel_slides,:hashtags,:recommended_time,'draft',NULL,:destination_url,:destination_type,:destination_ref_id,:notes)"),{
            "project_id":p.get("project_id"),"plan_id":p.get("plan_id"),"topic_id":p.get("topic_id"),"activity_id":p.get("activity_id"),"platform":p.get("platform"),"post_type":p.get("post_type"),"title":f"{p.get('title') or 'Post'} – varianta","body":p.get("body"),"image_prompt":p.get("image_prompt"),"carousel_slides":p.get("carousel_slides"),"hashtags":p.get("hashtags"),"recommended_time":p.get("recommended_time"),"destination_url":p.get("destination_url"),"destination_type":p.get("destination_type"),"destination_ref_id":p.get("destination_ref_id"),"notes":p.get("notes")})
        new_id=int(r.lastrowid)
        media=conn.execute(text("SELECT * FROM media_assets WHERE post_id=:id AND file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC LIMIT 1"),{"id":post_id}).mappings().first()
        if media:
            conn.execute(text("INSERT INTO media_assets(project_id,post_id,prompt,file_path,provider,status,kind,title) VALUES(:pid,:post,:prompt,:path,:provider,:status,:kind,:title)"),{
                "pid":p.get("project_id"),"post":new_id,"prompt":media.get("prompt"),"path":media.get("file_path"),"provider":media.get("provider"),"status":media.get("status"),"kind":media.get("kind"),"title":media.get("title")})
        return new_id


def delete_post(user_id:int,post_id:int)->bool:
    p=post_row(user_id,post_id)
    if not p:return False
    with db() as conn:
        conn.execute(text("DELETE FROM media_assets WHERE post_id=:id"),{"id":post_id})
        r=conn.execute(text("DELETE FROM posts WHERE id=:id"),{"id":post_id})
        return bool(r.rowcount)


def update_blog(user_id:int,blog_id:int,values:dict[str,Any])->bool:
    b=blog_row(user_id,blog_id)
    if not b:return False
    allowed=["title","slug","excerpt","body","seo_title","seo_description","social_teaser","image_prompt","status","wp_category_id","wp_category_name"]
    parts=[];params={"id":blog_id}
    for key in allowed:
        if key in values:parts.append(f"{key}=:{key}");params[key]=values[key]
    if not parts:return False
    with db() as conn:r=conn.execute(text("UPDATE blog_posts SET "+", ".join(parts)+" WHERE id=:id"),params);return bool(r.rowcount)


def create_or_update_connector(user_id:int, values:dict[str,Any], connector_id:int|None=None)->int:
    if connector_id:
        with db() as conn:
            conn.execute(text("UPDATE social_connectors SET project_id=:project_id,provider=:provider,account_name=:account_name,status='configured',client_id=:client_id,client_secret=:client_secret,access_token=:access_token,refresh_token=:refresh_token,expires_at=:expires_at,endpoint_url=:endpoint_url,auth_user=:auth_user,notes=:notes,last_test_status=NULL,last_test_message=NULL,updated_at=NOW() WHERE id=:id AND user_id=:uid"),{"id":connector_id,"uid":user_id,**values})
        return connector_id
    with db() as conn:
        r=conn.execute(text("INSERT INTO social_connectors(user_id,project_id,provider,account_name,status,client_id,client_secret,access_token,refresh_token,expires_at,endpoint_url,auth_user,notes) VALUES(:uid,:project_id,:provider,:account_name,'configured',:client_id,:client_secret,:access_token,:refresh_token,:expires_at,:endpoint_url,:auth_user,:notes)"),{"uid":user_id,**values})
        return int(r.lastrowid)


def delete_connector(user_id:int,connector_id:int)->bool:
    with db() as conn:r=conn.execute(text("DELETE FROM social_connectors WHERE id=:id AND user_id=:uid"),{"id":connector_id,"uid":user_id});return bool(r.rowcount)


def save_reference(user_id:int,project_id:int,title:str,notes:str,file_path:str)->int:
    if not project(user_id,project_id):raise RuntimeError("Projekt nenalezen.")
    with db() as conn:
        r=conn.execute(text("INSERT INTO visual_references(project_id,title,notes,file_path) VALUES(:pid,:title,:notes,:path)"),{"pid":project_id,"title":title[:190],"notes":notes,"path":file_path})
        return int(r.lastrowid)


def delete_reference(user_id:int,reference_id:int)->dict[str,Any]|None:
    ref=one("SELECT r.* FROM visual_references r JOIN projects pr ON pr.id=r.project_id WHERE r.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))",{"id":reference_id,"uid":user_id})
    if not ref:return None
    with db() as conn:conn.execute(text("DELETE FROM visual_references WHERE id=:id"),{"id":reference_id})
    return ref


def chat_events(user_id:int, project_id:int, limit:int=40)->list[dict[str,Any]]:
    try:
        data=rows(
            "SELECT id,event,message,context,created_at FROM ai_events "
            "WHERE user_id=:uid AND project_id=:pid AND event IN ('chat_user','chat_linda') "
            "ORDER BY id DESC LIMIT :limit",
            {"uid":user_id,"pid":project_id,"limit":limit},
        )
        data.reverse()
        return data
    except Exception:
        return []


def connected_platforms(user_id:int, project_id:int)->list[str]:
    try:
        data=rows(
            "SELECT DISTINCT provider FROM social_connectors WHERE user_id=:uid AND project_id=:pid "
            "AND provider<>'wordpress' AND status IN ('connected','configured') ORDER BY provider",
            {"uid":user_id,"pid":project_id},
        )
        return [str(x.get('provider') or '') for x in data if x.get('provider')]
    except Exception:
        return []


def all_users()->list[dict[str,Any]]:
    return rows("SELECT id,name,email,role,status,created_at,created_by FROM users ORDER BY id DESC")


def notification_preferences(user_id:int)->dict[str,Any]:
    defaults={"email_group_done":1,"email_failed":1,"email_plan_ready":1,"email_media_batch":0}
    try:
        row=one("SELECT * FROM notification_preferences WHERE user_id=:uid",{"uid":user_id})
        if row: defaults.update(row)
    except Exception: pass
    return defaults


def save_notification_preferences(user_id:int, values:dict[str,Any])->None:
    data={k:1 if values.get(k) else 0 for k in ("email_group_done","email_failed","email_plan_ready","email_media_batch")}
    with db() as conn:
        conn.execute(text("INSERT INTO notification_preferences(user_id,email_group_done,email_failed,email_plan_ready,email_media_batch) VALUES(:uid,:email_group_done,:email_failed,:email_plan_ready,:email_media_batch) ON DUPLICATE KEY UPDATE email_group_done=VALUES(email_group_done),email_failed=VALUES(email_failed),email_plan_ready=VALUES(email_plan_ready),email_media_batch=VALUES(email_media_batch)"),{"uid":user_id,**data})
