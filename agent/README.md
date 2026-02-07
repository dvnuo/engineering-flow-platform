# Agent Directory

## Directory Structure

```
agent/
├── __init__.py
├── core.py                  # Agent core logic and orchestration
├── model_fallback.py        # Model fallback and selection strategy
├── llm.py                  # LLM provider abstraction (OpenAI, Anthropic, Ollama)
├── heartbeat/              # Heartbeat mechanism for health checks
│   ├── __init__.py
│   ├── heartbeat.py        # Heartbeat implementation
│   └── scheduler.py        # Heartbeat scheduling
├── fastlane/               # Fast lane for priority requests
│   ├── __init__.py
│   └── fastlane.py        # Priority queue handling
└── (other agent components)
```

## How It Works

### 1. Agent Core Flow
```
User Message → Core → Intent Recognition → Skill Selection → Execution → Response
                    ↓
            Model Selection (LLM)
                    ↓
            Context Building
```

### 2. Model Fallback Mechanism
```python
# model_fallback.py

class ModelFallback:
    """Automatic model selection and fallback strategy."""
    
    # Provider priority order
    PROVIDERS = {
        "primary": ["openai/gpt-4", "anthropic/claude-3-opus"],
        "fallback": ["openai/gpt-3.5-turbo", "anthropic/claude-3-sonnet", "ollama/mistral"],
        "emergency": ["ollama/llama2", "local_model"]
    }
    
    def get_model(self, task_type: str) -> str:
        """Select model based on task type."""
        if task_type == "reasoning":
            return self.PROVIDERS["primary"][0]
        elif task_type == "fast":
            return self.PROVIDERS["fallback"][0]
        return self.PROVIDERS["primary"][0]
    
    def fallback(self, failed_model: str) -> str:
        """Get next available model when primary fails."""
        all_models = self.PROVIDERS["primary"] + self.PROVIDERS["fallback"]
        try:
            idx = all_models.index(failed_model)
            return all_models[min(idx + 1, len(all_models) - 1)]
        except ValueError:
            return all_models[0]
```

### 3. LLM Provider Abstraction
```python
# llm.py

class LLMProvider:
    """Abstract base class for LLM providers."""
    
    def complete(self, prompt: str, **kwargs) -> str:
        """Generate completion."""
        raise NotImplementedError
    
    def stream(self, prompt: str, **kwargs):
        """Stream completion."""
        raise NotImplementedError
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider."""
    
    def __init__(self, api_key: str, model: str = "gpt-4"):
        self.api_key = api_key
        self.model = model
    
    def complete(self, prompt: str, **kwargs) -> str:
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs
        )
        return response.choices[0].message.content


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""
    
    def __init__(self, api_key: str, model: str = "claude-3-opus"):
        self.api_key = api_key
        self.model = model
    
    def complete(self, prompt: str, **kwargs) -> str:
        response = anthropic.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs
        )
        return response.content[0].text


class OllamaProvider(LLMProvider):
    """Ollama local model provider."""
    
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "mistral"):
        self.base_url = base_url
        self.model = model
    
    def complete(self, prompt: str, **kwargs) -> str:
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, **kwargs}
        )
        return response.json()["response"]
```

### 4. Heartbeat System
```python
# heartbeat/heartbeat.py

class Heartbeat:
    """Periodic health check system."""
    
    def __init__(self, interval: int = 300):
        self.interval = interval
        self.checks = []
        self.last_check = None
        self.status = "healthy"
    
    def register_check(self, name: str, check_func: callable, threshold: int = 3):
        """Register a health check."""
        self.checks.append({
            "name": name,
            "func": check_func,
            "threshold": threshold,
            "failures": 0
        })
    
    def run_checks(self) -> dict:
        """Run all registered checks."""
        results = {}
        for check in self.checks:
            try:
                result = check["func"]()
                results[check["name"]] = "healthy" if result else "unhealthy"
            except Exception as e:
                results[check["name"]] = f"error: {e}"
                check["failures"] += 1
        return results
```

