---
name: smoke-skill
description: Deterministic native runtime contract smoke skill.
version: 1.0.0
owner: runtime-contract
triggers:
  - /smoke-skill
tools:
  - contract_echo
risk_level: low
---

Use `contract_echo` to verify native runtime can see optional runtime-local external tools assets and mounted external skills assets.
