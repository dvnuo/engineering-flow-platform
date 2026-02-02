"""Tests for WebChat UI module."""

import os
import pytest
from pathlib import Path

try:
    from gateway.webchat import setup_webchat_routes, load_template
except ImportError:
    pytest.skip("WebChat module not available", allow_module_level=True)


class TestWebChatTemplate:
    """Tests for WebChat template loading."""
    
    def test_load_template(self):
        """Test loading HTML template from file."""
        html = load_template("webchat.html")
        assert html is not None
        assert len(html) > 0
        assert "<!DOCTYPE html>" in html
    
    def test_template_structure(self):
        """Test template has correct structure."""
        html = load_template("webchat.html")
        
        # Check for key elements
        assert 'class="header"' in html
        assert 'class="chat-container"' in html
        assert 'class="messages"' in html
        assert 'id="messageInput"' in html
        assert 'id="sendButton"' in html
        assert 'id="typing"' in html
    
    def test_template_links_static(self):
        """Test template links to static CSS and JS files."""
        html = load_template("webchat.html")
        
        assert '/static/css/webchat.css' in html
        assert '/static/js/webchat.js' in html


class TestWebChatStaticFiles:
    """Tests for static files."""
    
    def test_css_file_exists(self):
        """Test CSS file exists and has content."""
        css_path = Path(__file__).parent.parent / "gateway" / "static" / "css" / "webchat.css"
        assert css_path.exists()
        
        with open(css_path, 'r') as f:
            css = f.read()
        
        assert len(css) > 0
        assert '.message {' in css
        assert '.input-field {' in css
        assert '.send-button {' in css
    
    def test_js_file_exists(self):
        """Test JS file exists and has content."""
        js_path = Path(__file__).parent.parent / "gateway" / "static" / "js" / "webchat.js"
        assert js_path.exists()
        
        with open(js_path, 'r') as f:
            js = f.read()
        
        assert len(js) > 0
        assert 'function sendMessage()' in js
        assert 'addMessage(' in js
        assert 'escapeHtml(' in js


class TestWebChatRoutes:
    """Tests for WebChat route registration."""
    
    def test_setup_webchat_routes_returns_none(self):
        """Test setup_webchat_routes modifies app in-place."""
        from aiohttp import web
        app = web.Application()
        result = setup_webchat_routes(app)
        assert result is None
    
    def test_routes_registered(self):
        """Test expected routes are registered."""
        from aiohttp import web
        app = web.Application()
        setup_webchat_routes(app)
        
        routes = [r.resource.canonical for r in app.router.routes() if r.resource]
        
        assert '/chat' in routes
        assert '/api/chat' in routes
        assert '/api/sessions' in routes
        assert '/api/usage' in routes
        assert '/api/clear' in routes
    
    def test_static_route_registered(self):
        """Test static file route is registered."""
        from aiohttp import web
        app = web.Application()
        setup_webchat_routes(app)
        
        routes = [r.resource.canonical for r in app.router.routes() if r.resource]
        
        # Check for static route pattern
        static_routes = [r for r in routes if '/static/' in r]
        assert len(static_routes) > 0


class TestWebChatDirectoryStructure:
    """Tests for proper directory structure."""
    
    def test_templates_directory_exists(self):
        """Test templates directory exists."""
        templates_dir = Path(__file__).parent.parent / "gateway" / "templates"
        assert templates_dir.exists()
        assert templates_dir.is_dir()
    
    def test_static_directory_exists(self):
        """Test static directory exists."""
        static_dir = Path(__file__).parent.parent / "gateway" / "static"
        assert static_dir.exists()
        assert static_dir.is_dir()
    
    def test_css_subdirectory_exists(self):
        """Test CSS subdirectory exists."""
        css_dir = Path(__file__).parent.parent / "gateway" / "static" / "css"
        assert css_dir.exists()
        assert css_dir.is_dir()
    
    def test_js_subdirectory_exists(self):
        """Test JS subdirectory exists."""
        js_dir = Path(__file__).parent.parent / "gateway" / "static" / "js"
        assert js_dir.exists()
        assert js_dir.is_dir()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
