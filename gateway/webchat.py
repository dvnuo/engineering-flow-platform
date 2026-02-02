"""WebChat UI and HTTP server for OpenClaw Mini.

A simple web interface to chat with the agent directly.
"""

import asyncio
import html
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from aiohttp import web

from agent.core import Agent as AgentCore
from config import config
from session.manager import session_manager
from session.persistence import session_store
from session.usage import usage_tracker

logger = logging.getLogger(__name__)


# HTML Template for WebChat
WEBCHAT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CodeW - Chat</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        .header {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            padding: 16px 24px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: white;
            font-size: 18px;
        }
        
        .header-title {
            color: white;
            font-size: 18px;
            font-weight: 600;
        }
        
        .header-subtitle {
            color: rgba(255, 255, 255, 0.6);
            font-size: 12px;
        }
        
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            max-width: 800px;
            margin: 0 auto;
            width: 100%;
            padding: 16px;
        }
        
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .message {
            display: flex;
            gap: 12px;
            max-width: 85%;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .message.user {
            align-self: flex-end;
            flex-direction: row-reverse;
        }
        
        .message.assistant {
            align-self: flex-start;
        }
        
        .message.error {
            align-self: flex-start;
            max-width: 90%;
        }
        
        .avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            flex-shrink: 0;
        }
        
        .message.user .avatar {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .message.assistant .avatar {
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
        }
        
        .message.error .avatar {
            background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
            color: white;
        }
        
        .message-content {
            padding: 12px 16px;
            border-radius: 16px;
            font-size: 14px;
            line-height: 1.5;
            word-wrap: break-word;
        }
        
        .message.user .message-content {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 4px;
        }
        
        .message.assistant .message-content {
            background: rgba(255, 255, 255, 0.1);
            color: white;
            border-bottom-left-radius: 4px;
        }
        
        .message.error .message-content {
            background: rgba(235, 51, 73, 0.2);
            color: #ff6b6b;
            border-bottom-left-radius: 4px;
        }
        
        .message-timestamp {
            font-size: 11px;
            color: rgba(255, 255, 255, 0.4);
            margin-top: 4px;
            padding: 0 8px;
        }
        
        .message.user .message-timestamp {
            text-align: right;
        }
        
        .input-area {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 16px;
            margin-top: 16px;
        }
        
        .input-wrapper {
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }
        
        .input-field {
            flex: 1;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 12px 16px;
            color: white;
            font-size: 14px;
            resize: none;
            min-height: 44px;
            max-height: 120px;
            font-family: inherit;
        }
        
        .input-field:focus {
            outline: none;
            border-color: rgba(102, 126, 234, 0.5);
        }
        
        .input-field::placeholder {
            color: rgba(255, 255, 255, 0.4);
        }
        
        .send-button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            border-radius: 12px;
            padding: 12px 20px;
            color: white;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .send-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }
        
        .send-button:active {
            transform: translateY(0);
        }
        
        .send-button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        
        .typing-indicator {
            display: none;
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            margin-left: 44px;
            margin-bottom: 12px;
        }
        
        .typing-indicator.show {
            display: flex;
            gap: 4px;
        }
        
        .typing-dot {
            width: 8px;
            height: 8px;
            background: rgba(255, 255, 255, 0.4);
            border-radius: 50%;
            animation: typing 1.4s infinite ease-in-out;
        }
        
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-4px); }
        }
        
        .status-bar {
            display: flex;
            justify-content: space-between;
            padding: 8px 16px;
            font-size: 12px;
            color: rgba(255, 255, 255, 0.4);
        }
        
        .welcome-message {
            text-align: center;
            color: rgba(255, 255, 255, 0.6);
            padding: 40px 20px;
        }
        
        .welcome-message h2 {
            color: white;
            margin-bottom: 12px;
        }
        
        .clear-button {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
            padding: 8px 16px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 12px;
            transition: background 0.2s;
        }
        
        .clear-button:hover {
            background: rgba(255, 255, 255, 0.2);
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">CW</div>
        <div>
            <div class="header-title">CodeW Assistant</div>
            <div class="header-subtitle">AI-Powered Personal Assistant</div>
        </div>
    </div>
    
    <div class="chat-container">
        <div class="messages" id="messages">
            <div class="welcome-message">
                <h2>👋 Welcome!</h2>
                <p>I'm your AI assistant. How can I help you today?</p>
            </div>
        </div>
        
        <div class="typing-indicator" id="typing">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
        
        <div class="input-area">
            <div class="input-wrapper">
                <textarea 
                    class="input-field" 
                    id="messageInput" 
                    placeholder="Type your message..."
                    rows="1"
                ></textarea>
                <button class="send-button" id="sendButton">Send</button>
            </div>
        </div>
    </div>
    
    <div class="status-bar">
        <span id="status">Ready</span>
        <span id="tokenCount">Tokens: 0</span>
    </div>
    
    <script>
        const messagesContainer = document.getElementById('messages');
        const messageInput = document.getElementById('messageInput');
        const sendButton = document.getElementById('sendButton');
        const typingIndicator = document.getElementById('typing');
        const statusSpan = document.getElementById('status');
        const tokenCountSpan = document.getElementById('tokenCount');
        
        let isLoading = false;
        let messageCount = 0;
        let totalTokens = 0;
        
        // Auto-resize textarea
        messageInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        });
        
        // Send on Enter (Shift+Enter for new line)
        messageInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        sendButton.addEventListener('click', sendMessage);
        
        function addMessage(role, content, timestamp = null) {
            const welcome = messagesContainer.querySelector('.welcome-message');
            if (welcome) {
                welcome.remove();
            }
            
            const div = document.createElement('div');
            div.className = `message ${role}`;
            
            const avatar = role === 'user' ? 'U' : role === 'assistant' ? 'AI' : '!';
            const time = timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            
            div.innerHTML = `
                <div class="avatar">${avatar}</div>
                <div>
                    <div class="message-content">${escapeHtml(content)}</div>
                    <div class="message-timestamp">${time}</div>
                </div>
            `;
            
            messagesContainer.appendChild(div);
            scrollToBottom();
            messageCount++;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML.replace(/\n/g, '<br>');
        }
        
        function scrollToBottom() {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        
        async function sendMessage() {
            if (isLoading) return;
            
            const content = messageInput.value.trim();
            if (!content) return;
            
            isLoading = true;
            sendButton.disabled = true;
            messageInput.value = '';
            messageInput.style.height = 'auto';
            
            addMessage('user', content);
            
            statusSpan.textContent = 'Thinking...';
            typingIndicator.classList.add('show');
            
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: content,
                        session_id: 'webchat'
                    })
                });
                
                const data = await response.json();
                
                typingIndicator.classList.remove('show');
                
                if (data.error) {
                    addMessage('error', `Error: ${data.error}`);
                    statusSpan.textContent = 'Error';
                } else {
                    addMessage('assistant', data.response);
                    
                    if (data.usage) {
                        totalTokens += data.usage.total_tokens || 0;
                        tokenCountSpan.textContent = `Tokens: ${totalTokens}`;
                    }
                    
                    statusSpan.textContent = 'Ready';
                }
            } catch (error) {
                typingIndicator.classList.remove('show');
                addMessage('error', `Connection error: ${error.message}`);
                statusSpan.textContent = 'Disconnected';
            } finally {
                isLoading = false;
                sendButton.disabled = false;
                messageInput.focus();
            }
        }
    </script>
