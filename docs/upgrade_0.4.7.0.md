# Upgrade Linda 0.4.6.9 → 0.4.7.0

## Cíl

0.4.7.0 je první release připravený pro ostré testování obrázků a prvních sociálních konektorů. Databáze se destruktivně nemění a `storage/uploads` se nemaže.

### Co opravuje

- image job se zastaví **ještě před placeným voláním AI Enginu**, pokud v aktivním venv chybí Pillow,
- běžné neprůhledné obrázky žádají Engine rovnou jako JPEG a Linda je následně lokálně ověří, zmenší a vytvoří thumbnail,
- chat běží v `balanced` režimu a zná stav konektorů i vlastní schopnosti,
- Brand Kit už neposílá base64 obrázky do obyčejné textové analýzy; reference se nejprve přečtou přes `image.understand` a teprve potom se skládá Style DNA,
- LinkedIn má 3-legged OAuth pro osobní profil a publikace umí nahrát obrázek přes LinkedIn Images API,
- Facebook při dostupném obrázku publikuje photo post, ne pouze text/link,
- health endpoint hlásí `media_ready` a `pillow`, takže chybějící image runtime už není skrytá chyba.

## DŮLEŽITÉ: proč 0.4.6.9 negeneruje obrázky

`requirements.txt` obsahuje Pillow, ale předchozí upgrade návod po rozbalení ZIPu nespouštěl `pip install -r requirements.txt`. Kód pak správně získal obrázek z AI Enginu, ale lokálně ho nemohl dekódovat/optimalizovat.

## Nasazení

```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz
cp .env .env.before-0.4.7.0
```

Rozbal `linda-0.4.7.0-update.zip` přes současnou aplikaci. Potom **povinně**:

```bash
python -m pip install -r requirements.txt
python -c "from PIL import Image; import PIL; print('Pillow OK', PIL.__version__)"
python -m py_compile app/main.py app/workers.py app/media_tools.py app/legacy_features.py app/manifest.py
python scripts/check_install.py
mkdir -p tmp
touch tmp/restart.txt
```

Health musí vrátit `media_ready: true`:

```bash
curl --max-time 20 -s https://socialmedia.freshcrackers.cz/health
```

Teprve potom spusť placený image acceptance test:

```bash
python scripts/test_engine_image.py --project-id 1
```

Pokud test projde, zkus v UI **jeden** obrázek. Nepouštěj hromadný retry starých image jobů, dokud jeden nový job nedojde do `Dokončeno` a obrázek se neukáže v detailu, Obsahu, Médiích a Kalendáři.

## CloudLinux / LiteSpeed – procesní limit

Současný `stderr.log` obsahuje opakovaně `RuntimeError: can't start new thread` a `fork() failed ... Resource temporarily unavailable`. Linda používá FastAPI přes `a2wsgi`, takže každý LSWSGI child potřebuje vlastní adapter thread. Pro testovací provoz nastav v **cPanel → Set up Python App → Linda → Environment variables**:

```text
LSAPI_CHILDREN=2
```

a aplikaci restartuj. Pokud i AI Engine drží zbytečně mnoho idle procesů, nastav pro Engine pro začátek `LSAPI_CHILDREN=3`. Není to proměnná Lindy v `.env`; musí ji dostat LSAPI process manager.

## LinkedIn OAuth

Do `.env` Lindy doplň:

```text
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...
LINKEDIN_SCOPES=openid profile email w_member_social
LINKEDIN_VERSION=202608
```

Do LinkedIn Developer aplikace zaregistruj přesně callback:

```text
https://socialmedia.freshcrackers.cz/oauth/linkedin/callback
```

Tento základní režim připojí osobní LinkedIn profil. Publikace za firemní Page vyžaduje schválený Community Management přístup a `w_organization_social`; to je další krok po základním ostrém testu.

## Meta a Google

Facebook/Instagram pokračují přes stávající Meta OAuth. Google Business Profile pokračuje přes stávající Google OAuth. X a TikTok jsou v tomto release stále možné přes ručně uložený user token; plný OAuth wizard pro ně není součástí 0.4.7.0.

## Rollback

Release nemá destruktivní migraci. Při rollbacku vrať aplikační soubory 0.4.6.9, ale zachovej aktuální DB, `.env` a `storage/uploads`.
