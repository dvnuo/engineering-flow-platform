# MobileX Test Cases Generator

Generate mobile automation test cases from Jira tickets. Creates Cucumber feature files and Java step implementations for iOS/Android.

## Quick Start

```bash
python3 skills/mobilex-test-cases-generator/scripts/generate_code.py feature EFP-123 "User Login"
```

## Files

- `SKILL.md` - Skill definition and usage guide
- `scripts/generate_code.py` - Code generation utilities
- `references/file-naming.md` - File naming conventions
- `references/template.md` - Code templates (Feature, Java files)

## Workflow

1. **Provide Jira Ticket** → Parse and fetch details
2. **Confirm Information** → Review summary and AC
3. **Generate Scenarios** → Create Cucumber scenarios
4. **Review Scenarios** → User confirms/adjusts
5. **Generate Code** → Create all test files
6. **Submit Code** → Git push + PR link

## Example

```
skill mode: generate mobile tests for EFP-123, EFP-456
```

## Requirements

- Jira access (for fetching ticket details)
- GitHub access (for committing code)
- Python 3.6+ (for code generation scripts)