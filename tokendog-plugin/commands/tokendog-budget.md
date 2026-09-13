---
description: View or set TokenDog spend budgets (daily / session / alert threshold / webhook).
argument-hint: "[--set-daily N] [--set-alert N] [--set-webhook URL]"
allowed-tools: Bash
---

# TokenDog — budget

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tokendog_cli.py budget ${ARGUMENTS}`

Show the budget state above. To change it, re-run with flags, e.g.
`/tokendog:budget --set-daily 25 --set-alert 15` (a leading `$` is fine). When a hard
daily/session budget is set, the budget-enforcement hook denies further tool calls once it's
crossed — except this command, which always runs so the cap can be raised from where it bit.
The hook fails open on any error, so it never blocks work spuriously. Figures are API
list-price attribution across every session on this machine today, not a charge.
