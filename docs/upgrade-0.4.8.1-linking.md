# Linda 0.4.8.1 — Fresh Key Propojení

Tato verze navazuje na 0.4.8.0 a opravuje projektový model Fresh Key.

Linda už při pouhém otevření projektu nevytváří nový globální projekt v AI Enginu. Místo toho:

1. nahlásí existující lokální projekt do inventáře Fresh Key;
2. pokud je projekt už propojený, uloží si pouze Fresh Key ID;
3. pokud propojený není, čeká na přiřazení v **AI Engine → Propojení**;
4. OAuth konektory lze spustit až nad hotovým propojením.

Tím se zabrání duplicitám typu „Fresh Crackers v Lindě“ + „Fresh Crackers v Bobanovi“ + další ručně založený projekt v AI Enginu.

## Databáze Lindy

Žádná nová tabulka ani sloupec oproti 0.4.8.0. Stávající `projects.fresh_key_project_id` zůstává pouze ukazatelem na Fresh Key propojení.

## Nasazení

- zachovej produkční `.env`, databázi a `storage/uploads`;
- nahraj soubory 0.4.8.1 přes stávající 0.4.8.0;
- restartuj Passenger;
- otevři stránku Projekty: Linda synchronizuje své existující projekty do inventáře Fresh Key;
- v AI Enginu otevři **Propojení** a spoj odpovídající projekty z jednotlivých aplikací.
