# Cron Directory

## Directory Structure

```
cron/
├── __init__.py
├── scheduler.py            # Main scheduler implementation
├── jobs/                    # Job implementations
│   ├── __init__.py
│   ├── mention_poller.py    # GitHub/Jira/Confluence mention poller
│   ├── heartbeat_check.py   # Periodic health checks
│   └── cleanup_job.py       # Cleanup old data
└── (job definitions)
```

## How It Works

### 1. Scheduler Architecture
```python
# cron/scheduler.py

from typing import Dict, List, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import asyncio

@dataclass
class CronJob:
    """Represents a scheduled job."""
    name: str
    schedule: str              # Cron expression
    function: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    enabled: bool = True
    last_run: datetime = None
    next_run: datetime = None
    max_retries: int = 3
    retry_delay: int = 60     # seconds

class CronScheduler:
    """Main cron scheduler."""
    
    def __init__(self, timezone: str = "UTC"):
        self.timezone = timezone
        self.jobs: Dict[str, CronJob] = {}
        self.running = False
    
    def add_job(self, job: CronJob):
        """Add a new job to the scheduler."""
        ...
    
    def remove_job(self, name: str):
        """Remove a job from the scheduler."""
        ...
    
    def start(self):
        """Start the scheduler."""
        ...
    
    def stop(self):
        """Stop the scheduler."""
        ...
```

### 2. Job Types

#### Interval Jobs
```python
# Run every 5 minutes
job = CronJob(
    name="heartbeat",
    schedule="*/5 * * * *",  # Every 5 minutes
    function=run_heartbeat,
    enabled=True
)
```

#### One-Time Jobs
```python
# Run once at specific time
job = CronJob(
    name="notification",
    schedule="2024-12-31 23:59:00",
    function=send_notification,
    enabled=True
)
```

#### Event-Driven Jobs
```python
# Run when event occurs
job = CronJob(
    name="on_mention",
    schedule="event:mention",
    function=handle_mention,
    enabled=True
)
```

## What Problems It Solves

- **Scheduled Task Automation**: Periodic execution of maintenance tasks
- **Resource Monitoring**: Regular health checks
- **Data Cleanup**: Automatic cleanup of old data
- **External Service Polling**: Check for mentions/PRs/issues
- **Backup Management**: Scheduled backups

## Configuration Options

### Core Cron Configuration (config.yaml)

```yaml
# config.yaml
cron:
  # Scheduler settings
  enabled: true
  timezone: "UTC"
  max_concurrent_jobs: 10
  default_timeout: 300        # seconds
  
  # Job execution settings
  execution:
    retry_count: 3
    retry_delay: 60           # seconds
    timeout_per_job: 300       # seconds
    parallel_execution: true
    max_parallel_jobs: 5
  
  # Logging
  logging:
    level: "INFO"
    file: "logs/cron.log"
    max_size: "100MB"
    backup_count: 5
  
  # Default jobs
  default_jobs:
    - name: "heartbeat_check"
      schedule: "*/5 * * * *"
      enabled: true
    - name: "cleanup"
      schedule: "0 3 * * *"   # 3 AM daily
      enabled: true
    - name: "metrics_report"
      schedule: "0 0 * * 0"    # Weekly on Sunday
      enabled: true
```

### Per-Job Configuration

```yaml
# Mention Poller Job
cron:
  jobs:
    mention_poller:
      name: "mention_poller"
      schedule: "*/5 * * * *"  # Every 5 minutes
      enabled: true
      timeout: 60
      platforms:
        github:
          enabled: true
          repos: ["owner/repo1", "owner/repo2"]
        jira:
          enabled: true
          server: "https://jira.example.com"
        confluence:
          enabled: true
          server: "https://confluence.example.com"
      actions:
        - "execute_command"
        - "notify"
      
    # Cleanup Job
    cleanup:
      name: "cleanup"
      schedule: "0 3 * * *"
      enabled: true
      timeout: 1800           # 30 minutes
      targets:
        - type: "logs"
          retention: "7d"
        - type: "sessions"
          retention: "1h"
        - type: "temp_files"
          retention: "24h"
        - type: "cache"
          retention: "1d"
      
    # Heartbeat Check Job
    heartbeat_check:
      name: "heartbeat_check"
      schedule: "*/5 * * * *"
      enabled: true
      timeout: 30
      checks:
        - "llm_connection"
        - "memory_health"
        - "channel_status"
        - "disk_usage"
      
    # Backup Job
    backup:
      name: "backup"
      schedule: "0 4 * * *"    # 4 AM daily
      enabled: true
      timeout: 3600           # 1 hour
      targets:
        - "database"
        - "config"
        - "memory_store"
      destination: "/backups/opsclaw"
      compression: true
```

