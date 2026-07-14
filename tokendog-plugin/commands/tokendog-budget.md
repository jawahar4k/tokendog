---
description: View or set TokenDog spend budgets (daily / session / alert threshold / webhook).
argument-hint: "[--set-daily N] [--set-alert N] [--set-webhook URL]"
allowed-tools: Bash
---

# TokenDog — budget

!`python -m tokendog.report budget ${ARGUMENTS}`

Show the budget state above. To change it, re-run with flags, e.g.
`/tokendog:budget --set-daily 25 --set-alert 15`. When a hard daily/session budget is
set, the budget-enforcement hook denies further tool calls once it's crossed (it fails
open on any error, so it never blocks work spuriously).
