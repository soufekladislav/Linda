# Linda 0.4.8.7 — Fresh Key bez kreditu + role

- Fresh Key/AI Engine účet se může automaticky připravit z Lindy; není placený a nevyžaduje kredity.
- V Propojení je vždy tlačítko **Spravovat propojení** do centrálního AI Enginu.
- Oprávnění jsou sjednocena na Vlastník / Správce / Editor / Čtenář.
- Klient / Agentura / Člen týmu jsou samostatný vztah, ne oprávnění.
- Jen Vlastník a Správce mohou odesílat projekt do Fresh Key a potvrzovat propojení. Čtenář je read-only.
- Staré role member/client/agency se kompatibilně chovají jako Editor.

Produkční `.env` při upgradu zachovej.

- Lokální projekt se do Fresh Key neposílá automaticky při otevření ani při vytvoření. Odeslání je vždy výslovná akce Vlastníka/Správce.
