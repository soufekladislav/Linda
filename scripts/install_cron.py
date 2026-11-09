from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

MARKER='# LINDA_BACKGROUND_WORKER'

def desired_line() -> str:
    root=Path(__file__).resolve().parents[1]
    py=Path(sys.executable)
    return f"*/2 * * * * cd {root} && {py} scripts/run_worker.py >> storage/worker.log 2>&1 {MARKER}"

def current() -> str:
    p=subprocess.run(['crontab','-l'],capture_output=True,text=True)
    return p.stdout if p.returncode==0 else ''

def main() -> int:
    ap=argparse.ArgumentParser(description='Zobrazí nebo nainstaluje jeden bezpečný cron worker Lindy.')
    ap.add_argument('--install',action='store_true')
    ap.add_argument('--remove',action='store_true')
    a=ap.parse_args(); old=current(); lines=[x for x in old.splitlines() if MARKER not in x]
    if a.remove:
        payload='\n'.join(lines).rstrip()+('\n' if lines else '')
        subprocess.run(['crontab','-'],input=payload,text=True,check=True); print('Cron Lindy odstraněn.'); return 0
    line=desired_line()
    if not a.install:
        print('Navržený cron:'); print(line); print('\nPro instalaci spusť: python scripts/install_cron.py --install'); return 0
    lines.append(line); payload='\n'.join(lines).rstrip()+'\n'; subprocess.run(['crontab','-'],input=payload,text=True,check=True)
    print('Cron Lindy nainstalován:'); print(line); return 0

if __name__=='__main__': raise SystemExit(main())
