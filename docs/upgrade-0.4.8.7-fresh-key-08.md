# Linda 0.4.8.7 — Fresh Key 0.8

- `Odeslat do Fresh Key` používá duplicate-safe kontrakt (`linking_version=2`).
- Pokud Engine najde pravděpodobně stejný projekt, Linda nevytvoří další Fresh Key. Uživatel dokončí volbu v `Spravovat propojení`.
- Pokud projekt nemá kandidáta, vznikne standardní jedno-app Propojení.
- Linda umí po centrálním sloučení/odpojení opravit zastaralý `fresh_key_project_id` při dalším načtení bloku Propojení.
- Žádná databázová migrace není potřeba.
