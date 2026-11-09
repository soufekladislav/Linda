# Linda 0.4.8.6 – Fresh Key + cron stability

Fresh Key publikace používá opravený Engine 0.7.4.2. Linda navíc opravuje globální cron dotaz `refresh_actions`, který bez user_id ponechával nezadaný SQL parametr `:uid`.

Žádná nová SQL migrace. Zachovej `.env` a `storage/`.