### Environment Variables

```bash
# Cron settings
CRON_ENABLED=true
CRON_TIMEZONE=UTC
CRON_MAX_CONCURRENT=10

# Job-specific
MENTION_POLLER_INTERVAL=300
BACKUP_PATH=/backups
```

## How to Run

### Start Scheduler
```bash
# Start cron service
python -m cron

# Start with debug
python -m cron --debug

# Start specific job
python -m cron --job mention_poller
```

### Test Jobs Manually
```bash
# Test a job
python -m cron --run-now heartbeat_check

# Test with dry-run
python -m cron --dry-run cleanup

# List all jobs
python -m cron --list
```

### Test via Code
```python
from cron.scheduler import CronScheduler

scheduler = CronScheduler()

# Add and run job manually
job = scheduler.get_job("mention_poller")
result = job.function()
print(result)
```

## Development Principles

### 1. Job Implementation
```python
# cron/jobs/mention_poller.py

from cron.scheduler import CronJob
from skills.decorator import SkillResult

class MentionPoller:
    """Poll for mentions across platforms."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.github = GitHubClient(config.get("github", {}))
        self.jira = JiraClient(config.get("jira", {}))
        self.confluence = ConfluenceClient(config.get("confluence", {}))
    
    def run(self) -> SkillResult:
        """Execute mention polling."""
        mentions = []
        
        # Poll GitHub
        mentions.extend(self.poll_github())
        
        # Poll Jira
        mentions.extend(self.poll_jira())
        
        # Poll Confluence
        mentions.extend(self.poll_confluence())
        
        # Process mentions
        for mention in mentions:
            self.process_mention(mention)
        
        return SkillResult(
            success=True,
            output=f"Processed {len(mentions)} mentions",
            data={"count": len(mentions)}
        )
    
    def poll_github(self) -> List[Dict]:
        """Poll GitHub for mentions."""
        ...
    
    def poll_jira(self) -> List[Dict]:
        """Poll Jira for mentions."""
        ...
    
    def poll_confluence(self) -> List[Dict]:
        """Poll Confluence for mentions."""
        ...
    
    def process_mention(self, mention: Dict):
        """Process a single mention."""
        ...
```

### 2. Error Handling
```python
class JobError(Exception):
    """Base job error."""
    pass

class JobTimeoutError(JobError):
    """Job execution timed out."""
    pass

class JobRetryError(JobError):
    """Job failed but can be retried."""
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
```

### 3. Testing Standards
```python
class TestMentionPoller:
    def test_poll_github(self):
        """Test GitHub polling."""
        poller = MentionPoller(config)
        mentions = poller.poll_github()
        assert isinstance(mentions, list)
    
    def test_run_success(self):
        """Test job execution."""
        poller = MentionPoller(config)
        result = poller.run()
        assert result.success is True
```

## API Reference

### CronScheduler (cron/scheduler.py)

```python
class CronScheduler:
    """Main scheduler for cron jobs."""
    
    def __init__(self, config: Dict[str, Any] = None):
        ...
    
    def add_job(self, job: CronJob):
        """Add a new job."""
        ...
    
    def remove_job(self, name: str):
        """Remove a job."""
        ...
    
    def get_job(self, name: str) -> CronJob:
        """Get job by name."""
        ...
    
    def list_jobs(self, enabled: bool = None) -> List[CronJob]:
        """List all jobs."""
        ...
    
    def start(self):
        """Start the scheduler."""
        ...
    
    def stop(self):
        """Stop the scheduler."""
        ...
    
    def run_now(self, name: str) -> JobResult:
        """Run job immediately."""
        ...
```

## Troubleshooting

### Jobs Not Running
```bash
# Check scheduler status
python -m cron --status

# Check job list
python -m cron --list --verbose

# Check logs
tail -f logs/cron.log
```

### Job Failures
```bash
# View job history
python -m cron --history

# Run with verbose
python -m cron --job mention_poller --verbose
```

### Performance Issues
```bash
# Check running jobs
ps aux | grep cron

# Check resource usage
top -p $(pgrep -f cron)
```
