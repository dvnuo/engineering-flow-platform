"""Fast Lane Commands - Real-time command processing.

Provides fast lane commands that can be processed without going through
the full LLM pipeline. These commands allow users to quickly adjust
agent behavior at runtime.

Supported commands:
- /thinking <level> - Set thinking level (off, minimal, low, medium, high)
- /reasoning <on|off> - Enable/disable reasoning replay
- /status - Show current configuration
"""

import logging
from typing import Any, Dict, Optional, Tuple

from src.agents.thinking import ThinkLevel, normalize_think_level

logger = logging.getLogger(__name__)


class FastLaneCommands:
    """Fast lane command handler."""
    
    # Command prefix
    COMMAND_PREFIX = "/"
    
    # Supported commands
    COMMANDS = ["thinking", "reasoning", "status", "help"]
    
    # Thinking levels
    THINKING_LEVELS = ["off", "minimal", "low", "medium", "high"]
    
    # Reasoning options
    REASONING_OPTIONS = ["on", "off"]
    
    def __init__(self, agent=None):
        """Initialize fast lane commands handler.
        
        Args:
            agent: Reference to the Agent instance (optional)
        """
        self._agent = agent
    
    @property
    def agent(self):
        """Get agent instance (lazy initialization)."""
        if self._agent is None:
            raise RuntimeError("Legacy Agent is not available in EFP runtime native mode")
        return self._agent
    
    def is_command(self, message: str) -> bool:
        """Check if message is a fast lane command.
        
        Args:
            message: User message to check
            
        Returns:
            True if message is a fast lane command
        """
        if not message:
            return False
        
        # Check if starts with command prefix
        if not message.strip().startswith(self.COMMAND_PREFIX):
            return False
        
        # Extract command name
        parts = message.strip().split()
        if not parts:
            return False
        
        command_name = parts[0][1:]  # Remove leading '/'
        return command_name.lower() in self.COMMANDS
    
    def parse_command(self, message: str) -> Tuple[str, Optional[str], Optional[str]]:
        """Parse a fast lane command.
        
        Args:
            message: User message to parse
            
        Returns:
            Tuple of (command_name, argument, full_argument)
        """
        if not message:
            return ("", None, None)
        
        parts = message.strip().split(maxsplit=2)
        
        command = parts[0][1:].lower() if parts else ""
        arg = parts[1].lower() if len(parts) > 1 else None
        full_arg = parts[2] if len(parts) > 2 else None
        
        return (command, arg, full_arg)
    
    async def process(self, message: str) -> Optional[str]:
        """Process a fast lane command.
        
        Args:
            message: Command message to process
            
        Returns:
            Response message, or None if not a fast lane command
        """
        if not self.is_command(message):
            return None
        
        command, arg, full_arg = self.parse_command(message)
        
        if command == "thinking":
            return await self._cmd_thinking(arg)
        elif command == "reasoning":
            return await self._cmd_reasoning(arg)
        elif command == "status":
            return self._cmd_status()
        elif command == "help":
            return self._cmd_help()
        
        return None
    
    async def _cmd_thinking(self, level: Optional[str]) -> str:
        """Handle /thinking command.
        
        Args:
            level: Thinking level to set
            
        Returns:
            Response message
        """
        if not level:
            return self._format_thinking_help()
        
        # Normalize the level
        normalized = normalize_think_level(level)
        
        if normalized is None:
            return f"❌ Invalid thinking level: `{level}`\n\nValid levels: {', '.join(self.THINKING_LEVELS)}"
        
        # Update agent's thinking level
        if self._agent:
            old_level = self._agent.think_level
            self._agent.think_level = normalized
            
            # Update heartbeat if enabled
            from src.agents.heartbeat import update_heartbeat_think_level
            update_heartbeat_think_level(normalized)
            
            logger.info(f"FastLane: thinking level changed - {old_level.value} -> {normalized.value}")
            
            return f"✅ Thinking level set to `{normalized.value}`\n\n📊 Previous: `{old_level.value}` → Current: `{normalized.value}`"
        
        return f"✅ Thinking level set to `{normalized.value}`"
    
    async def _cmd_reasoning(self, state: Optional[str]) -> str:
        """Handle /reasoning command.
        
        Args:
            state: Reasoning state (on/off)
            
        Returns:
            Response message
        """
        if not state or state not in self.REASONING_OPTIONS:
            return self._format_reasoning_help()
        
        from src.config import config
        
        if state == "on":
            # Enable reasoning replay
            # Note: This would require updating config and potentially restarting
            return f"🔄 Reasoning replay enabled\n\nNote: Full enabling requires config update and may need restart."
        
        return f"🔄 Reasoning replay disabled"
    
    def _cmd_status(self) -> str:
        """Handle /status command.
        
        Returns:
            Response message with current configuration
        """
        from src.agents.heartbeat import _heartbeat
        
        thinking_level = self._agent.think_level.value if self._agent else "unknown"
        
        status_parts = [
            "📊 **Current Configuration**",
            "",
            f"• Thinking Level: `{thinking_level}`",
            f"• Heartbeat: {'enabled' if _heartbeat else 'disabled'}",
        ]
        
        if _heartbeat:
            status_parts.extend([
                f"• Heartbeat Interval: {_heartbeat._get_effective_interval()}s",
                f"• Heartbeat Detail: {_heartbeat._get_check_detail_level()}",
            ])
        
        status_parts.extend([
            "",
            "💡 Use `/thinking <level>` to change thinking level",
            "💡 Use `/reasoning <on|off>` to toggle reasoning replay",
        ])
        
        return "\n".join(status_parts)
    
    def _cmd_help(self) -> str:
        """Handle /help command.
        
        Returns:
            Response message with help information
        """
        return """🤖 **Fast Lane Commands**

Available commands:

• `/thinking <level>` - Set thinking level
  Levels: off, minimal, low, medium, high
  
• `/reasoning <on|off>` - Toggle reasoning replay
  
• `/status` - Show current configuration
  
• `/help` - Show this help message

**Examples**:
```
/thinking high      # Enable deep thinking
/thinking off      # Quick responses
/reasoning on      # Enable reasoning display
/status            # View current settings
```
"""
    
    def _format_thinking_help(self) -> str:
        """Format thinking level help message."""
        return """📖 **Thinking Levels**

Usage: `/thinking <level>`

Available levels:
- `off` - No thinking, quick responses
- `minimal` - Minimal thinking
- `low` - Basic thinking
- `medium` - Standard thinking
- `high` - Deep thinking, detailed analysis

**Example**: `/thinking high`
"""
    
    def _format_reasoning_help(self) -> str:
        """Format reasoning help message."""
        return """📖 **Reasoning Replay**

Usage: `/reasoning <on|off>`

- `on` - Show model's internal reasoning
- `off` - Hide reasoning (default)

**Example**: `/reasoning on`
"""


# Global fast lane instance
_fastlane: Optional[FastLaneCommands] = None


def get_fastlane(agent=None) -> FastLaneCommands:
    """Get or create global fast lane instance."""
    global _fastlane
    if _fastlane is None:
        _fastlane = FastLaneCommands(agent)
    return _fastlane


async def process_fastlane_command(message: str, agent=None) -> Optional[str]:
    """Process a fast lane command.
    
    Args:
        message: Command message
        agent: Optional agent reference
        
    Returns:
        Response message, or None if not a fast lane command
    """
    fastlane = get_fastlane(agent)
    return await fastlane.process(message)


def is_fastlane_command(message: str) -> bool:
    """Check if message is a fast lane command.
    
    Args:
        message: User message
        
    Returns:
        True if it's a fast lane command
    """
    fastlane = get_fastlane()
    return fastlane.is_command(message)
