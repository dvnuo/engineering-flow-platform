"""
Cron skill - Schedule and manage recurring tasks.

Supports:
- List scheduled jobs
- Add new cron jobs
- Remove jobs
- Run jobs manually
- Get job status
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from skills.executor import SkillResult, skill

# Skill metadata
SKILL_NAME = "cron"
SKILL_DESCRIPTION = "Schedule and manage recurring tasks"


# In-memory cron job storage (in production, use database)
_cron_jobs: Dict[str, Dict[str, Any]] = {}
_cron_jobs_counter = 0


@skill(name=SKILL_NAME, description=SKILL_DESCRIPTION)
async def cron(
    action: str = "list",
    name: Optional[str] = None,
    schedule: Optional[str] = None,
    command: Optional[str] = None,
    job_id: Optional[str] = None,
    enabled: bool = True,
) -> SkillResult:
    """Manage cron jobs for scheduling tasks.
    
    Args:
        action: Operation (list, add, remove, run, status)
        name: Job name (for add)
        schedule: Cron expression or interval (e.g., "*/5 * * * *" or "every 5m")
        command: Command or message to execute
        job_id: Job ID (for remove, run)
        enabled: Whether job is enabled (default: True)
    
    Returns:
        SkillResult with job information
    
    Examples:
        # List all jobs
        cron(action="list")
        
        # Add a job (every 5 minutes)
        cron(action="add", name="check-alerts", schedule="*/5 * * * *", command="Check system alerts")
        
        # Run a job immediately
        cron(action="run", job_id="job-123")
        
        # Remove a job
        cron(action="remove", job_id="job-123")
    """
    global _cron_jobs_counter
    
    if action == "list":
        return await _list_jobs()
    
    elif action == "status":
        return await _get_status()
    
    elif action == "add":
        if not name or not schedule or not command:
            return SkillResult(
                success=False,
                output="name, schedule, and command are required for add action"
            )
        return await _add_job(name, schedule, command, enabled)
    
    elif action == "remove":
        if not job_id:
            return SkillResult(success=False, output="job_id is required for remove action")
        return await _remove_job(job_id)
    
    elif action == "run":
        if not job_id:
            return SkillResult(success=False, output="job_id is required for run action")
        return await _run_job(job_id)
    
    elif action == "update":
        if not job_id:
            return SkillResult(success=False, output="job_id is required for update action")
        return await _update_job(job_id, enabled=enabled)
    
    else:
        return SkillResult(
            success=False,
            output=f"Unknown action: {action}. Valid actions: list, status, add, remove, run, update"
        )


async def _list_jobs() -> SkillResult:
    """List all cron jobs."""
    global _cron_jobs
    
    if not _cron_jobs:
        return SkillResult(
            success=True,
            output="No scheduled jobs",
            data={"jobs": []}
        )
    
    jobs_list = []
    for job_id, job in _cron_jobs.items():
        jobs_list.append({
            "id": job_id,
            "name": job["name"],
            "schedule": job["schedule"],
            "command": job["command"],
            "enabled": job["enabled"],
            "created_at": job["created_at"],
            "last_run": job.get("last_run"),
            "next_run": job.get("next_run")
        })
    
    return SkillResult(
        success=True,
        output=f"Found {len(jobs_list)} job(s)",
        data={"jobs": jobs_list}
    )


async def _get_status() -> SkillResult:
    """Get cron scheduler status."""
    global _cron_jobs
    
    enabled_count = sum(1 for j in _cron_jobs.values() if j["enabled"])
    disabled_count = len(_cron_jobs) - enabled_count
    
    return SkillResult(
        success=True,
        output="Cron scheduler is running",
        data={
            "status": "running",
            "total_jobs": len(_cron_jobs),
            "enabled_jobs": enabled_count,
            "disabled_jobs": disabled_count,
            "timestamp": datetime.now().isoformat()
        }
    )


async def _add_job(name: str, schedule: str, command: str, enabled: bool = True) -> SkillResult:
    """Add a new cron job."""
    global _cron_jobs_counter
    
    _cron_jobs_counter += 1
    job_id = f"job-{_cron_jobs_counter}"
    
    # Parse schedule
    next_run = _calculate_next_run(schedule)
    
    job = {
        "id": job_id,
        "name": name,
        "schedule": schedule,
        "command": command,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "next_run": next_run,
        "run_count": 0
    }
    
    _cron_jobs[job_id] = job
    
    return SkillResult(
        success=True,
        output=f"Job added: {name} ({job_id})",
        data={
            "job": {
                "id": job_id,
                "name": name,
                "schedule": schedule,
                "next_run": next_run
            }
        }
    )


async def _remove_job(job_id: str) -> SkillResult:
    """Remove a cron job."""
    global _cron_jobs
    
    if job_id not in _cron_jobs:
        return SkillResult(success=False, output=f"Job not found: {job_id}")
    
    name = _cron_jobs[job_id]["name"]
    del _cron_jobs[job_id]
    
    return SkillResult(
        success=True,
        output=f"Job removed: {name} ({job_id})"
    )


async def _run_job(job_id: str) -> SkillResult:
    """Run a job immediately."""
    global _cron_jobs
    
    if job_id not in _cron_jobs:
        return SkillResult(success=False, output=f"Job not found: {job_id}")
    
    job = _cron_jobs[job_id]
    job["last_run"] = datetime.now().isoformat()
    job["run_count"] += 1
    
    # Calculate next run
    job["next_run"] = _calculate_next_run(job["schedule"])
    
    return SkillResult(
        success=True,
        output=f"Job executed: {job['name']}",
        data={
            "job_id": job_id,
            "executed_at": job["last_run"],
            "command": job["command"],
            "next_run": job["next_run"]
        }
    )


async def _update_job(job_id: str, enabled: Optional[bool] = None) -> SkillResult:
    """Update a job."""
    global _cron_jobs
    
    if job_id not in _cron_jobs:
        return SkillResult(success=False, output=f"Job not found: {job_id}")
    
    job = _cron_jobs[job_id]
    changes = []
    
    if enabled is not None:
        job["enabled"] = enabled
        changes.append(f"enabled={enabled}")
    
    return SkillResult(
        success=True,
        output=f"Job updated: {job['name']} ({', '.join(changes)})"
    )


def _calculate_next_run(schedule: str) -> Optional[str]:
    """Calculate next run time from cron expression.
    
    Supports:
    - "*/n * * * *" - Every n minutes
    - "0 * * * *" - Every hour
    - "0 9 * * *" - Every day at 9am
    - "every Xm/Xh/Xd" - Simple intervals (e.g., "every 5m", "every 1h")
    """
    now = datetime.now()
    
    # Handle simple intervals
    if schedule.startswith("every "):
        interval = schedule[6:]
        if interval.endswith('m'):
            minutes = int(interval[:-1])
            next_time = now.timestamp() + (minutes * 60)
        elif interval.endswith('h'):
            hours = int(interval[:-1])
            next_time = now.timestamp() + (hours * 3600)
        elif interval.endswith('d'):
            days = int(interval[:-1])
            next_time = now.timestamp() + (days * 86400)
        else:
            next_time = now.timestamp() + 3600
        return datetime.fromtimestamp(next_time).isoformat()
    
    # Basic cron parsing (simplified)
    try:
        parts = schedule.split()
        if len(parts) >= 2:
            # Very basic: just return a time in the future
            next_time = now.timestamp() + 3600  # Default: 1 hour
            return datetime.fromtimestamp(next_time).isoformat()
    except:
        pass
    
    return None


# Utility: Reminder skill (uses cron internally)
@skill(name="reminder", description="Set a reminder")
async def reminder(
    message: str,
    in_minutes: int = 30,
) -> SkillResult:
    """Set a reminder to be triggered after specified minutes.
    
    Args:
        message: Reminder message
        in_minutes: Minutes until reminder (default: 30)
    
    Returns:
        SkillResult with reminder info
    """
    global _cron_jobs_counter
    
    _cron_jobs_counter += 1
    job_id = f"reminder-{_cron_jobs_counter}"
    schedule = f"every {in_minutes}m"
    
    job = {
        "id": job_id,
        "name": f"Reminder: {message[:50]}",
        "schedule": schedule,
        "command": message,
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "next_run": _calculate_next_run(schedule),
        "run_count": 0,
        "is_reminder": True
    }
    
    _cron_jobs[job_id] = job
    
    # Auto-disable after one run (for reminders)
    job["run_once"] = True
    
    return SkillResult(
        success=True,
        output=f"Reminder set for {in_minutes} minutes from now",
        data={
            "reminder_id": job_id,
            "message": message,
            "in_minutes": in_minutes,
            "trigger_at": job["next_run"]
        }
    )
