# Tests Directory

## Directory Structure

```
tests/
├── __init__.py
├── conftest.py              # Pytest configuration and fixtures
├── test_agent*.py          # Agent tests
├── test_channel*.py        # Channel tests
├── test_skill*.py          # Skill tests
├── test_tools*.py         # Tools tests
├── test_coding_agent.py   # Coding agent tests
├── test_model_fallback.py # Model fallback tests
├── test_skill_creator.py  # Skill creator tests
├── fixtures/               # Test fixtures
│   ├── __init__.py
│   ├── channels.py         # Channel fixtures
│   ├── agents.py          # Agent fixtures
│   └── skills.py          # Skill fixtures
└── utils/                  # Test utilities
    ├── __init__.py
    ├── mocks.py           # Mock utilities
    ├── assertions.py      # Custom assertions
    └── helpers.py         # Test helpers
```

## How It Works

### 1. Pytest Framework
```python
# tests/conftest.py

import pytest
from typing import Generator
from unittest.mock import Mock

@pytest.fixture
def agent_core():
    """Provide agent core for testing."""
    from agent.core import AgentCore
    from config import AgentConfig
    config = AgentConfig()
    return AgentCore(config)

@pytest.fixture
def mock_channel():
    """Provide mock channel for testing."""
    from channel.base import Channel, Message, Response
    channel = Mock(spec=Channel)
    channel.receive.return_value = Message(
        content="test",
        author_id="123",
        channel_id="456",
        guild_id="789",
        timestamp="2024-01-01T00:00:00Z",
        message_id="msg-123"
    )
    return channel

@pytest.fixture
def sample_message() -> Message:
    """Provide sample message for testing."""
    return Message(
        content="Hello, agent!",
        author_id="user-123",
        channel_id="channel-456",
        guild_id="guild-789",
        timestamp="2024-01-01T00:00:00Z",
        message_id="msg-001"
    )
```

### 2. Test Organization
```python
# tests/test_agent_core.py

class TestAgentCore:
    """Tests for agent core functionality."""
    
    def test_process_message(self, agent_core):
        """Test message processing."""
        response = agent_core.process("Hello!")
        assert response is not None
        assert isinstance(response, str)
    
    def test_intent_recognition(self, agent_core):
        """Test intent recognition."""
        intent = agent_core.recognize_intent("Create a PR")
        assert intent.type == "github"
    
    def test_skill_selection(self, agent_core):
        """Test skill selection."""
        skill = agent_core.select_skill(intent)
        assert skill is not None
```

### 3. Test Utilities
```python
# tests/utils/mocks.py

class MockLLM:
    """Mock LLM provider for testing."""
    
    def __init__(self, responses: list = None):
        self.responses = responses or ["Mock response"]
        self.call_count = 0
    
    def complete(self, prompt: str) -> str:
        response = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        return response

# tests/utils/assertions.py

def assert_success(result):
    """Assert operation was successful."""
    assert result.success is True, f"Expected success, got: {result.error}"

def assert_contains(output: str, substring: str):
    """Assert output contains substring."""
    assert substring in output, f"Expected '{substring}' in output"
```

## What Problems It Solves

- **Regression Prevention**: Catch bugs before deployment
- **Code Quality**: Maintain high test coverage
- **CI/CD Integration**: Automated testing pipeline
- **Documentation by Example**: Tests serve as usage examples
- **Refactoring Safety**: Safe code modifications

## Configuration Options

### Pytest Configuration (pytest.ini)

```ini
[pytest]
# Test discovery
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Output format
addopts =
    -v                      # Verbose output
    --tb=short              # Short traceback
    --color=yes            # Colored output
    --co -q                # Collect only (quiet mode)

# Markers
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests as integration tests
    unit: marks tests as unit tests
    e2e: marks tests as end-to-end tests

# Filtering
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning

# Coverage
addopts =
    --cov=.
    --cov-report=term-missing
    --cov-report=html
    --cov-fail-under=80

# asyncio
asyncio_mode = auto

# Plugins
plugins =
    pytest-asyncio
    pytest-cov
    pytest-mock
    pytest-xdist
```

### Pytest Configuration (pyproject.toml)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-v",
    "--tb=short",
    "--color=yes",
]
asyncio_mode = "auto"

[tool.pytest_coverage.html]
title = "Engineering Flow Platform Test Coverage"
```

### Test-Specific Configuration

```python
# tests/conftest.py

import os

# Environment for tests
os.environ["TESTING"] = "true"
os.environ["LOG_LEVEL"] = "DEBUG"

# Disable certain features in tests
DISABLED_FEATURES = {
    "heartbeat": True,
    "metrics": True,
    "profiling": True,
}

# Test databases
TEST_DATABASES = {
    "memory": ":memory:",
    "sqlite": "file::memory:?cache=shared",
}

# Mock configurations
MOCK_DEFAULTS = {
    "llm_provider": "mock",
    "channel": "mock",
    "database": "memory",
}
```

### Fixture Configuration

```python
# tests/fixtures/channels.py

import pytest
from unittest.mock import Mock

@pytest.fixture(params=["discord", "whatsapp", "telegram"])
def any_channel(request):
    """Parametrized fixture for all channels."""
    channel = Mock()
    channel.type = request.param
    channel.config = {"token": "test_token"}
    return channel

