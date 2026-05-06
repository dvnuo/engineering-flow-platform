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

Use `contract_echo` to verify that the native runtime can see a mounted external tools repo and a mounted external skills repo.
