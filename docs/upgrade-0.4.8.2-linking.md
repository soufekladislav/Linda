# Linda 0.4.8.2 — Propojení

V Nastavení projektu je zjednodušený blok **Propojení** s informačním `i`, seznamem připojených AI aplikací a společným seznamem uživatelů. Správce může uživatele přidat/pozvat, změnit roli a odebrat. Existující Linda účet dostane lokální přístup ihned; Fresh Key členství se aktivuje po ověření centrální identity.

## Nasazení
- nahraj soubory přes 0.4.8.1;
- zachovej `.env`, databázi a `storage/`;
- bez nové DB migrace proti 0.4.8.1;
- `FRESH_ENGINE_API_KEY` musí fungovat proti Fresh AI Engine 0.7.2.
