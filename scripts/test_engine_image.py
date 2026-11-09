from __future__ import annotations

import argparse, base64, json, hashlib, sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
import httpx

from app.engine import engine_client
from app.media_tools import validate_image_bytes, optimize_image_bytes


def main() -> int:
    ap=argparse.ArgumentParser(description='Ověří image.generate přes Fresh AI Engine.')
    ap.add_argument('--project-id', type=int, default=1)
    ap.add_argument('--prompt', default='Jednoduchý čistý marketingový obrázek: zelený abstraktní růstový graf na světlém pozadí, bez textu a bez loga.')
    args=ap.parse_args()
    res=engine_client.run(project_id=args.project_id,task_type='image_generation',operation='image.generate',quality='economy',prompt=args.prompt,context={},input_data={'diagnostic':True})
    out={'ok':res.ok,'audit':res.audit(),'result_kind':type(res.result).__name__}
    if not res.ok:
        out['error']=res.error; print(json.dumps(out,ensure_ascii=False,indent=2,default=str)); return 2
    data=res.result if isinstance(res.result,dict) else {'url':res.result} if isinstance(res.result,str) else {}
    raw=str(data.get('base64') or data.get('b64_json') or '')
    url=str(data.get('url') or data.get('image_url') or '')
    content=b''; ext='png'
    if raw:
        if raw.startswith('data:'):
            head,raw=raw.split(',',1); ext='jpg' if 'jpeg' in head else ('webp' if 'webp' in head else 'png')
        content=base64.b64decode(raw)
    elif url:
        rr=httpx.get(url,timeout=120,follow_redirects=True); rr.raise_for_status(); content=rr.content
        c=rr.headers.get('content-type',''); ext='jpg' if 'jpeg' in c else ('webp' if 'webp' in c else 'png')
    else:
        out['result_preview']=str(res.result)[:1500]; print(json.dumps(out,ensure_ascii=False,indent=2,default=str)); return 3
    meta=validate_image_bytes(content, 'image/jpeg' if ext=='jpg' else f'image/{ext}')
    optimized, optimized_ext, optimization=optimize_image_bytes(content, f'test.{ext}')
    optimized_meta=validate_image_bytes(optimized, 'image/jpeg' if optimized_ext=='jpg' else f'image/{optimized_ext}')
    root=Path(__file__).resolve().parents[1]/'storage'/'diagnostics'; root.mkdir(parents=True,exist_ok=True)
    path=root/f"engine-image-test-{hashlib.sha1(optimized).hexdigest()[:10]}.{optimized_ext}"; path.write_bytes(optimized)
    out.update({'saved_to':str(path),'bytes':len(optimized),'source_meta':meta,'stored_meta':optimized_meta,'optimization':optimization})
    print(json.dumps(out,ensure_ascii=False,indent=2,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
