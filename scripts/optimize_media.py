from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from app.db import db, rows
from app.media_tools import UPLOAD_ROOT, optimize_image_bytes, resolve_upload_path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

def main() -> int:
    parser = argparse.ArgumentParser(description="Bezpečná dávková optimalizace starších médií Lindy včetně Brand Kitu.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--delete-source", action="store_true")
    args = parser.parse_args()
    limit=max(1,min(500,args.limit))
    items=[]
    for table in ("media_assets","visual_references"):
        sql=f"SELECT id,project_id,file_path FROM {table} WHERE file_path IS NOT NULL AND file_path<>''"
        params={"limit":limit}
        if args.project:
            sql += " AND project_id=:pid"; params["pid"]=args.project
        sql += " ORDER BY id ASC LIMIT :limit"
        for row in rows(sql,params):
            row["_table"]=table; items.append(row)
    items=items[:limit]
    changed=saved=0
    for item in items:
        mid=int(item["id"]); table=str(item["_table"]); stored=str(item.get("file_path") or "")
        source=resolve_upload_path(stored)
        label=f"{table}#{mid}"
        if not source or source.suffix.lower() not in IMAGE_EXTS:
            print(f"SKIP {label}: unresolved/non-image {stored}"); continue
        raw=source.read_bytes(); optimized,ext,info=optimize_image_bytes(raw,source.name); delta=len(raw)-len(optimized)
        if not info.get("optimized") or delta<=0:
            print(f"KEEP {label}: {source.name} {len(raw)} B ({info.get('reason','no gain')})"); continue
        print(f"{'APPLY' if args.apply else 'DRY'} {label}: {source.name} {len(raw)} -> {len(optimized)} B, save {delta} B")
        changed+=1; saved+=delta
        if not args.apply: continue
        target_dir=UPLOAD_ROOT/"optimized"; target_dir.mkdir(parents=True,exist_ok=True)
        prefix="media" if table=="media_assets" else "reference"
        target=target_dir/f"{prefix}-{mid}-{hashlib.sha1(optimized).hexdigest()[:10]}.{ext}"; target.write_bytes(optimized)
        rel=str(target.relative_to(ROOT))
        with db() as conn:
            conn.execute(text(f"UPDATE {table} SET file_path=:path WHERE id=:id"),{"path":rel,"id":mid})
        if args.delete_source and source!=target:
            refs=rows("SELECT (SELECT COUNT(*) FROM media_assets WHERE file_path=:p)+(SELECT COUNT(*) FROM visual_references WHERE file_path=:p) c",{"p":stored})
            if refs and int(refs[0].get("c") or 0)==0:
                try: source.unlink(); print(f"  deleted source: {source}")
                except Exception as exc: print(f"  source delete failed: {exc}")
    print(f"SUMMARY: candidates={len(items)} optimized={changed} saved_bytes={saved} apply={args.apply}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
