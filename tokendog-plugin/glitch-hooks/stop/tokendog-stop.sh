#!/bin/bash
# TokenDog stop hook for Glitch — records AUTHORITATIVE token counts.
#
# You do NOT edit any Python. Just make Glitch run this script on `stop`:
#   - Drop this file into your Glitch hooks catalog (hooks/stop/) and `glitch sync`, OR
#   - Copy it next to your other Glitch stop hooks (e.g. examples/hooks/stop/).
#
# It reads the env vars Glitch sets on stop (TOKENS_INPUT / TOKENS_OUTPUT /
# MODEL / AGENT_NAME / GLITCH_RUN_ID) and hands them to TokenDog.
# Fire-and-forget: never blocks, swallows all errors, always exits 0.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${HERE}/tokendog-telemetry.py" </dev/null >/dev/null 2>&1
exit 0
