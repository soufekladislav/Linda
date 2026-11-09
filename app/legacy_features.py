from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import unicodedata
from email.message import EmailMessage
from datetime import datetime, timedelta
from typing import Any
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, quote

import httpx
from sqlalchemy import text

from .config import settings
from .db import db, one, rows
from .fresh_core import from_connector
from .fresh_key import hydrate_connector
from .repositories import queue_job
from .media_tools import resolve_upload_path, file_mime

NETWORK_LABELS = {
    "facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn",
    "x": "X", "tiktok": "TikTok", "google_business": "Google Business Profile", "blog": "Blog / web", "wordpress": "WordPress",
}
DEFAULT_TIMES = {"facebook":"18:30","instagram":"19:00","linkedin":"09:00","google_business":"10:00","x":"12:30","tiktok":"19:30","blog":"08:00"}


def json_obj(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {} if default is None else default
    try:
        return json.loads(str(value))
    except Exception:
        return {} if default is None else default


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        if p.scheme.lower() not in {"http", "https"} or not p.netloc:
            return ""
        qs = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
              if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid"}]
        path = p.path or "/"
        clean = urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(qs), ""))
        return clean.rstrip("/")
    except Exception:
        return ""


def normalize_host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return re.sub(r"^www\.", "", host)
    except Exception:
        return ""


def _words(text_value: str) -> list[str]:
    value = unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", " ", text_value or "").lower())
    parts = re.findall(r"[\wÀ-ž]+", value, re.UNICODE)
    stop = {"který","která","které","jako","jsou","bude","nebo","pro","při","podle","tento","tato","tohle","jejich","jeho","více","také","všechno","jak","co","se","na","do","od","po","ve","za","a","i","o","u","s","z"}
    out=[]
    for w in parts:
        if len(w)>=4 and w not in stop and w not in out:
            out.append(w)
    return out


def tracking_url(url: str, platform: str, activity_id: int | None = None) -> str:
    if not url:
        return ""
    p=urlparse(url)
    q=dict(parse_qsl(p.query, keep_blank_values=True))
    q.update({"utm_source":platform,"utm_medium":"social","utm_campaign":f"linda-{activity_id or 'organic'}"})
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),p.fragment))


def select_links(project_id: int, context: str, requested: list[dict[str,Any]] | None = None,
                 limit: int = 5, revision: int = 0, exclude: str = "") -> list[dict[str,Any]]:
    items=rows("SELECT id,source_type,url,title,summary,content_body,traffic,conversions,meta_json FROM project_knowledge "
               "WHERE project_id=:pid AND status='active' AND url<>'' ORDER BY id DESC", {"pid":project_id})
    by_url={normalize_url(str(x.get("url") or "")):x for x in items if normalize_url(str(x.get("url") or ""))}
    picked=[]; used=set(); ex=normalize_url(exclude)
    for x in requested or []:
        if not isinstance(x,dict): continue
        nu=normalize_url(str(x.get("url") or ""))
        if not nu or nu==ex or nu not in by_url or nu in used: continue
        r=by_url[nu]
        picked.append({"knowledge_id":int(r["id"]),"url":r["url"],"anchor":str(x.get("anchor") or r.get("title") or ""),"title":r.get("title") or "","type":r.get("source_type")})
        used.add(nu)
    words=_words(context)
    scored=[]
    for r in items:
        nu=normalize_url(str(r.get("url") or ""))
        if not nu or nu==ex or nu in used: continue
        title=str(r.get("title") or "").lower(); hay=(title+" "+str(r.get("summary") or "")+" "+str(r.get("content_body") or "")[:1800]).lower()
        score=sum(7 if w in title else 2 for w in words if w in hay)
        score += min(12,int(r.get("conversions") or 0)*3) + min(8,int(r.get("traffic") or 0)/100)
        score += {"product":5,"page":3,"post":1}.get(str(r.get("source_type")),0)
        scored.append((score, (int(r["id"])+revision)%17, r))
    scored.sort(key=lambda x:(-x[0],x[1]))
    for _,__,r in scored:
        if len(picked)>=limit: break
        nu=normalize_url(str(r.get("url") or ""))
        if nu in used: continue
        picked.append({"knowledge_id":int(r["id"]),"url":r["url"],"anchor":r.get("title") or "","title":r.get("title") or "","type":r.get("source_type")})
        used.add(nu)
    return picked[:limit]


def refresh_blog_links(user_id: int, blog_id: int) -> list[dict[str,Any]]:
    b=one("SELECT b.*,pr.user_id FROM blog_posts b JOIN projects pr ON pr.id=b.project_id WHERE b.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))",{"id":blog_id,"uid":user_id})
    if not b: raise RuntimeError("Článek nenalezen.")
    revision=int(b.get("link_revision") or 0)+1
    links=select_links(int(b["project_id"]), f"{b.get('title','')} {b.get('excerpt','')} {re.sub('<[^>]+>',' ',str(b.get('body') or ''))}", [], 5, revision, str(b.get("destination_url") or ""))
    body=str(b.get("body") or "")
    body=re.sub(r'<aside class=["\']linda-related-links["\']>.*?</aside>', '', body, flags=re.I|re.S)
    if links:
        li="".join(f'<li><a href="{x["url"]}">{x.get("anchor") or x.get("title")}</a></li>' for x in links)
        body=body.rstrip()+f'\n<aside class="linda-related-links"><h2>Související obsah</h2><ul>{li}</ul></aside>'
    with db() as conn:
        conn.execute(text("UPDATE blog_posts SET body=:body,internal_links_json=:links,link_revision=:rev WHERE id=:id"),{"body":body,"links":json.dumps(links,ensure_ascii=False),"rev":revision,"id":blog_id})
    return links


