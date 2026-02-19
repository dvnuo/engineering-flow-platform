# skills/ - Skill Registry and Management

## Structure

```
skills/
├── registry.py    # Skill loading, matching, and discovery
└── tracer.py      # Skill execution tracing for UI
```

## Components

### Skill Registry (`registry.py`)
- Loads skills from `skills/` directory and user skills from `~/.efp/skills/`
- Matches user messages to skills via triggers
- Provides skill metadata to agent prompt builder
- Supports skill versioning and deprecation

### Skill Tracer (`tracer.py`)
- Tracks skill execution events for UI display
- Collects step-by-step execution data
