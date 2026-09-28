---
name: config
description: Mostra o cambia la config personale di traffic-light-review per questa repo (soglie, file tracciati, riepilogo).
disable-model-invocation: true
argument-hint: "[set <sezione.chiave> <valore> | reset [<sezione.chiave>]]"
---

Esegui con Bash, dalla cartella corrente, questo comando seguito dagli argomenti `$ARGUMENTS`:

```bash
uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/config_cli.py"
```

- Passa ogni argomento tra apici singoli e separato dagli altri: comando (`set` o `reset`), chiave e valore. Il valore va passato intero come un solo argomento, per esempio `set 'files.exclude_dirs' '[data, logs]'`.
- Senza argomenti mostra la config effettiva con la fonte di ogni valore (`default` o `personale`).
- Riporta l'output del comando così com'è, senza commenti né spiegazioni.
- Non modificare mai a mano il file di config e non proporre valori.
