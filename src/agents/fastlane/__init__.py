"""Fast Lane Commands - Real-time command processing.

Provides fast lane commands that can be processed without going through
the full LLM pipeline. These commands allow users to quickly inspect
agent behavior at runtime.

Supported commands:
- /status - Show current configuration
- /help - Show help message
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class FastLaneCommands:
    """Fast lane command handler."""

    # Command prefix
    COMMAND_PREFIX = "/"

    # Supported commands
    COMMANDS = ["status", "help"]

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

        if command == "status":
            return self._cmd_status()
        elif command == "help":
            return self._cmd_help()

        return None

    def _cmd_status(self) -> str:
        """Handle /status command.

        Returns:
            Response message with current configuration
        """
        from src.agents.heartbeat import _heartbeat

        status_parts = [
            "📊 **Current Configuration**",
            "",
            f"• Heartbeat: {'enabled' if _heartbeat else 'disabled'}",
        ]

        if _heartbeat:
            status_parts.extend([
                f"• Heartbeat Interval: {_heartbeat._get_effective_interval()}s",
                f"• Heartbeat Detail: {_heartbeat._get_check_detail_level()}",
            ])

        return "\n".join(status_parts)

    def _cmd_help(self) -> str:
        """Handle /help command.

        Returns:
            Response message with help information
        """
        return """🤖 **Fast Lane Commands**

Available commands:

• `/status` - Show current configuration

• `/help` - Show this help message

**Examples**:
```
/status            # View current settings
```
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