def regenerate_post_link(user_id: int, post_id: int) -> str:
    p=one("SELECT p.*,pr.user_id FROM posts p JOIN projects pr ON pr.id=p.project_id WHERE p.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid))",{"id":post_id,"uid":user_id})
    if not p: raise RuntimeError("Příspěvek nenalezen.")
    links=select_links(int(p["project_id"]), f"{p.get('title','')} {p.get('body','')}", [], 8, int(p["id"])+int(datetime.now().timestamp()), str(p.get("destination_url") or ""))
    if not links: raise RuntimeError("Na webu není jiný vhodný aktivní odkaz.")
    pick=links[0]
    with db() as conn:
        conn.execute(text("UPDATE posts SET destination_url=:url,destination_type=:type,destination_ref_id=:ref,destination_status=NULL,destination_checked_at=NULL,publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"),{"url":pick["url"],"type":pick["type"],"ref":pick["knowledge_id"],"id":post_id})
    return str(pick["url"])


def _email_preference(user_id:int, ntype:str)->bool:
    try:
        pref=one("SELECT * FROM notification_preferences WHERE user_id=:uid",{"uid":user_id}) or {}
        if ntype=="job_failed" or ntype=="publication_hold": return bool(pref.get("email_failed",1))
        if ntype=="plan_ready": return bool(pref.get("email_plan_ready",1))
        if ntype=="media_batch": return bool(pref.get("email_media_batch",0))
        return bool(pref.get("email_group_done",1))
    except Exception:
        return ntype not in {"media_batch"}