## What Problems It Solves

- **Multi-Model Support**: Unified interface for OpenAI, Anthropic, Ollama
- **Automatic Fallback**: Seamless switch when primary model fails
- **Heartbeat Monitoring**: Health checks prevent stale connections
- **Fast Lane**: Priority handling for urgent requests
- **Token Management**: Efficient context and token usage

## Configuration Options

### Core Agent Configuration (config.yaml)

```yaml
# config.yaml
agent:
  # Core settings
  name: "opsclaw"
  mode: "auto"              # auto, manual, readonly
  
  # Default model settings
  default_model: "openai/gpt-4"
  model_timeout: 120        # seconds
  
  # Model fallback configuration
  fallback:
    enabled: true
    strategy: "priority"     # priority, round_robin, least_cost
    max_retries: 3
    retry_delay: 1           # seconds
    providers:
      primary:
        - "openai/gpt-4"
        - "anthropic/claude-3-opus"
      fallback:
        - "openai/gpt-3.5-turbo"
        - "anthropic/claude-3-sonnet"
      emergency:
        - "ollama/mistral"
        - "local/llama2"
  
  # Task-type model routing
  model_routing:
    reasoning: "openai/gpt-4"
    creative: "anthropic/claude-3-opus"
    fast: "openai/gpt-3.5-turbo"
    coding: "openai/gpt-4"
    local: "ollama/mistral"
  
  # Heartbeat configuration
  heartbeat:
    enabled: true
    interval: 300            # seconds
    timeout: 30
    checks:
      - name: "llm_connection"
        threshold: 3
      - name: "memory_health"
        threshold: 2
      - name: "channel_status"
        threshold: 3
  
  # Fast lane configuration
  fastlane:
    enabled: true
    max_queue_size: 100
    priority_threshold: 7   # Priority level for fast lane
  
  # Context management
  context:
    max_tokens: 128000
    strategy: "truncate"     # truncate, summarize, window
    system_prompt: "You are OpsClaw, a helpful AI assistant."
  
  # Reasoning replay (OpenAI o1/o3 style)
  reasoning_replay:
    enabled: false
    max_reasoning_tokens: 25000
```

### LLM Provider Configuration

```yaml
# OpenAI
openai:
  api_key: ${OPENAI_API_KEY}
  organization: ${OPENAI_ORG_ID}
  base_url: "https://api.openai.com/v1"
  models:
    default: "gpt-4-turbo-preview"
    reasoning: "o1-preview"
    fast: "gpt-3.5-turbo"
  options:
    max_tokens: 4096
    temperature: 0.7
    top_p: 1.0
    frequency_penalty: 0
    presence_penalty: 0

# Anthropic
anthropic:
  api_key: ${ANTHROPIC_API_KEY}
  base_url: "https://api.anthropic.com"
  models:
    default: "claude-3-opus-20240229"
    fast: "claude-3-haiku-20240307"
  options:
    max_tokens: 4096
    temperature: 0.7
    top_k: 0

# Ollama
ollama:
  base_url: "http://localhost:11434"
  models:
    default: "mistral"
    coding: "codellama"
    reasoning: "llama2"
  options:
    temperature: 0.7
    top_p: 0.9
    num_predict: 2048
```

### Environment Variables

```bash
# Core
OPENAI_API_KEY=sk-xxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
OLLAMA_BASE_URL=http://localhost:11434

# Organization
OPENAI_ORG_ID=org-xxxxxxxxxxxx

# Local models
LOCAL_MODEL_PATH=/path/to/models
```

## How to Run

### Start Agent
```bash
# Basic startup
python main.py

# With custom config
python main.py --config /path/to/config.yaml

# Development mode
python main.py --debug

# Specific profile
python main.py --profile production
```

### Test Agent Components
```bash
# Test model fallback
pytest tests/test_model_fallback.py -v

# Test LLM providers
pytest tests/test_llm.py -v

# Test heartbeat
pytest tests/test_heartbeat.py -v

# Test all agent components
pytest tests/test_agent*.py -v
```

