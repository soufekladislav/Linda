# Linda 0.4.8.0 — Fresh Key

Linda nově používá centrální Fresh Key v AI Enginu pro sociální OAuth a globální projekty.

## Nasazení

1. Zálohuj databázi Lindy a celý adresář aplikace.
2. Nahraj verzi přes stávající instalaci; `.env` a `storage/uploads` zachovej.
3. Ověř, že Linda má stávající hodnoty:

```env
FRESH_ENGINE_URL=https://aiengine.freshcrackers.cz
FRESH_ENGINE_API_KEY=<stávající klíč Lindy do Enginu>
```

4. Pro nová sociální připojení už není potřeba `META_APP_SECRET`, `LINKEDIN_CLIENT_SECRET` ani `GOOGLE_BUSINESS_CLIENT_SECRET` v Lindě. Tyto hodnoty zůstaly v `.env.example` pouze jako legacy fallback pro staré lokální konektory.
5. Restartuj Passenger aplikaci.

Aplikace se při startu pokusí provést pouze aditivní migraci. Pokud DB účet nemá právo `ALTER/CREATE`, spusť ručně `sql-0.4.8.0-fresh-key.sql`.

## Fresh Key přihlášení

Na přihlašovací stránce Linda nabízí `Přihlásit jedním tlačítkem přes Fresh Key`. Uživatel je přesměrován na AI Engine, kde se ověří centrální Fresh účet. Linda dostane pouze jednorázový kód a server-to-server jej vymění za ověřenou globální identitu.

Pokud v Lindě ještě lokální účet neexistuje, po úspěšném centrálním ověření se vytvoří automaticky a naváže na Fresh Key. Pokud už existuje, naváže se bezpečně pouze na tutéž ověřenou identitu. Shoda neověřeného lokálního e-mailu sama o sobě žádný projekt neodemkne.

## Chování

- „Připojit Meta“ otevře Fresh Key OAuth a po autorizaci nabídne Facebook/Instagram assety.
- LinkedIn a Google Business fungují stejným centrálním flow.
- OAuth tokeny se neukládají do Lindy; Linda má jen `fresh_key_connection_id` a runtime token si vyžádá až při testu/publikaci.
- existující lokální konektory nejsou smazány a dál fungují jako legacy fallback,
- existující projekty/obsah/ID nejsou migrovány destruktivně,
- když už existující Fresh projekt používá Linda a další člen projektu ho připojí, nevznikne druhá kopie Lindina projektu; uživatel dostane přístup ke stejným Lindiným pracovním datům přes lokální `project_members`.

- členy globálního projektu lze přidávat přímo z nastavení projektu v Lindě; členství se ukládá do Fresh Key a nevyžaduje návštěvu UI AI Enginu,
