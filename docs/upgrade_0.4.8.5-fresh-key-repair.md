# Linda 0.4.8.5 – Fresh Key stabilizační oprava

- opravuje mapování starších rolí `admin` / `superadmin` na projektového Správce,
- před odesláním do Fresh Key samoopraví chybějící owner mirror v `project_members`,
- zachovává pravidlo: Fresh Key může měnit pouze Vlastník nebo Správce,
- `Editor` může pracovat a schvalovat, `Čtenář` pouze číst,
- žádná destruktivní migrace; produkční `.env` zachovat.
