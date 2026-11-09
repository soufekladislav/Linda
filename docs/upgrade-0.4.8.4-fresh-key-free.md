# Linda 0.4.8.4 – Fresh Key zdarma

Fresh Key je bezplatná spojovací vrstva. Linda automaticky připraví Fresh Key identitu / účet AI Enginu pro přihlášeného uživatele, ale projekt do Fresh Key odešle až po explicitním kliknutí **Odeslat do Fresh Key**.

Vedle toho je vždy **Spravovat propojení**, které otevře centrální Fresh Key v AI Enginu.

Role: Vlastník / Správce / Editor / Čtenář. Vztah Člen týmu / Klient / Agentura je samostatný údaj. Čtenář je read-only.

Aditivní DB změna: `project_members.relationship`. Pokud automatický ALTER selže, použij `sql-0.4.8.4-project-roles.sql`. Produkční `.env` zachovej.
