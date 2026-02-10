"""WebChat UI and HTTP server for Engineering Flow Platform.

A simple web interface to chat with the agent directly.

UNIQUE_MARKER_12345
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

from src.agents.core import Agent as AgentCore
from src.agents.errors import extract_error_details, LLMError
from src.config import config
from src.sessions.manager import session_manager
from src.sessions.persistence import session_persistence
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
        
        logger.info(f"[api_chat] Processing message for session: {session_id}")
        
        # Initialize session manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        # Run agent (history is managed internally by session_manager)
        agent = AgentCore()
        result = await agent.process(
            message=message,
            session_id=session_id,
            user_name="webchat-user",
            track_usage=True,
            reasoning_replay=reasoning_replay,
        )
        
        # Force save session to persistence
        session = await session_manager.get_session(session_id)
        logger.info(f"[api_chat] Session after agent.process(): {session is not None}")
        if session and session.get("history"):
            logger.info(f"[api_chat] Saving session with {len(session['history'])} messages")
            await session_persistence.save_session(
                session_id=session_id,
                channel=session.get("channel", ""),
                messages=session["history"],
                metadata=session.get("metadata", {}),
            )
        else:
            logger.warning(f"[api_chat] No session or empty history for {session_id}")
        
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


async def api_chat_stream(request: web.Request) -> web.StreamResponse:
    """Handle streaming chat API requests (Server-Sent Events).
    
    POST /api/chat/stream
    Body: {"message": "...", "session_id": "optional"}
    
    Returns: text/event-stream with chunks of the response
    """
    try:
        data = await request.json()
        message = (data.get('message') or '').strip()
        session_id = data.get('session_id', f'webchat_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}')
        
        if not message:
            response = web.json_response({'error': 'Empty message'}, status=400)
            return response
        
        # Create streaming response
        response = web.StreamResponse(
            status=200,
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',  # Disable nginx buffering
            }
        )
        
        await response.prepare()
        
        # Send start event
        await response.write(f"event: start\ndata: {json.dumps({'session_id': session_id})}\n\n")
        
        # Run agent and stream response
        agent = AgentCore()
        
        async def stream_callback(chunk: str):
            """Callback for streaming chunks."""
            try:
                # Escape newlines for SSE format
                escaped = chunk.replace('\n', '\\n').replace('\r', '\\r')
                await response.write(f"event: chunk\ndata: {escaped}\n\n")
            except Exception as e:
                logger.error(f"Error writing stream chunk: {e}")
        
        result = await agent.process(
            message=message,
            session_id=session_id,
            user_name="webchat-user",
            track_usage=True,
            stream_callback=stream_callback,
        )
        
        response_text = result.get("response", "") if result else ""
        usage = result.get("usage", {}) if result else {}
        
        # Record usage
        if usage:
            usage_tracker.record_usage(
                provider="openai",
                model=config.llm.get('model', 'unknown'),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                session_id=session_id,
                task_type="chat"
            )
        
        # Send usage data
        usage_data = json.dumps({
            'usage': usage,
            'session_id': session_id,
        })
        await response.write(f"event: usage\ndata: {usage_data}\n\n")
        
        # Send done event
        await response.write(f"event: done\ndata: \n\n")
        
        return response
        
    except json.JSONDecodeError:
        response = web.json_response({'error': 'Invalid JSON'}, status=400)
        return response
    except Exception as e:
        logger.error(f"Stream error: {e}")
        error_data = json.dumps({'error': str(e)})
        try:
            await response.write(f"event: error\ndata: {error_data}\n\n")
        except Exception:
            pass
        return web.Response(status=500, text=str(e))


async def api_sessions(request: web.Request) -> web.Response:
    """List recent sessions with details.
    
    GET /api/sessions?limit=10
    Returns: List of sessions with name, last message, timestamp
    
    VERSION: FINAL_TEST_2026_02_10_17_10
    """
    import time
    start_time = time.time()
    logger.info(f"[api_sessions FINAL_TEST_2026_02_10_17_10] ENTERING - checking version")
    logger.info(f"[FINAL_TEST] Source file: /root/engineering-flow-platform/src/gateway/webchat.py")
    try:
        # Initialize session manager if needed
        if not session_manager._initialized:
            logger.info("[api_sessions] Initializing session manager")
            await session_manager.initialize()
        
        limit = int(request.query.get('limit', 10))
        session_ids = await session_manager.list_sessions()
        logger.info(f"[api_sessions] Found {len(session_ids)} sessions: {session_ids[:5]}")
        
        # Format sessions with details, filter out empty sessions
        detailed_sessions = []
        for session_id in session_ids[:limit]:
            # Get session info
            session_info = await session_manager.get_session_info(session_id)
            
            if not session_info:
                logger.warning(f"[api_sessions] No info for session: {session_id}")
                continue
            
            history = session_info.get('history', [])
            
            # Skip empty sessions (no user messages)
            user_messages = [msg for msg in history if msg.get('role') == 'user']
            if not user_messages:
                logger.info(f"[api_sessions] Skipping empty session: {session_id}")
                continue
            
            # Get first user message as session name
            first_user_msg = user_messages[0]
            session_name = (first_user_msg.get('content', '') or 'New Chat')[:30]
            if not session_name.strip():
                session_name = 'New Chat'
            
            # Get last message preview
            last_message = ''
            for msg in reversed(history):
                if msg.get('role') in ('user', 'assistant'):
                    last_message = (msg.get('content', '') or '')[:50]
                    break
            
            detailed_sessions.append({
                'session_id': session_id,
                'name': session_name,
                'last_message': last_message,
                'updated_at': session_info.get('updated_at', datetime.utcnow().isoformat()),
                'message_count': len(user_messages),
                '_marker': 'FIXED_2026_02_10_16_58',  # Version marker
            })
            logger.info(f"[api_sessions] Added session: {session_id} -> name='{session_name}'")
        
        logger.info(f"[api_sessions] Returning {len(detailed_sessions)} sessions")
        return web.json_response({'sessions': detailed_sessions})
    except Exception as e:
        logger.error(f"[api_sessions] ERROR: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_load_session(request: web.Request) -> web.Response:
    """Load session messages.
    
    GET /api/sessions/{session_id}
    Returns: Session messages
    """
    try:
        # Initialize session manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        session_id = request.match_info.get('session_id', '')
        if not session_id:
            return web.json_response({'error': 'Session ID required'}, status=400)
        
        session_info = await session_manager.get_session(session_id)
        
        if not session_info:
            return web.json_response({'error': 'Session not found'}, status=404)
        
        history = session_info.get('history', [])
        
        # Extract session name from first user message
        session_name = 'New Chat'
        for msg in history:
            if msg.get('role') == 'user':
                content = msg.get('content', '') or 'New Chat'
                session_name = content[:30]
                break
        
        return web.json_response({
            'session_id': session_id,
            'name': session_name,
            'messages': history,
        })
    except Exception as e:
        logger.error(f"Error loading session: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_browse_files(request: web.Request) -> web.Response:
    """Browse file directory.
    
    GET /api/files?path=/workspace
    Returns: List of files and directories
    """
    try:
        path = request.query.get('path', Path.home() / ".efp/workspace/engineering-flow")
        base_path = Path(path)
        
        if not base_path.exists():
            return web.json_response({'error': 'Path not found', 'path': path}, status=404)
        
        items = []
        for item in sorted(base_path.iterdir()):
            items.append({
                'name': item.name,
                'path': str(item.resolve()),
                'is_dir': item.is_dir(),
                'is_file': item.is_file(),
            })
        
        return web.json_response({'path': str(base_path.resolve()), 'items': items})
    except Exception as e:
        logger.error(f"Error browsing files: {e}")
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
        examples = []
        in_examples = False
        
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('# ') and not name:
                name = line[2:].strip().replace(' Skill', '').lower()
            elif line.startswith('## Examples'):
                in_examples = True
                continue
            elif in_examples:
                if line.startswith('```') or line.startswith('## '):
                    in_examples = False
                    continue
                # Extract example commands from comments
                if line.startswith('# '):
                    example = line[2:].strip()
                    if example and len(example) < 80:  # Limit example length
                        examples.append(example)
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
            "examples": examples[:3],  # Limit to 3 examples
        }
    except Exception:
        return None


def _get_skills_list() -> List[Dict[str, Any]]:
    """Get list of all available skills.
    
    Returns:
        List of skill dictionaries
    """
    skills = []
    project_root = Path(__file__).resolve().parent.parent.parent
    
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
        GET  /             - WebChat UI (root)
        GET  /chat         - WebChat UI (backward compatibility)
        GET  /static/*     - Static files (CSS, JS)
        POST /api/chat     - Send message
        POST /api/chat/stream - Send message (streaming SSE)
        GET  /api/sessions - List recent sessions
        GET  /api/sessions/{session_id} - Load session messages
        GET  /api/files    - Browse files
        GET  /api/usage   - Get usage stats
        POST /api/clear   - Clear session
        GET  /api/skills  - Get available skills
    """
    app.router.add_get('/', serve_webchat)
    app.router.add_get('/chat', serve_webchat)  # Backward compatibility
    app.router.add_get('/static/{path:.*}', serve_static)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_post('/api/chat/stream', api_chat_stream)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/sessions/{session_id}', api_load_session)
    app.router.add_get('/api/files', api_browse_files)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/clear', api_clear)
    app.router.add_get('/api/skills', api_skills)
    
    logger.info("WebChat routes registered:")
    logger.info("  GET  /              - WebChat UI (root)")
    logger.info("  GET  /chat         - WebChat UI (backward compat)")
    logger.info("  GET  /static/*     - Static files (CSS, JS)")
    logger.info("  POST /api/chat     - Send message")
    logger.info("  POST /api/chat/stream - Send message (streaming SSE)")
    logger.info("  GET  /api/sessions - List recent sessions")
    logger.info("  GET  /api/sessions/{id} - Load session messages")
    logger.info("  GET  /api/files    - Browse files")
    logger.info("  GET  /api/usage   - Get usage stats")
    logger.info("  POST /api/clear   - Clear session")
    logger.info("  GET  /api/skills  - Get available skills")
