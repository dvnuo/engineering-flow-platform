"""Heartbeat module - Periodic background checks.

This module provides heartbeat functionality that periodically checks:
- Emails (if configured)
- Calendar (if configured)
- Weather (if configured)
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class HeartbeatChecker:
    """Periodic background heartbeat checker."""

    def __init__(
        self,
        check_interval: int = 300,  # 5 minutes default
    ):
        """Initialize heartbeat checker.

        Args:
            check_interval: Base interval between checks in seconds
        """
        self.check_interval = check_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._last_checks: Dict[str, float] = {}

    def _get_effective_interval(self) -> int:
        """Get effective check interval."""
        return self.check_interval

    def _get_check_detail_level(self) -> str:
        """Get check detail level."""
        return "normal"

    async def _check_emails(self) -> Dict[str, Any]:
        """Check emails."""
        return {
            "type": "emails",
            "detail_level": "normal",
            "unread_count": 0,
            "important_count": 0,
            "action_required": [],
            "message": "Email analysis - considering importance and urgency",
        }

    async def _check_calendar(self) -> Dict[str, Any]:
        """Check calendar."""
        return {
            "type": "calendar",
            "detail_level": "normal",
            "today_count": 0,
            "conflicts": [],
            "upcoming_important": [],
            "message": "Calendar analysis - checking conflicts and priorities",
        }

    async def _check_weather(self) -> Dict[str, Any]:
        """Check weather."""
        return {
            "type": "weather",
            "detail_level": "normal",
            "current": {},
            "forecast": [],
            "alerts": [],
            "recommendations": [],
            "message": "Weather analysis - forecast and recommendations",
        }

    async def run_checks(self) -> Dict[str, Any]:
        """Run all heartbeat checks."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "detail_level": self._get_check_detail_level(),
            "checks": {},
        }

        # Run checks concurrently
        email_task = asyncio.create_task(self._check_emails())
        calendar_task = asyncio.create_task(self._check_calendar())
        weather_task = asyncio.create_task(self._check_weather())

        results["checks"]["emails"] = await email_task
        results["checks"]["calendar"] = await calendar_task
        results["checks"]["weather"] = await weather_task

        return results

    async def _heartbeat_loop(self):
        """Main heartbeat loop."""
        logger.info(f"=== [HEARTBEAT] STARTED ===")
        logger.info(f"  interval={self._get_effective_interval()}s")

        while self.running:
            try:
                # Check if interval has passed
                now = datetime.now().timestamp()
                last_email = self._last_checks.get("emails", 0)
                interval = self._get_effective_interval()

                if now - last_email >= interval:
                    results = await self.run_checks()
                    self._last_checks["emails"] = now

                    # Log summary based on detail level
                    detail = results["detail_level"]
                    logger.info(f"=== [HEARTBEAT] CHECK COMPLETED ===")
                    logger.info(f"  Detail: {detail}")

            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            await asyncio.sleep(10)  # Check every 10s for interval expiry

    async def start(self):
        """Start the heartbeat checker."""
        if self.running:
            return

        self.running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"=== [HEARTBEAT] CHECKER STARTED ===")
        logger.info(f"  interval={self._get_effective_interval()}s")

    async def stop(self):
        """Stop the heartbeat checker."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("=== [HEARTBEAT] STOPPED ===")


# Global heartbeat instance
_heartbeat: Optional[HeartbeatChecker] = None


def get_heartbeat() -> HeartbeatChecker:
    """Get or create the global heartbeat instance."""
    global _heartbeat
    if _heartbeat is None:
        _heartbeat = HeartbeatChecker()
    return _heartbeat


async def start_heartbeat():
    """Start the heartbeat."""
    heartbeat = get_heartbeat()
    await heartbeat.start()


async def stop_heartbeat():
    """Stop the heartbeat."""
    global _heartbeat
    if _heartbeat:
        await _heartbeat.stop()
        _heartbeat = None
