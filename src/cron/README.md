# cron/ - Scheduled Tasks

## Runtime automation watcher status

Runtime-side automation watchers are **deprecated/removed**.

- Automation monitoring and polling now run in **Portal** (control plane).
- EFP runtime no longer polls GitHub/Jira/Confluence for automation discovery.
- EFP runtime no longer pulls runtime profile/identity bindings for automation and no longer ingests discovered automation events back to Portal.
- EFP runtime now focuses on task execution dispatched by Portal, especially through `/api/tasks/execute`.

## Compatibility note

`src.cron.automation_watchers` remains as a no-op compatibility shim:

- `is_enabled()` always returns `False`
- `start_automation_watchers()` is a no-op
- `stop_automation_watchers()` is a no-op

This prevents legacy imports from breaking while ensuring polling is inactive.
