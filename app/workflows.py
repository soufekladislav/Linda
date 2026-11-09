from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from .db import db, one

WORKFLOWS_KEY = "linda_conversion_workflows_v1"
FORMATS_KEY = "linda_post_formats_v1"
SCHEDULES_KEY = "linda_planned_tasks_v1"

DEFAULT_WORKFLOWS: list[dict[str, Any]] = [
    {
        "key": "product_to_social",
        "label": "Produkt → sociální sítě → web",
        "description": "Podpoř existující produkt nebo službu z webu. Sociální obsah vždy vede na konkrétní konverzní URL.",
        "rules": "Vyber relevantní produkt/službu z project_knowledge. Připrav platformově odlišné posty. Každý post musí mít CTA a cílovou URL produktu/služby. Nevytvářej nový článek, pokud není pro vysvětlení produktu skutečně potřeba.",
        "enabled": True,
    },
    {
        "key": "article_to_product",
        "label": "Nový článek → produkt/CTA → sociální sítě",
        "description": "Vytvoř nový článek, uvnitř ho prolinkuj na produkty nebo hlavní CTA a potom článek distribuuj na sítě.",
        "rules": "Nejprve vyber obchodní cíl a cílový produkt/službu. Článek musí obsahovat 2–5 interních odkazů, z toho alespoň jeden na konverzní stránku. Teprve po publikaci článku vytvoř social deriváty vedoucí na skutečnou URL článku.",
        "enabled": True,
    },
    {
        "key": "existing_article_boost",
        "label": "Existující článek → sociální sítě → konverze",
        "description": "Znovu využij existující kvalitní obsah a přiveď z něj návštěvnost dál k produktu nebo poptávce.",
        "rules": "Vyber existující článek z webu, zkontroluj jeho interní odkazy/CTA a připrav distribuční posty. Pokud článek nemá jasnou obchodní cestu, navrhni jeho doplnění před kampaní.",
        "enabled": True,
    },
    {
        "key": "uploaded_video",
        "label": "Nahrané video → post → CTA",
        "description": "Uživatel nahraje video a Linda ho zařadí do plánu, připraví copy a naváže ho na produkt, článek nebo poptávku.",
        "rules": "Použij uživatelské video jako primární kreativní asset. Připrav krátký hook, platformový caption a CTA. Vždy vyber nebo vyžádej cílovou URL z webu; pokud není vhodná, navrhni článek nebo landing page.",
        "enabled": True,
    },
    {
        "key": "campaign_hub",
        "label": "Kampaň → obsahový hub → více sítí",
        "description": "Pro větší téma vytvoř hlavní obsahový bod a kolem něj sérii příspěvků s jedním obchodním cílem.",
        "rules": "Urči hlavní konverzi, vyber cílovou stránku nebo vytvoř článek. Naplánuj více dotyků v čase, ale nepřetěžuj publikum. Měř kliknutí a konverze, ne jen impresi.",
        "enabled": True,
    },
]



DEFAULT_PLANNED_TASKS: list[dict[str, Any]] = [
    {"key":"daily_project_check","label":"Denní kontrola projektu","task":"proactive_review","cadence":"daily","hour":8,"weekday":0,"enabled":True,"project_id":None,"description":"Linda zkontroluje konektory, čekající obsah, staré termíny a stav projektu a upozorní jen na to, co vyžaduje pozornost."},
    {"key":"daily_analytics","label":"Denní synchronizace analytiky","task":"analytics_sync","cadence":"daily","hour":7,"weekday":0,"enabled":True,"project_id":None,"description":"Načte aktuální analytiku z Fresh Core u projektů, kde je WordPress konektor."},
    {"key":"daily_media_cleanup","label":"Průběžný úklid médií","task":"media_maintenance","cadence":"daily","hour":3,"weekday":0,"enabled":True,"project_id":None,"description":"Po malých dávkách optimalizuje starší obrázky a hlídá kapacitu úložiště."},
    {"key":"weekly_brand_check","label":"Týdenní kontrola Brand Kitu","task":"brand_analyze","cadence":"weekly","hour":6,"weekday":1,"enabled":True,"project_id":None,"description":"Jednou týdně znovu ověří Style DNA a vizuální pravidla projektu."},
    {"key":"weekly_project_profile","label":"Týdenní obnova AI profilu","task":"project_profile","cadence":"weekly","hour":6,"weekday":2,"enabled":True,"project_id":None,"description":"Aktualizuje obchodní a obsahový profil projektu podle aktuálních znalostí webu."},
]
DEFAULT_FORMATS: list[dict[str, Any]] = [
    {"key": "single_post", "label": "Post", "platforms": ["facebook", "instagram", "linkedin", "x"], "asset": "image_optional", "enabled": True},
    {"key": "carousel", "label": "Carousel", "platforms": ["facebook", "instagram", "linkedin"], "asset": "multi_image", "enabled": True},
    {"key": "reel", "label": "Reel / krátké video", "platforms": ["facebook", "instagram", "tiktok"], "asset": "video", "enabled": True},
    {"key": "video", "label": "Video", "platforms": ["facebook", "instagram", "linkedin", "tiktok", "x"], "asset": "video", "enabled": True},
    {"key": "story", "label": "Story", "platforms": ["facebook", "instagram"], "asset": "image_or_video", "enabled": True},
    {"key": "blog_promo", "label": "Propagace článku", "platforms": ["facebook", "instagram", "linkedin", "x", "tiktok"], "asset": "article_featured", "enabled": True},
]


def _read_meta(key: str) -> Any:
    row = one("SELECT meta_value FROM app_meta WHERE meta_key=:key LIMIT 1", {"key": key})
    if not row or not row.get("meta_value"):
        return None
    try:
        return json.loads(str(row["meta_value"]))
    except Exception:
        return None


def _write_meta(key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    with db() as conn:
        conn.execute(
            text(
                "INSERT INTO app_meta(meta_key,meta_value) VALUES(:key,:value) "
                "ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"
            ),
            {"key": key, "value": payload},
        )


def conversion_workflows() -> list[dict[str, Any]]:
    value = _read_meta(WORKFLOWS_KEY)
    if isinstance(value, list):
        return value
    return [dict(item) for item in DEFAULT_WORKFLOWS]


def post_formats() -> list[dict[str, Any]]:
    value = _read_meta(FORMATS_KEY)
    if isinstance(value, list):
        return value
    return [dict(item) for item in DEFAULT_FORMATS]


def save_conversion_workflows(items: list[dict[str, Any]]) -> None:
    _write_meta(WORKFLOWS_KEY, items)


def save_post_formats(items: list[dict[str, Any]]) -> None:
    _write_meta(FORMATS_KEY, items)


def workflow_by_key(key: str) -> dict[str, Any] | None:
    for item in conversion_workflows():
        if str(item.get("key") or "") == key:
            return item
    return None


def planned_tasks() -> list[dict[str, Any]]:
    value = _read_meta(SCHEDULES_KEY)
    if isinstance(value, list):
        return value
    return [dict(item) for item in DEFAULT_PLANNED_TASKS]


def save_planned_tasks(items: list[dict[str, Any]]) -> None:
    _write_meta(SCHEDULES_KEY, items)
