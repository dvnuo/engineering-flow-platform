"""Session persistence layer for Engineering Flow Platform.

Manages JSONL transcript files and sessions.json store with TTL support.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from src.config import config

logger = logging.getLogger(__name__)
