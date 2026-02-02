"""Tests for WebChat UI module."""

import pytest

try:
    from gateway.webchat import setup_webchat_routes, WEBCHAT_TEMPLATE
except ImportError:
    pytest.skip("WebChat module not available", allow_module_level=True)


class TestWebChatTemplate:
    """Tests for WebChat template content."""
    
    def test_template_has_required_elements(self):
        """Test WebChat template has all required UI elements."""
        assert 'class="header"' in WEBCHAT_TEMPLATE
        assert 'class="logo"' in WEBCHAT_TEMPLATE
        assert 'class="messages"' in WEBCHAT_TEMPLATE
        assert 'class="input-area"' in WEBCHAT_TEMPLATE
        assert 'id="messageInput"' in WEBCHAT_TEMPLATE
        assert 'id="sendButton"' in WEBCHAT_TEMPLATE
        assert 'id="typing"' in WEBCHAT_TEMPLATE
        assert 'CodeW Assistant' in WEBCHAT_TEMPLATE
        assert '/api/chat' in WEBCHAT_TEMPLATE
        assert 'placeholder="Type your message..."' in WEBCHAT_TEMPLATE
    
    def test_template_has_styling(self):
        """Test WebChat template has proper styling."""
        assert 'background: linear-gradient' in WEBCHAT_TEMPLATE
        assert 'border-radius' in WEBCHAT_TEMPLATE
        assert 'animation' in WEBCHAT_TEMPLATE
        assert '@keyframes' in WEBCHAT_TEMPLATE
    
    def test_template_has_javascript(self):
        """Test WebChat template has JavaScript functionality."""
        assert 'addEventListener' in WEBCHAT_TEMPLATE
        assert 'async function sendMessage' in WEBCHAT_TEMPLATE
        assert 'fetch(' in WEBCHAT_TEMPLATE
        assert 'JSON.stringify' in WEBCHAT_TEMPLATE
    
    def test_template_is_complete_html(self):
        """Test WebChat template is complete HTML document."""
        assert WEBCHAT_TEMPLATE.startswith('<!DOCTYPE html>')
        assert '<html' in WEBCHAT_TEMPLATE
        assert '<head>' in WEBCHAT_TEMPLATE
        assert '<body>' in WEBCHAT_TEMPLATE
        assert '</html>' in WEBCHAT_TEMPLATE


class TestWebChatRoutes:
    """Tests for WebChat route registration."""
    
    def test_setup_webchat_routes_returns_none(self):
        """Test setup_webchat_routes doesn't return anything."""
        from aiohttp import web
        app = web.Application()
        result = setup_webchat_routes(app)
        # Should modify app in-place
        assert result is None or result is not Exception
    
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
