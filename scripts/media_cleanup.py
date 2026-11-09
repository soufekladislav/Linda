from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import rows
from app.media_tools import UPLOAD_ROOT, resolve_upload_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Najde a volitelně smaže osiřelá média Lindy.")
    parser.add_argument("--apply", action="store_true", help="Skutečně mazat. Bez přepínače jen dry-run.")
    parser.add_argument("--older-than", type=int, default=30, help="Mazat jen soubory starší než N dní.")
    parser.add_argument("--include-thumbs", action="store_true", help="Zahrnout cache thumbnailů; jinak je pouze reportuje.")
    args = parser.parse_args()

    refs = rows("SELECT file_path FROM media_assets WHERE file_path IS NOT NULL AND file_path<>''") + rows(
        "SELECT file_path FROM visual_references WHERE file_path IS NOT NULL AND file_path<>''"
    )
    referenced: set[Path] = set()
    for row in refs:
        path = resolve_upload_path(str(row.get("file_path") or ""), allow_scan=False)
        if path:
            referenced.add(path.resolve())

    cutoff = time.time() - max(0, args.older_than) * 86400
    orphaned: list[Path] = []
    thumbs: list[Path] = []
    if UPLOAD_ROOT.exists():
        for path in UPLOAD_ROOT.rglob("*"):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            if ".thumbs" in path.parts:
                thumbs.append(path)
                continue
            try:
                if path.resolve() not in referenced and path.stat().st_mtime <= cutoff:
                    orphaned.append(path)
            except Exception:
                pass

    print(f"REFERENCED={len(referenced)} ORPHANED={len(orphaned)} THUMBS={len(thumbs)} APPLY={args.apply}")
    for path in orphaned[:200]:
        print(f"ORPHAN {path.relative_to(ROOT)} {path.stat().st_size} B")
    if args.include_thumbs:
        for path in thumbs[:200]:
            print(f"THUMB {path.relative_to(ROOT)} {path.stat().st_size} B")

    if args.apply:
        deleted = 0
        for path in orphaned:
            try: path.unlink(); deleted += 1
            except Exception as exc: print(f"DELETE_FAILED {path}: {exc}")
        if args.include_thumbs:
            for path in thumbs:
                try: path.unlink(); deleted += 1
                except Exception as exc: print(f"DELETE_FAILED {path}: {exc}")
        print(f"DELETED={deleted}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
