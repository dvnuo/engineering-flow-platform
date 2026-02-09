"""WebChat UI and HTTP server for Engineering Flow Platform.

A simple web interface to chat with the agent directly.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from aiohttp import web

from src.agents.core import Agent as AgentCore
from src.agents.errors import extract_error_details, LLMError
from src.config import config
from src.sessions.manager import session_manager
from src.sessions.usage import usage_tracker

logger = logging.getLogger(__name__)


# Get template and static paths
TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def load_template(filename: str) -> str:
    """Load HTML template from file."""
    template_path = TEMPLATE_DIR / filename
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


async def serve_webchat(request: web.Request) -> web.Response:
    """Serve the WebChat UI."""
    try:
        html_content = load_template("webchat.html")
        return web.Response(
            text=html_content,
            content_type='text/html',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
            }
        )
    except FileNotFoundError:
        logger.error(f"WebChat template not found: {TEMPLATE_DIR / 'webchat.html'}")
        return web.Response(
            text="<html><body><h1>WebChat template not found</h1></body></html>",
            status=500,
            content_type='text/html'
        )


async def serve_static(request: web.Request) -> web.Response:
    """Serve static files (CSS, JS)."""
    path = request.match_info.get('path', '')
    file_path = STATIC_DIR / path
    
    # Security: prevent directory traversal
    try:
        file_path = file_path.resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())):
            return web.Response(status=403, text="Forbidden")
    except (ValueError, OSError):
        return web.Response(status=400, text="Invalid path")
    
    if not file_path.exists():
        return web.Response(status=404, text="Not found")
    
    # Determine content type
    content_type = 'text/plain'
    if file_path.suffix == '.css':
        content_type = 'text/css'
    elif file_path.suffix == '.js':
        content_type = 'application/javascript'
    elif file_path.suffix == '.html':
        content_type = 'text/html'
    elif file_path.suffix == '.json':
        content_type = 'application/json'
    elif file_path.suffix == '.png':
        content_type = 'image/png'
    elif file_path.suffix == '.jpg' or file_path.suffix == '.jpeg':
        content_type = 'image/jpeg'
    elif file_path.suffix == '.svg':
        content_type = 'image/svg+xml'
    elif file_path.suffix == '.ico':
        content_type = 'image/x-icon'
    
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        return web.Response(
            body=content,
            content_type=content_type,
            headers={
                'Cache-Control': 'public, max-age=3600',
            }
        )
    except Exception as e:
        logger.error(f"Error serving static file {file_path}: {e}")
        return web.Response(status=500, text="Internal server error")


async def api_chat(request: web.Request) -> web.Response:
    """Handle chat API requests.
    
    POST /api/chat
    Body: {"message": "...", "session_id": "optional", "reasoning_replay": false}
    """
    try:
        data = await request.json()
        message = (data.get('message') or '').strip()
        
        # Dynamic session_id with timestamp-based default for multi-session support
        session_id = data.get('session_id', f'webchat_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}')
        
        # Get reasoning_replay setting
        reasoning_replay = data.get('reasoning_replay', None)
        
        if not message:
            return web.json_response({'error': 'Empty message'}, status=400)
        
        # Run agent (history is managed internally by session_manager)
        agent = AgentCore()
        result = await agent.process(
            message=message,
            session_id=session_id,
            user_name="webchat-user",
            track_usage=True,
            reasoning_replay=reasoning_replay,
        )
        
        response = result.get("response", "") if result else ""
        usage = result.get("usage", {}) if result else {}
        reasoning = result.get("reasoning", "") if result else ""
        
        # Record usage if available
        if usage:
            usage_tracker.record_usage(
                session_id=session_id,
                response={"usage": usage},
                model=config.llm.get('model', 'unknown'),
                channel='webchat'
            )
        
        response_data = {
            'response': response,
            'session_id': session_id,
            'usage': usage
        }
        
        # Include reasoning if available
        if reasoning:
            response_data['reasoning'] = reasoning
        
        return web.json_response(response_data)
        
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        # Get detailed error information
        error_details = extract_error_details(e)
        
        # Log full error details
        logger.error(f"Chat error: {e}")
        logger.error(f"Error details: {json.dumps(error_details, indent=2)}")
        
        # Return user-friendly error message with optional details
        user_message = str(e)
        error_type = error_details.get("error_type", "unknown")
        status_code = 500
        
        # Map error types to HTTP status codes
        if error_type == "bad_request":
            status_code = 400
        elif error_type == "authentication_error":
            status_code = 401
        elif error_type == "rate_limit":
            status_code = 429
        elif error_type == "server_error":
            status_code = 500
        
        # Try to get a user-friendly message
        if isinstance(e, LLMError):
            # Use the error's message
            user_message = e.message
            status_code = e.status_code or status_code
        
        return web.json_response({
            'error': user_message,
            'error_type': error_type,
            'details': error_details.get("details", {}),
            'timestamp': error_details.get("timestamp"),
        }, status=status_code)


async def api_sessions(request: web.Request) -> web.Response:
    """List active sessions."""
    try:
        sessions = await session_manager.list_sessions()
        return web.json_response({'sessions': sessions})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


async def api_usage(request: web.Request) -> web.Response:
    """Get usage statistics."""
    try:
        session_id = request.query.get('session_id')
        if session_id:
            summary = usage_tracker.get_session_summary(session_id)
            return web.json_response(summary)
        else:
            summary = usage_tracker.get_global_summary()
            by_model = usage_tracker.get_usage_by_model()
            return web.json_response({
                'global': summary,
                'by_model': by_model
            })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


async def api_clear(request: web.Request) -> web.Response:
    """Clear chat history."""
    try:
        data = await request.json()
        session_id = data.get('session_id', 'webchat')
        
        await session_manager.clear_history(session_id)
        
        return web.json_response({'success': True})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


def setup_webchat_routes(app: web.Application):
    """Set up WebChat routes.
    
    Routes:
        GET  /chat           - WebChat UI
        GET  /static/*       - Static files (CSS, JS)
        POST /api/chat       - Send message
        GET  /api/sessions   - List sessions
        GET  /api/usage      - Get usage stats
        POST /api/clear      - Clear session
    """
    app.router.add_get('/chat', serve_webchat)
    app.router.add_get('/static/{path:.*}', serve_static)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/clear', api_clear)
    
    logger.info("WebChat routes registered:")
    logger.info("  GET  /chat        - WebChat UI")
    logger.info("  GET  /static/*    - Static files (CSS, JS)")
    logger.info("  POST /api/chat    - Send message")
    logger.info("  GET  /api/sessions - List sessions")
    logger.info("  GET  /api/usage    - Get usage stats")
    logger.info("  POST /api/clear   - Clear session")