### Health Check
```bash
# Check agent health
curl http://localhost:8080/health

# Check model status
curl http://localhost:8080/models

# Check heartbeat status
curl http://localhost:8080/heartbeat
```

## Development Principles

### 1. Module Responsibilities
```
core.py         → Main orchestration and coordination
model_fallback.py → Model selection and automatic fallback
llm.py          → LLM API abstraction and provider management
heartbeat/      → Health monitoring system
fastlane/       → Priority request handling
```

### 2. Error Handling
```python
# Graceful degradation
try:
    result = llm.complete(prompt)
except RateLimitError:
    model = fallback.get_next_model()
    result = alternative.complete(prompt)
except TimeoutError:
    return cached_result or error_response
```

### 3. Performance Optimization
```python
# Response caching
@cache(ttl=300)
def complete_with_cache(prompt: str) -> str:
    return llm.complete(prompt)

# Connection pooling
session = aiohttp.ClientSession(
    connector=aiohttp.TCPConnector(limit=100)
)

# Streaming responses
async def stream_response(prompt: str):
    async for chunk in llm.stream(prompt):
        yield chunk
```

### 4. Testing Standards
```python
class TestModelFallback:
    def test_primary_failure_fallback(self):
        """Test automatic fallback on primary failure."""
        fallback = ModelFallback()
        result = fallback.handle_failure("openai/gpt-4")
        assert result in fallback.PROVIDERS["fallback"]
    
    def test_all_providers_failed(self):
        """Test behavior when all providers fail."""
        fallback = ModelFallback()
        result = fallback.handle_complete_failure()
        assert result == "emergency_mode"
```

### 5. Logging and Monitoring
```python
import structlog

logger = structlog.get_logger()

# Structured logging
logger.info(
    "model_completed",
    model="gpt-4",
    tokens=1500,
    duration_ms=2340,
    success=True
)

logger.error(
    "model_failed",
    model="gpt-4",
    error="rate_limit",
    retry_count=2
)
```

## API Reference

### Core Module (agent/core.py)

```python
class AgentCore:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm = LLMManager(config.llm)
        self.fallback = ModelFallback(config.fallback)
        self.heartbeat = Heartbeat(config.heartbeat)
    
    def process(self, message: str) -> str:
        """Process user message and return response."""
        intent = self.recognize_intent(message)
        skill = self.select_skill(intent)
        result = skill.execute(message)
        return self.format_response(result)
    
    def recognize_intent(self, message: str) -> Intent:
        """Recognize user intent from message."""
        ...
    
    def select_skill(self, intent: Intent) -> Skill:
        """Select appropriate skill for intent."""
        ...
    
    def health_check(self) -> HealthStatus:
        """Perform health check."""
        ...
```

### Model Fallback (agent/model_fallback.py)

```python
class ModelFallback:
    def __init__(self, config: FallbackConfig):
        self.providers = config.providers
        self.strategy = config.strategy
    
    def get_model(self, task_type: str) -> str:
        """Get best model for task type."""
        ...
    
    def handle_failure(self, failed_model: str) -> str:
        """Handle model failure and return fallback."""
        ...
    
    def get_status(self) -> FallbackStatus:
        """Get current fallback status."""
        ...
```

## Troubleshooting

### Model Not Available
```python
# Check model availability
from agent.model_fallback import ModelFallback

fallback = ModelFallback()
available = fallback.get_available_models()
print(available)
```

### Connection Issues
```bash
# Check LLM provider connectivity
curl http://localhost:8080/health/providers

# Test OpenAI connection
python -c "import openai; print(openai.Model.list())"

# Test Ollama connection
curl http://localhost:11434/api/tags
```

### Performance Issues
```python
# Enable verbose logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Check token usage
from agent.llm import TokenCounter
counter = TokenCounter()
usage = counter.get_usage_stats()
```
