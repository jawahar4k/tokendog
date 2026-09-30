---
description: Lessons captured from sessions in this repo — list, forget one, or share now.
argument-hint: "[--forget FILE] [--share-now] [--index]"
allowed-tools: Bash
---

# TokenDog — session learnings

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tokendog_cli.py learnings ${ARGUMENTS}`

Show the result above. Lessons are captured automatically at compaction and at session end, and
loaded into every session as notes to check against the code. To remove a wrong one of yours, run
`/tokendog:learnings --forget <file>`; it will not be captured again. A wrong *shared* lesson is
fixed or deleted in an ordinary pull request.