def _send_notification_email(user_id:int,title:str,message:str,action_url:str="")->bool:
    if not settings.email_notifications: return False
    try:
        user=one("SELECT name,email FROM users WHERE id=:uid AND status='active'",{"uid":user_id})
        if not user or "@" not in str(user.get("email") or ""): return False
        path=Path(settings.sendmail_path)
        if not path.exists(): return False
        msg=EmailMessage(); msg["Subject"]=title; msg["To"]=str(user["email"]); msg["From"]=settings.mail_from or "Linda <no-reply@freshcrackers.cz>"
        link=(settings.app_url.rstrip("/")+"/"+action_url.lstrip("/")) if action_url.startswith("?") else action_url
        msg.set_content(message + (("\n\nOtevřít v Lindě: "+link) if link else ""))
        proc=subprocess.run([str(path),"-t","-i"],input=msg.as_bytes(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        return proc.returncode==0
    except Exception:
        return False


def notify(user_id:int, project_id:int|None, ntype:str, level:str, title:str, message:str,
           action_url:str="", dedupe_key:str="", throttle_hours:int=0) -> int:
    key=(dedupe_key or hashlib.sha1(f"{user_id}:{ntype}:{title}:{message[:160]}".encode()).hexdigest())[:190]
    if throttle_hours:
        old=one("SELECT id,created_at FROM notifications WHERE user_id=:uid AND dedupe_key=:dk ORDER BY id DESC LIMIT 1",{"uid":user_id,"dk":key})
        if old and old.get("created_at") and old["created_at"] > datetime.now()-timedelta(hours=throttle_hours): return int(old["id"])
    with db() as conn:
        result=conn.execute(text("INSERT INTO notifications(user_id,project_id,type,level,title,message,action_url,dedupe_key,is_read) VALUES(:uid,:pid,:type,:level,:title,:message,:url,:dk,0) ON DUPLICATE KEY UPDATE project_id=VALUES(project_id), type=VALUES(type), level=VALUES(level), title=VALUES(title), message=VALUES(message), action_url=VALUES(action_url), is_read=0"),{"uid":user_id,"pid":project_id,"type":ntype,"level":level,"title":title[:190],"message":message,"url":action_url[:500],"dk":key})
        nid=int(result.lastrowid)
    if _email_preference(user_id,ntype) and _send_notification_email(user_id,title,message,action_url):
        try:
            with db() as conn: conn.execute(text("UPDATE notifications SET email_sent_at=NOW() WHERE id=:id"),{"id":nid})
        except Exception: pass
    return nid


def recommended_time(project_id:int, platform:str)->str:
    try:
        r=one("SELECT hour_of_day,COUNT(*) n,AVG(engagements+clicks*2+conversions*8) score FROM content_metrics WHERE project_id=:pid AND provider=:provider AND hour_of_day IS NOT NULL GROUP BY hour_of_day HAVING COUNT(*)>=2 ORDER BY score DESC,n DESC LIMIT 1",{"pid":project_id,"provider":platform})
        if r: return f"{int(r['hour_of_day']):02d}:00"
    except Exception: pass
    return DEFAULT_TIMES.get(platform,"09:00")


def _http_json(method:str,url:str,headers:dict[str,str]|None=None,payload:Any=None,form:dict[str,Any]|None=None,timeout:int=45)->dict[str,Any]:
    try:
        with httpx.Client(timeout=timeout,follow_redirects=True,headers={"User-Agent":"FreshAISocialBot/0.4.5",**(headers or {})}) as client:
            r=client.request(method,url,json=payload if form is None else None,data=form)
        try: data=r.json()
        except Exception: data={}
        return {"ok":r.is_success,"code":r.status_code,"json":data,"raw":r.text[:4000],"headers":dict(r.headers),"error":""}
    except Exception as exc:
        return {"ok":False,"code":0,"json":{},"raw":"","headers":{},"error":str(exc)}


def http_probe(url:str,timeout:int=15)->dict[str,Any]:
    raw=(url or "").strip()
    if raw and not re.match(r"^https?://",raw,re.I): raw="https://"+raw
    if not raw: return {"ok":False,"code":0,"message":"Chybí URL."}
    try:
        with httpx.Client(timeout=timeout,follow_redirects=True,headers={"User-Agent":"FreshAISocialBot/0.4.5"}) as client:
            r=client.head(raw)
            if r.status_code in {405,501}: r=client.get(raw)
        return {"ok":200<=r.status_code<400,"code":r.status_code,"message":f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"ok":False,"code":0,"message":str(exc)}


def _google_business_access_token(c:dict[str,Any])->str:
    token=str(c.get("access_token") or "").strip()
    refresh=str(c.get("refresh_token") or "").strip()
    client_id=str(c.get("client_id") or settings.google_business_client_id or "").strip()
    client_secret=str(c.get("client_secret") or settings.google_business_client_secret or "").strip()
    expires=c.get("expires_at")
    needs_refresh=False
    if isinstance(expires,datetime):
        needs_refresh = expires <= datetime.now() + timedelta(minutes=2)
    if token and not needs_refresh:
        return token
    if not refresh or not client_id or not client_secret:
        return token
    res=_http_json("POST","https://oauth2.googleapis.com/token",form={
        "client_id":client_id,"client_secret":client_secret,"refresh_token":refresh,"grant_type":"refresh_token"
    })
    new_token=str(res.get("json",{}).get("access_token") or "")
    if not res.get("ok") or not new_token:
        return token
    expires_in=int(res.get("json",{}).get("expires_in") or 3600)
    try:
        with db() as conn:
            conn.execute(text("UPDATE social_connectors SET access_token=:token,expires_at=:expires,updated_at=NOW() WHERE id=:id"),{
                "token":new_token,"expires":datetime.now()+timedelta(seconds=max(60,expires_in-60)),"id":int(c.get("id") or 0)
            })
    except Exception:
        pass
    return new_token


def _google_business_ids(c:dict[str,Any])->tuple[str,str]:
    raw=str(c.get("auth_user") or "").strip()
    if "|" not in raw:
        return "",""
    account_id,location_id=raw.split("|",1)
    return account_id.strip().replace("accounts/",""),location_id.strip().replace("locations/","")


def test_connector(c:dict[str,Any])->dict[str,Any]:
    try:
        c = hydrate_connector(c)
    except Exception as exc:
        return {"ok":False,"message":f"Fresh Key: {exc}"}
    provider=str(c.get("provider") or "")
    token=str(c.get("access_token") or "")
    if provider=="wordpress":
        fc=from_connector(c); res=fc.ping(); raw=res.data if isinstance(res.data,dict) else {}
        data=(raw.get("data") if isinstance(raw.get("data"),dict) else raw) if isinstance(raw,dict) else {}
        if res.ok:
            return {"ok":True,"message":"Fresh Core odpovídá"+(f" · {data.get('version')}" if data.get('version') else ""),"capabilities":data}
        code=str((raw.get("code") if isinstance(raw,dict) else "") or "")
        hint=""
        if res.status_code in {401,403} and code in {"rest_forbidden","fresh_core_forbidden"}:
            hint=" · REST přístup odmítl WordPress/security layer. Použij Fresh Core 0.5.2 a ověř READ key."
        return {"ok":False,"message":f"Fresh Core: HTTP {res.status_code} {res.error}{hint}","capabilities":data}
    if provider in {"facebook","instagram"}:
        if not token: return {"ok":False,"message":"Chybí access token."}
        res=_http_json("GET",f"https://graph.facebook.com/me?fields=id,name&access_token={token}")
        return {"ok":bool(res["ok"] and res["json"].get("id")),"message":"Meta token odpověděl OK." if res["ok"] and res["json"].get("id") else f"Meta test selhal: HTTP {res['code']} {res['error'] or res['raw'][:220]}"}
    if provider=="linkedin":
        if not token:return {"ok":False,"message":"Chybí access token."}
        res=_http_json("GET","https://api.linkedin.com/v2/userinfo",{"Authorization":f"Bearer {token}","Accept":"application/json"})
        return {"ok":res["ok"],"message":f"LinkedIn test: HTTP {res['code']}"}
    if provider=="google_business":
        token=_google_business_access_token(c)
        account_id,location_id=_google_business_ids(c)
        if not token:return {"ok":False,"message":"Google Business Profile: chybí OAuth access/refresh token."}
        headers={"Authorization":f"Bearer {token}","Accept":"application/json"}
        if location_id:
            res=_http_json("GET",f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{location_id}?readMask=name,title,websiteUri",headers)
            ok=bool(res["ok"] and res["json"].get("name"))
            title=str(res["json"].get("title") or c.get("account_name") or "profil")
            return {"ok":ok,"message":f"Google Business Profile OK · {title}" if ok else f"Google Business Profile test selhal: HTTP {res['code']} {res['error'] or res['raw'][:240]}","capabilities":{"account_id":account_id,"location_id":location_id}}
        res=_http_json("GET","https://mybusinessaccountmanagement.googleapis.com/v1/accounts",headers)
        ok=bool(res["ok"] and isinstance(res["json"].get("accounts"),list))
        return {"ok":ok,"message":"Google Business účet odpověděl OK. Vyber konkrétní firemní profil." if ok else f"Google Business test selhal: HTTP {res['code']} {res['error'] or res['raw'][:240]}"}
    if provider=="x":
        if not token:return {"ok":False,"message":"X: chybí uživatelský OAuth token."}
        res=_http_json("GET","https://api.x.com/2/users/me?user.fields=id,name,username",{"Authorization":f"Bearer {token}","Accept":"application/json"})
        ok=bool(res["ok"] and (res["json"].get("data") or {}).get("id"))
        return {"ok":ok,"message":f"X OK · @{(res['json'].get('data') or {}).get('username','účet')}" if ok else f"X test selhal: HTTP {res['code']} {res['error'] or res['raw'][:220]}"}
    if provider=="tiktok":
        if not token:return {"ok":False,"message":"TikTok: chybí user access token."}
        res=_http_json("POST","https://open.tiktokapis.com/v2/post/publish/creator_info/query/",{"Authorization":f"Bearer {token}","Content-Type":"application/json; charset=UTF-8","Accept":"application/json"},{})
        err=str((res["json"].get("error") or {}).get("code") or "")
        ok=bool(res["ok"] and err in {"","ok"} and res["json"].get("data"))
        return {"ok":ok,"message":f"TikTok OK · {(res['json'].get('data') or {}).get('creator_username','účet')}" if ok else f"TikTok test selhal: {(res['json'].get('error') or {}).get('message') or 'HTTP '+str(res['code'])}"}
    return {"ok":False,"message":"Neznámý konektor."}


def connector_preflight(user_id:int,project_id:int,provider:str,live:bool=False)->dict[str,Any]:
    c=one("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider=:provider ORDER BY id DESC LIMIT 1",{"uid":user_id,"pid":project_id,"provider":provider})
    if not c:return {"ok":False,"message":f"Chybí konektor {NETWORK_LABELS.get(provider,provider)}.","connector":None}
    if int(c.get("fresh_key_managed") or 0):
        try:
            c = hydrate_connector(c)
        except Exception as exc:
            return {"ok":False,"message":f"Fresh Key: {exc}","connector":c}
    if str(c.get("status") or "") not in {"connected","configured"}:
        return {"ok":False,"message":str(c.get("last_test_message") or "Konektor není připraven."),"connector":c}
    if live:
        result=test_connector(c)
        with db() as conn:
            conn.execute(text("UPDATE social_connectors SET status=:status,last_preflight_at=NOW(),last_preflight_status=:pf,last_preflight_message=:msg WHERE id=:id"),{"status":"connected" if result["ok"] else "error","pf":"ok" if result["ok"] else "error","msg":result["message"][:2000],"id":int(c["id"])})
        return {**result,"connector":c}
    if str(c.get("last_test_status") or "") == "error":
        return {"ok":False,"message":str(c.get("last_test_message") or "Poslední test selhal."),"connector":c}
    return {"ok":True,"message":str(c.get("last_test_message") or "Konektor je připraven."),"connector":c}


def wordpress_categories(user_id:int, project_id:int)->list[dict[str,Any]]:
    c=one("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1",{"uid":user_id,"pid":project_id})
    if not c:return []
    cats=[]
    try:
        cats=json.loads(str(c.get("categories_json") or "[]")) if c.get("categories_json") else []
    except Exception:
        cats=[]
    if not isinstance(cats,list) or not cats:
        try:
            res=from_connector(c).terms("category")
            data=res.data if isinstance(res.data,dict) else {}
            payload=data.get("data") if isinstance(data.get("data"),list) else data.get("items")
            cats=payload if isinstance(payload,list) else (res.data if isinstance(res.data,list) else [])
        except Exception:
            cats=[]
    out=[]
    for cat in cats if isinstance(cats,list) else []:
        if not isinstance(cat,dict):continue
        cid=int(cat.get("id") or cat.get("term_id") or 0)
        if not cid:continue
        out.append({"id":cid,"name":str(cat.get("name") or ""),"slug":str(cat.get("slug") or ""),"description":str(cat.get("description") or ""),"parent":int(cat.get("parent") or 0)})
    return out


def suggest_wordpress_category(project_id:int, categories:list[dict[str,Any]], title:str, body:str)->dict[str,Any]|None:
    usable=[c for c in categories if str(c.get("slug") or "").lower() not in {"uncategorized","nezarazene","nezarazeno"} and str(c.get("name") or "").lower() not in {"uncategorized","nezařazené","nezařazeno"}] or categories
    if not usable:return None
    text=(str(title or "")+" "+re.sub(r"<[^>]+>"," ",str(body or ""))).lower()
    terms=set(_words(text))
    scores={int(c["id"]):0.0 for c in usable}
    by_id={int(c["id"]):c for c in usable}
    for c in usable:
        label=(str(c.get("name") or "")+" "+str(c.get("slug") or "").replace("-"," ")+" "+str(c.get("description") or "")).lower()
        cwords=set(_words(label))
        scores[int(c["id"])]+=8.0*len(terms & cwords)
        # Common Czech editorial buckets should be easy to distinguish even on
        # small sites with sparse category descriptions.
        name=(str(c.get("name") or "")+" "+str(c.get("slug") or "")).lower()
        hints={
            "kari": {"kariéra","kariera","práce","prace","pozice","nábor","nabor","zaměstnan","zamestnan","brigád","brigad"},
            "blog": {"blog","magazín","magazin","novinky","rady","tipy","návod","navod","marketing","eshop","web"},
        }
        for key,words in hints.items():
            if key in name and any(w in text for w in words):scores[int(c["id"])]+=12.0
    # Learn from existing WordPress content already synchronized into knowledge.
    samples=rows("SELECT title,summary,content_body,meta_json FROM project_knowledge WHERE project_id=:pid AND source_type='post' AND status='active' ORDER BY traffic DESC,id DESC LIMIT 80",{"pid":project_id})
    for item in samples:
        hay=(str(item.get("title") or "")+" "+str(item.get("summary") or "")+" "+str(item.get("content_body") or "")[:1200]).lower()
        overlap=len(terms & set(_words(hay)))
        if overlap<=0:continue
        try:meta=json.loads(str(item.get("meta_json") or "{}"))
        except Exception:meta={}
        for cat in meta.get("categories",[]) if isinstance(meta,dict) and isinstance(meta.get("categories"),list) else []:
            if not isinstance(cat,dict):continue
            cid=int(cat.get("id") or 0)
            if cid in scores:scores[cid]+=min(15.0,float(overlap)*1.7)
    chosen=max(usable,key=lambda c:(scores.get(int(c["id"]),0.0),-int(c["id"])))
    return {**chosen,"score":round(scores.get(int(chosen["id"]),0.0),2)}


def media_public_url(media_id:int)->str:
    """Public media URL with a real image extension for WordPress sideloading.

    WordPress media_sideload_image() rejects extension-less URLs. Older Linda
    URLs used /?page=public_media&id=..., so WordPress created the post but
    silently skipped the featured image. Keep the same signature, but expose a
    pretty path ending in .jpg/.png/.webp.
    """
    key=settings.media_signing_key or settings.cron_token or "fresh-ai-social-media"
    sig=hmac.new(key.encode(),f"media:{media_id}".encode(),hashlib.sha256).hexdigest()
    row=one("SELECT file_path FROM media_assets WHERE id=:id AND file_path IS NOT NULL LIMIT 1",{"id":media_id}) or {}
    suffix=Path(str(row.get("file_path") or "")).suffix.lower()
    if suffix not in {".jpg",".jpeg",".png",".webp",".gif"}: suffix=".jpg"
    filename=quote(f"linda-{media_id}{suffix}")
    return f"{settings.app_url.rstrip('/')}/public-media/{media_id}/{sig}/{filename}"


def _latest_media_record(post_id:int)->dict[str,Any] | None:
    return one("SELECT id,file_path,title FROM media_assets WHERE post_id=:id AND file_path IS NOT NULL AND status='generated' ORDER BY id DESC LIMIT 1",{"id":post_id})


def _latest_media(post_id:int)->str:
    m=_latest_media_record(post_id)
    return media_public_url(int(m["id"])) if m else ""


def _linkedin_upload_image(token:str, author:str, media:dict[str,Any], title:str="") -> tuple[str,str]:
    """Upload a Linda image through LinkedIn Images API and return (image_urn, error).

    The Posts API cannot consume Linda's public URL directly as an image asset. LinkedIn
    first requires an Images API upload that yields a urn:li:image:* identifier.
    """
    path=resolve_upload_path(str(media.get("file_path") or ""))
    if not path or not path.is_file():
        return "","LinkedIn: lokální obrázek nebyl nalezen."
    mime=file_mime(path)
    if mime not in {"image/jpeg","image/png","image/gif"}:
        return "",f"LinkedIn: nepodporovaný formát obrázku {mime}."
    headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/json",
        "Accept":"application/json",
        "X-Restli-Protocol-Version":"2.0.0",
        "Linkedin-Version":settings.linkedin_version,
    }
    init=_http_json("POST","https://api.linkedin.com/rest/images?action=initializeUpload",headers,{"initializeUploadRequest":{"owner":author}})
    value=init.get("json",{}).get("value") or {}
    upload_url=str(value.get("uploadUrl") or "")
    image_urn=str(value.get("image") or "")
    if not init.get("ok") or not upload_url or not image_urn:
        return "",f"LinkedIn: nepodařilo se připravit upload obrázku (HTTP {init.get('code')}). {init.get('error') or init.get('raw','')[:220]}"
    try:
        with httpx.Client(timeout=90,follow_redirects=True,headers={"User-Agent":"FreshAISocialBot/0.4.7"}) as client:
            up=client.put(upload_url,content=path.read_bytes(),headers={"Authorization":f"Bearer {token}","Content-Type":mime})
        if not up.is_success:
            return "",f"LinkedIn: upload obrázku selhal (HTTP {up.status_code}). {up.text[:220]}"
    except Exception as exc:
        return "",f"LinkedIn: upload obrázku selhal: {exc}"
    return image_urn,""


def publish_post(post:dict[str,Any],connector:dict[str,Any])->dict[str,Any]:
    provider=str(connector.get("provider") or "")
    body=str(post.get("body") or "").strip(); hashtags=str(post.get("hashtags") or "").strip(); text_body=(body+("\n\n"+hashtags if hashtags else "")).strip()
    dest=str(post.get("destination_url") or "").strip()
    if dest and dest not in text_body:text_body += "\n\n"+dest
    media_record=_latest_media_record(int(post["id"]))
    media_url=media_public_url(int(media_record["id"])) if media_record else ""
    token=str(connector.get("access_token") or "")
    if provider=="facebook":
        page_id=str(connector.get("auth_user") or connector.get("account_name") or "").strip()
        if not token or not page_id:return {"ok":False,"message":"Facebook: chybí Page ID nebo Page Access Token."}
        if media_url:
            res=_http_json("POST",f"https://graph.facebook.com/{page_id}/photos",form={"url":media_url,"caption":text_body,"access_token":token})
        else:
            form={"message":text_body,"access_token":token}
            if dest:form["link"]=dest
            res=_http_json("POST",f"https://graph.facebook.com/{page_id}/feed",form=form)
        ext=str(res["json"].get("post_id") or res["json"].get("id") or ""); ok=bool(res["ok"] and ext)
        return {"ok":ok,"message":f"Facebook publikováno: {ext}" if ok else f"Facebook publish selhal: HTTP {res['code']} {res['error'] or res['raw'][:260]}","response":res,"external_id":ext,"permalink":""}
    if provider=="instagram":
        ig=str(connector.get("auth_user") or "").strip()
        if not token or not ig:return {"ok":False,"message":"Instagram: chybí Business Account ID nebo token."}
        if not media_url:return {"ok":False,"message":"Instagram potřebuje obrázek."}
        create=_http_json("POST",f"https://graph.facebook.com/{ig}/media",form={"image_url":media_url,"caption":text_body,"access_token":token})
        cid=str(create["json"].get("id") or "")
        if not create["ok"] or not cid:return {"ok":False,"message":f"Instagram media container selhal: HTTP {create['code']} {create['raw'][:260]}","response":create}
        pub=_http_json("POST",f"https://graph.facebook.com/{ig}/media_publish",form={"creation_id":cid,"access_token":token})
        ext=str(pub["json"].get("id") or "");ok=bool(pub["ok"] and ext)
        return {"ok":ok,"message":f"Instagram publikováno: {ext}" if ok else f"Instagram publish selhal: HTTP {pub['code']} {pub['raw'][:260]}","response":pub,"external_id":ext,"permalink":""}
    if provider=="linkedin":
        author=str(connector.get("auth_user") or "").strip()
        if not token or not author:return {"ok":False,"message":"LinkedIn: chybí token nebo Author URN."}
        payload={"author":author,"commentary":text_body,"visibility":"PUBLIC","distribution":{"feedDistribution":"MAIN_FEED","targetEntities":[],"thirdPartyDistributionChannels":[]},"lifecycleState":"PUBLISHED","isReshareDisabledByAuthor":False}
        if media_record:
            image_urn,image_error=_linkedin_upload_image(token,author,media_record,str(post.get("title") or media_record.get("title") or ""))
            if image_error:
                return {"ok":False,"message":image_error}
            payload["content"]={"media":{"altText":str(post.get("title") or media_record.get("title") or "Linda vizuál")[:120],"id":image_urn}}
        res=_http_json("POST","https://api.linkedin.com/rest/posts",{"Authorization":f"Bearer {token}","Content-Type":"application/json","X-Restli-Protocol-Version":"2.0.0","Linkedin-Version":settings.linkedin_version,"Accept":"application/json"},payload)
        ext=str(res["headers"].get("x-restli-id") or res["json"].get("id") or "");ok=bool(res["ok"])
        return {"ok":ok,"message":f"LinkedIn publikováno{': '+ext if ext else ''}." if ok else f"LinkedIn publish selhal: HTTP {res['code']} {res['raw'][:260]}","response":res,"external_id":ext,"permalink":""}
    if provider=="google_business":
        token=_google_business_access_token(connector)
        account_id,location_id=_google_business_ids(connector)
        if not token or not account_id or not location_id:
            return {"ok":False,"message":"Google Business Profile: chybí OAuth token nebo vybraný Account/Location ID."}
        payload={"languageCode":"cs-CZ","summary":text_body[:1500],"topicType":"STANDARD"}
        if dest:
            payload["callToAction"]={"actionType":"LEARN_MORE","url":dest}
        if media_url:
            payload["media"]=[{"mediaFormat":"PHOTO","sourceUrl":media_url}]
        res=_http_json("POST",f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/localPosts",{
            "Authorization":f"Bearer {token}","Content-Type":"application/json","Accept":"application/json"
        },payload)
        ext=str(res["json"].get("name") or "")
        ok=bool(res["ok"] and ext)
        return {"ok":ok,"message":f"Google Business Profile publikováno: {ext}" if ok else f"Google Business Profile publish selhal: HTTP {res['code']} {res['error'] or res['raw'][:300]}","response":res,"external_id":ext,"permalink":""}
    if provider=="x":
        if not token:return {"ok":False,"message":"X: chybí uživatelský OAuth token."}
        xtext=text_body if len(text_body)<=280 else text_body[:275]+"…"
        res=_http_json("POST","https://api.x.com/2/tweets",{"Authorization":f"Bearer {token}","Content-Type":"application/json","Accept":"application/json"},{"text":xtext})
        ext=str((res["json"].get("data") or {}).get("id") or "");ok=bool(res["ok"] and ext)
        return {"ok":ok,"message":f"X publikováno: {ext}" if ok else f"X publish selhal: HTTP {res['code']} {res['raw'][:260]}","response":res,"external_id":ext,"permalink":f"https://x.com/i/web/status/{ext}" if ext else ""}
    if provider=="tiktok":
        if not token:return {"ok":False,"message":"TikTok: chybí user access token."}
        if not media_url:return {"ok":False,"message":"TikTok photo post potřebuje obrázek."}
        info=_http_json("POST","https://open.tiktokapis.com/v2/post/publish/creator_info/query/",{"Authorization":f"Bearer {token}","Content-Type":"application/json; charset=UTF-8","Accept":"application/json"},{})
        opts=((info["json"].get("data") or {}).get("privacy_level_options") or [])
        if not info["ok"] or not opts:return {"ok":False,"message":"TikTok Creator Info selhalo.","response":info}
        privacy="PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opts else ("SELF_ONLY" if "SELF_ONLY" in opts else opts[0])
        payload={"post_info":{"title":str(post.get("title") or "")[:90],"description":text_body[:3800],"privacy_level":privacy,"disable_comment":False,"auto_add_music":True,"brand_content_toggle":False,"brand_organic_toggle":True},"source_info":{"source":"PULL_FROM_URL","photo_cover_index":0,"photo_images":[media_url]},"post_mode":"DIRECT_POST","media_type":"PHOTO"}
        res=_http_json("POST","https://open.tiktokapis.com/v2/post/publish/content/init/",{"Authorization":f"Bearer {token}","Content-Type":"application/json; charset=UTF-8","Accept":"application/json"},payload)
        ext=str((res["json"].get("data") or {}).get("publish_id") or "");err=str((res["json"].get("error") or {}).get("code") or "");ok=bool(res["ok"] and ext and err in {"","ok"})
        return {"ok":ok,"message":f"TikTok publikace přijata: {ext}" if ok else f"TikTok publish selhal: {(res['json'].get('error') or {}).get('message') or 'HTTP '+str(res['code'])}","response":res,"external_id":ext,"permalink":""}
    return {"ok":False,"message":"Pro tuto síť není publikace implementována."}


def check_destination(post:dict[str,Any],user_id:int,fresh:bool=False)->dict[str,Any]:
    url=str(post.get("destination_url") or "").strip()
    if not url:return {"ok":True,"message":"Příspěvek nemá povinný odkaz.","url":""}
    checked=post.get("destination_checked_at")
    if not fresh and str(post.get("destination_status") or "")=="ok" and isinstance(checked,datetime) and checked>datetime.now()-timedelta(minutes=30):
        return {"ok":True,"message":"Odkaz byl nedávno ověřen.","url":url}
    if str(post.get("destination_type") or "")=="blog" and post.get("destination_ref_id"):
        b=one("SELECT status,destination_url FROM blog_posts WHERE id=:id AND project_id=:pid",{"id":int(post["destination_ref_id"]),"pid":int(post["project_id"])})
        if not b or str(b.get("status"))!="published" or not b.get("destination_url"):
            return {"ok":False,"message":"Navázaný článek ještě není veřejně publikovaný.","url":url}
        url=tracking_url(str(b["destination_url"]),str(post.get("platform") or "social"),int(post.get("activity_id") or 0) or None)
    wp=one("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1",{"uid":user_id,"pid":int(post["project_id"])})
    ok=False;message=""
    if wp and normalize_host(str(wp.get("endpoint_url") or ""))==normalize_host(url):
        res=from_connector(wp).content_status(url)
        if res.ok and isinstance(res.data,dict):
            ok=bool(res.data.get("visible"));message=str(res.data.get("message") or "")
    if not message:
        probe=http_probe(url);ok=bool(probe["ok"]);message=str(probe["message"])
    with db() as conn:
        conn.execute(text("UPDATE posts SET destination_url=:url,destination_status=:status,destination_checked_at=NOW() WHERE id=:id"),{"url":url,"status":"ok" if ok else "error","id":int(post["id"])})
    return {"ok":ok,"message":"Cílová stránka je veřejně dostupná." if ok else f"Cílová stránka není veřejně dostupná: {message}","url":url}


def hold_publication(post:dict[str,Any],reason:str,message:str,user_id:int)->None:
    same=str(post.get("publication_hold") or "")==reason and str(post.get("publication_hold_message") or "")==message[:500]
    with db() as conn:
        conn.execute(text("UPDATE posts SET publication_hold=:reason,publication_hold_at=NOW(),publication_hold_message=:message WHERE id=:id"),{"reason":reason,"message":message[:500],"id":int(post["id"])})
    if not same:
        provider = 'wordpress' if str(post.get('platform') or '') == 'blog' else str(post.get('platform') or '')
        action=(f"?page=connectors&project_id={int(post['project_id'])}&provider={provider}" if reason=="connector" else f"?page=content&project_id={int(post['project_id'])}")
        notify(user_id,int(post["project_id"]),"publication_hold","warning","Publikace čeká na opravu",f"{post.get('title')}: {message}",action,f"publication-hold:{int(post['id'])}:{reason}",24)


def schedule_due_publications()->list[int]:
    queued=[]
    data=rows("SELECT p.*,pr.user_id FROM posts p JOIN projects pr ON pr.id=p.project_id "
              "WHERE p.status='scheduled' AND p.scheduled_at<=NOW() "
              "AND (p.publication_hold IS NULL OR (p.publication_hold IN ('connector','link') AND p.publication_hold_at<DATE_SUB(NOW(),INTERVAL 30 MINUTE))) "
              "ORDER BY p.scheduled_at ASC LIMIT 100")
    for post in data:
        uid=int(post["user_id"]);pid=int(post["project_id"]);platform=str(post.get("platform") or "")
        # Legacy platform=blog means a web article, not a social connector.
        if platform=="blog":
            pf=connector_preflight(uid,pid,"wordpress",False)
            if not pf["ok"]:
                hold_publication(post,"connector",pf["message"],uid);continue
            dedupe_key=f"wp-publish:post:{int(post['id'])}"
            active=one("SELECT id FROM task_queue WHERE user_id=:uid AND dedupe_key=:dk AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",{"uid":uid,"dk":dedupe_key})
            if active:
                continue
            failed=one("SELECT id,message FROM task_queue WHERE user_id=:uid AND dedupe_key=:dk AND status='failed' ORDER BY id DESC LIMIT 1",{"uid":uid,"dk":dedupe_key})
            if failed:
                hold_publication(post,"worker_error","Předchozí pokus o publikaci selhal. Otevři Frontu úloh, zkontroluj důvod a po opravě použij Zkusit znovu.",uid)
                continue
            jid=queue_job(uid,pid,"wp_publish_post",f"WordPress: {post.get('title')}",{"post_id":int(post["id"]),"force_publish":True},worker_group="publication",priority=100,notify_user=True,dedupe_key=dedupe_key)
        else:
            pf=connector_preflight(uid,pid,platform,False)
            if not pf["ok"]:
                hold_publication(post,"connector",pf["message"],uid);continue
            dest=check_destination(post,uid,False)
            if not dest["ok"]:
                hold_publication(post,"link",dest["message"],uid);continue
            dedupe_key=f"publish:post:{int(post['id'])}"
            active=one("SELECT id FROM task_queue WHERE user_id=:uid AND dedupe_key=:dk AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",{"uid":uid,"dk":dedupe_key})
            if active:
                continue
            failed=one("SELECT id,message FROM task_queue WHERE user_id=:uid AND dedupe_key=:dk AND status='failed' ORDER BY id DESC LIMIT 1",{"uid":uid,"dk":dedupe_key})
            if failed:
                hold_publication(post,"worker_error","Předchozí pokus o publikaci selhal. Otevři Frontu úloh, zkontroluj důvod a po opravě použij Zkusit znovu.",uid)
                continue
            jid=queue_job(uid,pid,"social_publish_post",f"Publikace: {post.get('title')}",{"post_id":int(post["id"])},worker_group="publication",priority=100,notify_user=True,dedupe_key=dedupe_key)
        queued.append(jid)
        with db() as conn:
            conn.execute(text("UPDATE posts SET publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"),{"id":int(post["id"])})
    return queued


def refresh_action_items(user_id:int|None=None)->int:
    params={}; where=""
    if user_id: where=" AND pr.user_id=:uid";params["uid"]=user_id
    if user_id:
        projects=rows("SELECT DISTINCT pr.* FROM projects pr LEFT JOIN project_members pm ON pm.project_id=pr.id AND pm.user_id=:uid WHERE (pr.user_id=:uid OR pm.user_id=:uid)",params)
    else:
        # Global cron/maintenance runs have no user id. Do not leave an unbound
        # :uid placeholder in the JOIN; that caused refresh_actions to fail before
        # any planned task could run.
        projects=rows("SELECT DISTINCT pr.* FROM projects pr",{})
    created=0
    for p in projects:
        uid=int(p["user_id"]);pid=int(p["id"])
        problems=[]
        if not p.get("website_url"):problems.append(("project_web","warning","Doplň web projektu","Linda potřebuje URL webu pro znalosti a odkazy.",f"?page=project_setup&project_id={pid}"))
        failed=rows("SELECT provider,last_test_message FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND last_test_status='error'",{"uid":uid,"pid":pid})
        for c in failed:problems.append((f"connector_{c['provider']}","critical",f"Konektor {NETWORK_LABELS.get(str(c['provider']),c['provider'])} potřebuje opravit",str(c.get("last_test_message") or "Poslední test selhal."),f"?page=connectors&project_id={pid}&provider={c['provider']}"))
        for typ,severity,title,message,url in problems:
            fp=f"{typ}:{pid}"
            old=one("SELECT id FROM action_items WHERE user_id=:uid AND fingerprint=:fp AND status='open' LIMIT 1",{"uid":uid,"fp":fp})
            if old:continue
            with db() as conn:
                conn.execute(text("INSERT INTO action_items(user_id,project_id,source,type,severity,title,message,action_url,fingerprint,status) VALUES(:uid,:pid,'linda',:type,:sev,:title,:msg,:url,:fp,'open')"),{"uid":uid,"pid":pid,"type":typ,"sev":severity,"title":title,"msg":message,"url":url,"fp":fp})
            created+=1
    return created
