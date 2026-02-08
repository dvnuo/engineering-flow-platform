"""
Confluence Channel - Backward compatible API.

This module re-exports from src/core/confluence/ for backward compatibility.
"""

from src.core.confluence import ConfluenceChannel

# Global instance for backward compatibility
confluence_channel = ConfluenceChannel()

# Export classes for direct import
__all__ = ["ConfluenceChannel", "confluence_channel"]
