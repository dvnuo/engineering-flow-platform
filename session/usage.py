"""Usage tracking for OpsClaw Mini.

Tracks token usage and estimates costs.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class UsageStats:
    """Token usage statistics."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_tokens: int = 0
    model: str = ""
    cost: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "context_tokens": self.context_tokens,
            "model": self.model,
            "cost": self.cost,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UsageStats":
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            context_tokens=data.get("context_tokens", 0),
            model=data.get("model", ""),
            cost=data.get("cost", 0.0),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat()),
        )


# Token pricing (per 1M tokens) - can be extended with more models
TOKEN_PRICING = {
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "gpt-3.5-turbo-16k": {"input": 3.0, "output": 4.0},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "default": {"input": 1.0, "output": 2.0},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost for a given model and token count."""
    pricing = TOKEN_PRICING.get(model, TOKEN_PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


class UsageTracker:
    """Tracks token usage per session and globally."""
    
    def __init__(self, base_path: str = "~/.opsclaw/usage"):
        self.base_path = Path(base_path).expanduser()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.session_file = self.base_path / "sessions.jsonl"
        self.global_file = self.base_path / "global.jsonl"
    
    def _parse_usage_from_response(self, response: Dict, model: str) -> UsageStats:
        """Parse token usage from LLM API response."""
        usage = response.get("usage", {})
        
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
        
        # Estimate context tokens (input - prompt tokens from history)
        # This is an approximation
        context_tokens = input_tokens
        
        cost = estimate_cost(model, input_tokens, output_tokens)
        
        return UsageStats(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            context_tokens=context_tokens,
            model=model,
            cost=cost,
        )
    
    def record_usage(
        self,
        session_id: str,
        response: Dict,
        model: str,
        channel: str = "unknown"
    ) -> UsageStats:
        """Record token usage for a session."""
        stats = self._parse_usage_from_response(response, model)
        
        # Append to session file
        entry = {
            "session_id": session_id,
            "channel": channel,
            **stats.to_dict(),
        }
        
        with open(self.session_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
        
        # Append to global file
        global_entry = {
            "type": "usage",
            **stats.to_dict(),
        }
        
        with open(self.global_file, 'a') as f:
            f.write(json.dumps(global_entry) + '\n')
        
        return stats
    
    def get_session_usage(self, session_id: str) -> List[UsageStats]:
        """Get usage stats for a specific session."""
        usages = []
        
        if not self.session_file.exists():
            return usages
        
        with open(self.session_file, 'r') as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        usages.append(UsageStats.from_dict(entry))
        
        return usages
    
    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Get summary stats for a session."""
        usages = self.get_session_usage(session_id)
        
        if not usages:
            return {
                "session_id": session_id,
                "total_input": 0,
                "total_output": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "request_count": 0,
            }
        
        total_input = sum(u.input_tokens for u in usages)
        total_output = sum(u.output_tokens for u in usages)
        total_cost = sum(u.cost for u in usages)
        
        return {
            "session_id": session_id,
            "total_input": total_input,
            "total_output": total_output,
            "total_tokens": total_input + total_output,
            "total_cost": total_cost,
            "request_count": len(usages),
            "model": usages[-1].model if usages else "",
        }
    
    def get_global_summary(self, hours: Optional[int] = None) -> Dict[str, Any]:
        """Get global usage summary, optionally filtered by time."""
        if not self.global_file.exists():
            return {
                "total_input": 0,
                "total_output": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "request_count": 0,
            }
        
        cutoff = None
        if hours:
            cutoff = datetime.utcnow().timestamp() - (hours * 3600)
        
        total_input = 0
        total_output = 0
        total_cost = 0.0
        request_count = 0
        
        with open(self.global_file, 'r') as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if cutoff:
                        ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                        if ts < cutoff:
                            continue
                    
                    total_input += entry.get("input_tokens", 0)
                    total_output += entry.get("output_tokens", 0)
                    total_cost += entry.get("cost", 0.0)
                    request_count += 1
        
        return {
            "total_input": total_input,
            "total_output": total_output,
            "total_tokens": total_input + total_output,
            "total_cost": total_cost,
            "request_count": request_count,
        }
    
    def get_usage_by_model(self, hours: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        """Get usage breakdown by model."""
        if not self.global_file.exists():
            return {}
        
        cutoff = None
        if hours:
            cutoff = datetime.utcnow().timestamp() - (hours * 3600)
        
        by_model: Dict[str, Dict[str, Any]] = {}
        
        with open(self.global_file, 'r') as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("type") != "usage":
                        continue
                    
                    if cutoff:
                        ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                        if ts < cutoff:
                            continue
                    
                    model = entry.get("model", "unknown")
                    if model not in by_model:
                        by_model[model] = {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cost": 0.0,
                            "requests": 0,
                        }
                    
                    by_model[model]["input_tokens"] += entry.get("input_tokens", 0)
                    by_model[model]["output_tokens"] += entry.get("output_tokens", 0)
                    by_model[model]["cost"] += entry.get("cost", 0.0)
                    by_model[model]["requests"] += 1
        
        return by_model
    
    def clear_session_usage(self, session_id: str) -> int:
        """Clear usage records for a session. Returns count cleared."""
        if not self.session_file.exists():
            return 0
        
        remaining = []
        cleared = 0
        
        with open(self.session_file, 'r') as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        cleared += 1
                    else:
                        remaining.append(line)
        
        with open(self.session_file, 'w') as f:
            f.writelines(remaining)
        
        return cleared


# Global usage tracker instance
usage_tracker = UsageTracker()
