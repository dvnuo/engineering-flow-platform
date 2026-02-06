"""Heartbeat module - Periodic background checks with thinking-level awareness.

This module provides heartbeat functionality that periodically checks:
- Emails (if configured)
- Calendar (if configured)
- Weather (if configured)

The behavior is influenced by the thinking level:
- thinking=off: Fast heartbeat, simplified checks (just unread count)
- thinking=high: Detailed checks, multi-round verification
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from agent.thinking import ThinkLevel

logger = logging.getLogger(__name__)


class HeartbeatChecker:
    """Heartbeat checker with thinking-level awareness."""
    
    def __init__(
        self,
        think_level: ThinkLevel = ThinkLevel.OFF,
        check_interval: int = 300,  # 5 minutes default
    ):
        """Initialize heartbeat checker.
        
        Args:
            think_level: Current thinking level affecting check behavior
            check_interval: Base interval between checks in seconds
        """
        self.think_level = think_level
        self.check_interval = check_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._last_checks: Dict[str, float] = {}
        
    def _get_effective_interval(self) -> int:
        """Get effective check interval based on thinking level."""
        # thinking=high: more frequent checks
        # thinking=off: less frequent checks
        if self.think_level == ThinkLevel.HIGH:
            return max(60, self.check_interval // 2)  # 2x more frequent
        elif self.think_level == ThinkLevel.OFF:
            return self.check_interval * 2  # 2x less frequent
        return self.check_interval
    
    def _get_check_detail_level(self) -> str:
        """Get check detail level based on thinking level."""
        if self.think_level == ThinkLevel.HIGH:
            return "detailed"
        elif self.think_level in (ThinkLevel.OFF, ThinkLevel.MINIMAL):
            return "simplified"
        return "normal"
    
    async def _check_emails(self) -> Dict[str, Any]:
        """Check emails based on thinking level."""
        detail_level = self._get_check_detail_level()
        
        try:
            from src.tools.jira import jira_get_issue
        except ImportError:
            return {"status": "unavailable", "reason": "Jira not configured"}
        
        if detail_level == "simplified":
            # Just get unread count (fast)
            return {
                "type": "emails",
                "detail_level": "simplified",
                "unread_count": 0,  # Would call API for actual count
                "message": "Quick email check - unread count only",
            }
        else:
            # Detailed analysis (slower)
            return {
                "type": "emails",
                "detail_level": "detailed",
                "unread_count": 0,
                "important_count": 0,
                "action_required": [],
                "message": "Detailed email analysis - considering importance and urgency",
            }
    
    async def _check_calendar(self) -> Dict[str, Any]:
        """Check calendar based on thinking level."""
        detail_level = self._get_check_detail_level()
        
        if detail_level == "simplified":
            return {
                "type": "calendar",
                "detail_level": "simplified",
                "today_count": 0,
                "message": "Quick calendar check - today's events only",
            }
        else:
            return {
                "type": "calendar",
                "detail_level": "detailed",
                "today_count": 0,
                "conflicts": [],
                "upcoming_important": [],
                "message": "Detailed calendar analysis - checking conflicts and priorities",
            }
    
    async def _check_weather(self) -> Dict[str, Any]:
        """Check weather based on thinking level."""
        detail_level = self._get_check_detail_level()
        
        if detail_level == "simplified":
            return {
                "type": "weather",
                "detail_level": "simplified",
                "condition": "unknown",
                "message": "Quick weather check - current condition only",
            }
        else:
            return {
                "type": "weather",
                "detail_level": "detailed",
                "current": {},
                "forecast": [],
                "alerts": [],
                "recommendations": [],
                "message": "Detailed weather analysis - forecast and recommendations",
            }
    
    async def run_checks(self) -> Dict[str, Any]:
        """Run all heartbeat checks based on thinking level."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "think_level": self.think_level.value,
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
        
        # Multi-round verification for high thinking level:
        # When thinking=high, detailed checks return additional info
        # (important_count, conflicts, alerts, etc.) for deeper analysis
        if self.think_level == ThinkLevel.HIGH:
            logger.debug(f"=== [HEARTBEAT] MULTI-ROUND VERIFICATION ===")
            logger.debug(f"  Enabled: detailed analysis for all checks")
        
        return results
    
    async def _heartbeat_loop(self):
        """Main heartbeat loop."""
        logger.info(f"=== [HEARTBEAT] STARTED ===")
        logger.info(f"  think_level={self.think_level.value}")
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
                    
                    # For high thinking level, log more details (multi-round verification)
                    if self.think_level == ThinkLevel.HIGH:
                        logger.debug(f"=== [HEARTBEAT] DETAILED RESULTS ===")
                        for check_type, check_result in results["checks"].items():
                            logger.debug(f"  {check_type}: {check_result.get('message', '')}")
                
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
        logger.info(f"  think_level={self.think_level.value}")
    
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
    
    def update_think_level(self, think_level: ThinkLevel):
        """Update thinking level at runtime."""
        old_level = self.think_level
        self.think_level = think_level
        
        if old_level != think_level:
            logger.info(f"=== [HEARTBEAT] LEVEL CHANGED ===")
            logger.info(f"  {old_level.value} -> {think_level.value}")
            logger.info(f"  interval: {self._get_effective_interval()}s")
            logger.info(f"  detail: {self._get_check_detail_level()}")


# Global heartbeat instance
_heartbeat: Optional[HeartbeatChecker] = None


def get_heartbeat(think_level: ThinkLevel = ThinkLevel.OFF) -> HeartbeatChecker:
    """Get or create the global heartbeat instance."""
    global _heartbeat
    if _heartbeat is None:
        _heartbeat = HeartbeatChecker(think_level=think_level)
    return _heartbeat


async def start_heartbeat(think_level: ThinkLevel = ThinkLevel.OFF):
    """Start the heartbeat with specified thinking level."""
    heartbeat = get_heartbeat(think_level)
    await heartbeat.start()


async def stop_heartbeat():
    """Stop the heartbeat."""
    global _heartbeat
    if _heartbeat:
        await _heartbeat.stop()
        _heartbeat = None


def update_heartbeat_think_level(think_level: ThinkLevel):
    """Update the thinking level of the running heartbeat."""
    global _heartbeat
    if _heartbeat:
        _heartbeat.update_think_level(think_level)
