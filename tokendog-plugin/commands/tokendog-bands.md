---
description: Show where your tokens actually are — context-size bands, concentration, and peak context.
argument-hint: ""
allowed-tools: Bash
---

# TokenDog — bands

!`python -m tokendog.report bands`

Roll-ups by runtime/tool/session/model answer "who spent it". This answers "how big was the
context when it was spent" — usually the more actionable number, because every token in a
context is re-read on every later turn of that session.

Read the headline line first: if a small share of turns carries most of your context tokens,
the win is in splitting or scoping those turns, not in trimming everywhere.
