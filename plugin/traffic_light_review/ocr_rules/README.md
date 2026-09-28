# Regole di review di Open Code Review

I file di questa cartella, tranne questo README, sono copiati senza modifiche da
[Open Code Review](https://github.com/alibaba/open-code-review) di Alibaba,
cartella `internal/config/rules/`. Tag e commit di provenienza sono in [`SOURCE.json`](SOURCE.json).

```
Copyright 2026 alibaba/open-code-review Contributors
Licensed under the Apache License, Version 2.0
```

Il testo completo della licenza è in [`LICENSE`](LICENSE). Questi file restano sotto Apache-2.0; il resto di
traffic-light-review è sotto licenza MIT.

traffic-light-review non è un progetto di Alibaba né di Open Code Review. Usa queste regole con un resolver proprio
(`traffic_light_review/rules.py`) che replica quello di OCR: primo pattern che corrisponde in `system_rules.json`,
fallback su `default.md`, `objc.md` per i file `.m` che iniziano come Objective-C, e i livelli utente
`.opencodereview/rule.json` del progetto e `~/.opencodereview/rule.json`.
