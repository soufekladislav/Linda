# Linda 0.4.8.3 — Fresh Key potvrzované Propojení

## Nové workflow

- **Sdílení lidí** je čistě lokální funkce Lindy a funguje i bez Fresh Key.
- Pokud e-mail už má účet v Lindě, přístup se přidá rovnou do `project_members`; jinak vznikne čekající `project_invitations` a aktivuje se po přihlášení stejným e-mailem.
- Fresh Key slouží pouze ke spojení stejného projektu s dalšími Fresh aplikacemi.
- Projekt se do Fresh Key posílá pouze explicitně tlačítkem **Odeslat do Fresh Key**.
- Fresh Key nespojuje projekty automaticky podle názvu ani URL.
- Návrh z AI Enginu se v Lindě zobrazí jako upozornění a uživatel jej může potvrdit nebo odmítnout.
- Pokud návrh požaduje nový projekt, Linda jej vytvoří až po potvrzení a následně pošle jeho lokální ID do Fresh Key.
- Před propojením/vytvořením Fresh Key zároveň odešle e-mail s odkazem zpět do Lindy.

## Databáze

0.4.8.3 přidává aditivní tabulku `project_invitations`. Aplikace ji zkusí vytvořit sama; při omezených DB právech spusť ručně `sql-0.4.8.3-project-invitations.sql`.

## Upgrade

Zachovej produkční `.env`, databázi a `storage/uploads`. Po nahrání souborů restartuj Passenger.
