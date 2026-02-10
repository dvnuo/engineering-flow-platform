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
                provider="openai",
                model=config.llm.get('model', 'unknown'),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                session_id=session_id,
                task_type="chat"
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
        days = int(request.query.get('days', 30))
        hours = days * 24
        
        if session_id:
            summary = usage_tracker.get_session_summary(session_id)
            return web.json_response(summary)
        else:
            global_summary = usage_tracker.get_global_summary(hours=hours)
            by_model = usage_tracker.get_usage_by_model(hours=hours)
            by_provider = usage_tracker.get_usage_by_provider(hours=hours)
            return web.json_response({
                'period_days': days,
                'global': global_summary,
                'by_model': by_model,
                'by_provider': by_provider,
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


def _parse_skill_from_file(skill_path: Path) -> Optional[Dict[str, Any]]:
    """Parse a skill from SKILL.md file.
    
    Args:
        skill_path: Path to SKILL.md file
        
    Returns:
        Skill dict or None if parsing fails
    """
    try:
        content = skill_path.read_text(encoding='utf-8')
        
        # Extract skill name from first line (without # prefix)
        lines = content.strip().split('\n')
        name = ""
        description = ""
        emoji = "🔧"
        
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('# ') and not name:
                name = line[2:].strip().replace(' Skill', '').lower()
            elif line.startswith('## Examples'):
                break
            elif not description and line and not line.startswith('#'):
                description = line
        
        # Try to find emoji in first line or after #
        emoji_match = content.strip().split('\n')[0]
        if '📌' in emoji_match:
            emoji = "📌"
        elif '🔧' in emoji_match:
            emoji = "🔧"
        elif '💻' in emoji_match:
            emoji = "💻"
        elif '📝' in emoji_match:
            emoji = "📝"
        elif '🔍' in emoji_match:
            emoji = "🔍"
        elif '🌤️' in emoji_match:
            emoji = "🌤️"
        
        return {
            "name": name,
            "description": description,
            "emoji": emoji,
            "path": str(skill_path.parent.name),
        }
    except Exception:
        return None


def _get_skills_list() -> List[Dict[str, Any]]:
    """Get list of all available skills.
    
    Returns:
        List of skill dictionaries
    """
    skills = []
    project_root = Path(__file__).parent.parent.parent.parent
    
    # Check multiple locations for skills
    skill_dirs = [
        project_root / "skills",
        project_root / "src" / "skills",
    ]
    
    for skill_dir in skill_dirs:
        if not skill_dir.exists():
            continue
            
        for skill_path in skill_dir.iterdir():
            if skill_path.is_dir():
                skill_file = skill_path / "SKILL.md"
                if skill_file.exists():
                    skill = _parse_skill_from_file(skill_file)
                    if skill and skill["name"]:
                        skills.append(skill)
    
    return skills


async def api_skills(request: web.Request) -> web.Response:
    """Get list of available skills.
    
    GET /api/skills
    Returns: List of skills with name, description, emoji
    """
    try:
        query = request.query.get('q', '').lower()
        skills = _get_skills_list()
        
        if query:
            # Filter skills by query
            skills = [
                s for s in skills
                if query in s.get('name', '') or query in s.get('description', '')
            ]
        
        return web.json_response({'skills': skills})
    except Exception as e:
        logger.error(f"Error getting skills: {e}")
        return web.json_response({'error': str(e), 'skills': []}, status=500)


def setup_webchat_routes(app: web.Application):
    """Set up WebChat routes.
    
    Routes:
        GET  /chat           - WebChat UI
        GET  /static/*       - Static files (CSS, JS)
        POST /api/chat       - Send message
        GET  /api/sessions   - List sessions
        GET  /api/usage      - Get usage stats
        POST /api/clear      - Clear session
        GET  /api/skills     - Get available skills
    """
    app.router.add_get('/chat', serve_webchat)
    app.router.add_get('/static/{path:.*}', serve_static)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/clear', api_clear)
    app.router.add_get('/api/skills', api_skills)
    
    logger.info("WebChat routes registered:")
    logger.info("  GET  /chat        - WebChat UI")
    logger.info("  GET  /static/*    - Static files (CSS, JS)")
    logger.info("  POST /api/chat    - Send message")
    logger.info("  GET  /api/sessions - List sessions")
    logger.info("  GET  /api/usage    - Get usage stats")
    logger.info("  POST /api/clear   - Clear session")
    logger.info("  GET  /api/skills  - Get available skills")