@pytest.fixture
def discord_channel():
    """Discord-specific channel fixture."""
    ...

@pytest.fixture  
def whatsapp_channel():
    """WhatsApp-specific channel fixture."""
    ...
```

## How to Run

### Run All Tests
```bash
# Basic test run
pytest

# With coverage
pytest --cov=. --cov-report=html

# With specific configuration
pytest -c pytest.ini
```

### Run Specific Tests
```bash
# By file
pytest tests/test_agent_core.py

# By class
pytest tests/test_agent_core.py::TestAgentCore

# By method
pytest tests/test_agent_core.py::TestAgentCore::test_process_message

# By marker
pytest -m "unit"
pytest -m "integration"
pytest -m "not slow"

# By keyword
pytest -k "test_process"
```

### Run with Different Options
```bash
# Verbose output
pytest -v

# Stop on first failure
pytest -x

# Show local variables
pytest -l

# Capture output
pytest -s              # Print stdout
pytest --capture=no    # Don't capture

# Measure timing
pytest --durations=0  # Show all timings
pytest --durations=10  # Show top 10 slowest

# Parallel execution
pytest -n auto         # Use all CPU cores
pytest -n 4            # Use 4 workers
```

### Generate Reports
```bash
# HTML coverage report
pytest --cov=. --cov-report=html
open htmlcov/index.html

# JUnit XML report
pytest --junitxml=report.xml

# JSON report
pytest --json=report.json --json-truncate=0

# Allure report
pytest --alluredir=allure-results
allure serve allure_results
```

## Development Principles

### 1. Test Naming Conventions
```python
def test_<module>_<function>_<expected_behavior>():
    """Test description."""
    ...

# Examples:
def test_agent_core_process_message_success()
def test_skill_coding_agent_exec_with_pty()
def test_channel_discord_send_message_failure()
```

### 2. Test Organization
```python
class TestModuleName:
    """Test suite for module_name."""
    
    def setup_method(self):
        """Setup before each test."""
        self.fixture = create_fixture()
    
    def teardown_method(self):
        """Cleanup after each test."""
        self.fixture.cleanup()
    
    def test_feature_success(self):
        """Test successful execution."""
        ...
    
    def test_feature_failure(self):
        """Test error handling."""
        ...
    
    def test_edge_case(self):
        """Test boundary conditions."""
        ...
    
    def test_with_parameters(self, param):
        """Parametrized test."""
        ...
```

### 3. Test Pyramid

```
        /\
       /E2E\         <-- 10% End-to-end tests
      /-----\        
     /Integ\        <-- 30% Integration tests
    /-------\       
   /  Unit  \      <-- 60% Unit tests
  /---------\     
```

### 4. Best Practices

#### Unit Tests
```python
def test_calculate_total():
    """Unit test - test single function."""
    from module import calculate_total
    
    result = calculate_total([1, 2, 3])
    assert result == 6
```

#### Integration Tests
```python
def test_agent_skill_integration(agent_core, skill_registry):
    """Integration test - test component interaction."""
    response = agent_core.process("Execute skill")
    assert "skill output" in response
```

#### E2E Tests
```python
def test_full_message_flow(mock_discord, agent_core):
    """End-to-end test - test complete flow."""
    message = create_discord_message("/help")
    response = agent_core.process(message)
    assert "help text" in response
```

### 5. Mocking Guidelines
```python
from unittest.mock import Mock, patch, MagicMock

# Mock external dependencies
@patch('module.external_api')
def test_with_mock(mock_api):
    mock_api.return_value = {"result": "success"}
    result = module.function()
    assert result["result"] == "success"

# Mock class instances
mock_channel = Mock(spec=Channel)
mock_channel.send.return_value = "msg-123"
```

## Test Coverage Requirements

### Minimum Coverage by Module
```bash
# Required minimums
agent/              → 85%
channel/           → 80%
skills/            → 90%
tools/             → 85%
tests/             → 100%
```

### Coverage Report Sections
```bash
# Generate detailed coverage
pytest --cov=. --cov-report=term-missing --cov-report=html

# Check specific module coverage
pytest --cov=agent --cov-report=term-missing

# Coverage by test file
pytest --cov-per-test
```

## Troubleshooting

### Tests Not Found
```bash
# Check test discovery
pytest --collect-only

# Verify test file naming
ls tests/test_*.py

# Check PYTHONPATH
export PYTHONPATH=/path/to/engineering-flow-platform:$PYTHONPATH
```

### Import Errors
```bash
# Install in development mode
pip install -e .

# Check imports
python -c "from tests.conftest import *"
```

### Slow Tests
```bash
# Find slowest tests
pytest --durations=10

# Skip slow tests
pytest -m "not slow"

# Run without coverage (faster)
pytest --no-cov
```

### Flaky Tests
```bash
# Run multiple times
pytest --flaky=3

# Verbose output
pytest -v --tb=long

# Isolate test
pytest test_file.py::test_name -v --tb=long -s
```

## CI/CD Integration

### GitHub Actions
```yaml
# .github/workflows/tests.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e .
          pip install pytest pytest-cov pytest-asyncio
      
      - name: Run tests
        run: pytest --cov=. --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml
```

### Pre-commit Hooks
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: run-tests
        name: Run Tests
        entry: pytest -x --no-header
        language: system
        pass_filenames: false
        always_run: true
```
