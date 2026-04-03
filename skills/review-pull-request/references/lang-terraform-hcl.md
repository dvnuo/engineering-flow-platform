# Terraform / HCL Review Heuristics

- Identify changes that force replace/destroy on critical resources.
- Review IAM policies for overly broad permissions.
- Check unintended public exposure (network, storage, service endpoints).
- Flag dangerous defaults and missing guardrails.
- Avoid hardcoded environment/account IDs when variable/provider data is safer.
- Assess stateful resource migration risk (renames/moves/import needs).
- Verify lifecycle/meta-arguments are intentional and documented.