</body>
</html>
"""


async def serve_webchat(request: web.Request) -> web.Response:
    """Serve the WebChat UI."""
    return web.Response(
        text=WEBCHAT_TEMPLATE,
        content_type='text/html',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        }
    )


async def api_chat(request: web.Request) -> web.Response:
    """Handle chat API requests."""
    try:
        data = await request.json()
        message = data.get('message', '').strip()
        session_id = data.get('session_id', 'webchat')
        
        if not message:
            return web.json_response({'error': 'Empty message'}, status=400)
        
        # Get or create session
        session = session_manager.get_session(session_id)
        
        # Add user message to session
        session_manager.add_message(session_id, 'user', message)
        
        # Run agent (history is managed internally by session_manager)
        agent = AgentCore()
        response = await agent.process(
            message=message,
            session_id=session_id,
            user_name="webchat-user"
        )
        
        result = {"response": response}
        
        # Record usage
        if result.get('usage'):
            usage_tracker.record_usage(
                session_id=session_id,
                response=result['usage'],
                model=config.llm.get('model', 'unknown'),
                channel='webchat'
            )
        
        # Add assistant response to session
        session_manager.add_message(session_id, 'assistant', result.get('response', ''))
        
        return web.json_response({
            'response': result.get('response', ''),
            'session_id': session_id,
            'usage': result.get('usage')
        })
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_sessions(request: web.Request) -> web.Response:
    """List active sessions."""
    try:
        sessions = await session_store.list_sessions()
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
        
        session_manager.clear_history(session_id)
        await session_store.delete_session(session_id)
        usage_tracker.clear_session_usage(session_id)
        
        return web.json_response({'success': True})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


def setup_webchat_routes(app: web.Application):
    """Set up WebChat routes."""
    app.router.add_get('/chat', serve_webchat)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/clear', api_clear)
    
    logger.info("WebChat routes registered: /chat, /api/chat, /api/sessions, /api/usage, /api/clear")
