"""
Tests for Confluence Channel multi-instance support.
"""

import pytest
from tests._optional_runtime_deps import skip_if_missing_ruamel_yaml

skip_if_missing_ruamel_yaml("full runtime dependencies unavailable (missing ruamel.yaml)")

import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestConfluenceChannelMultiInstance:
    """Test Confluence multi-instance functionality."""
    
    def test_get_instance_client_by_name(self):
        """Test getting Confluence client by instance name."""
        from src.confluence.api import ConfluenceChannel
        
        # Create mock config that returns real dicts
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'password': 'pass1'},
            {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'password': 'pass2'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'password': 'pass2'}
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client(name='Dev Wiki')
            
            assert client is not None
            assert client.base_url == 'https://dev.company.atlassian.net/wiki'
            assert client.space == 'DEV'
    
    def test_get_instance_client_by_url(self):
        """Test getting Confluence client by URL."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'password': 'pass1'},
            {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'password': 'pass2'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'password': 'pass1'}
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client(url='https://company.atlassian.net/wiki')
            
            assert client is not None
            assert client.base_url == 'https://company.atlassian.net/wiki'
            assert client.space == 'TEAM'
    
    def test_get_instance_client_returns_first_if_not_found(self):
        """Test that get_instance_client returns first instance as default."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'password': 'pass1'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'password': 'pass1'}
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client(name='NonExistent')
            
            # Should return first instance as default
            assert client is not None
            assert client.space == 'TEAM'
    
    def test_get_instance_client_no_instances(self):
        """Test get_instance_client when no instances configured returns default channel."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = []
        mock_config.find_confluence_instance.return_value = None
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client(name='Company Wiki')
            
            # Returns default channel with empty config when no instances
            assert client is not None
            assert client.base_url == ''
    
    def test_get_instance_client_single_instance(self):
        """Test get_instance_client with single instance (backward compat)."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user', 'password': 'pass'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user', 'password': 'pass'}
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client()
            
            assert client is not None
            assert client.base_url == 'https://company.atlassian.net/wiki'

    def test_get_instance_client_strict_returns_none_when_url_unmatched(self):
        """Strict mode should return None instead of default fallback when URL is unmatched."""
        from src.confluence.api import ConfluenceChannel

        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'password': 'pass1'},
            {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'password': 'pass2'}
        ]
        mock_config.find_confluence_instance.return_value = None

        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client(
                url='https://unknown.example/wiki/spaces/X/pages/1',
                strict=True,
            )

            assert client is None
            mock_config.find_confluence_instance.assert_called_once_with(
                url='https://unknown.example/wiki/spaces/X/pages/1',
                name=None,
                strict=True,
            )


class TestConfluenceChannelBasic:
    """Test basic Confluence Channel functionality."""
    
    def test_confluence_channel_init(self):
        """Test Confluence channel initialization."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.confluence = {
            'url': 'https://company.atlassian.net/wiki',
            'username': 'user@company.com',
            'password': 'test-password',
            'space': 'DEV'
        }
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user@company.com', 'password': 'test-password'}
        ]
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.base_url == 'https://company.atlassian.net/wiki'
            assert channel.username == 'user@company.com'
            assert channel.space == 'DEV'
    
    def test_confluence_channel_init_defaults(self):
        """Test Confluence channel initialization with defaults."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.confluence = {}
        mock_config.get_confluence_instances.return_value = []
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.base_url == ''
            assert channel.username == ''
            assert channel.password == ''
            assert channel.token == ''


class TestConfluenceChannelAuth:
    """Test Confluence authentication methods."""
    
    def test_bearer_token_auth(self):
        """Test Bearer token authentication."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'token': 'my-bearer-token'}
        ]
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.token == 'my-bearer-token'
            header = channel._get_auth_header()
            assert header == {'Authorization': 'Bearer my-bearer-token'}
    
    def test_basic_auth_with_password(self):
        """Test Basic auth with username and password."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user@company.com', 'password': 'secret'}
        ]
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.username == 'user@company.com'
            assert channel.password == 'secret'
            header = channel._get_auth_header()
            assert 'Authorization' in header
            assert header['Authorization'].startswith('Basic ')
    
    def test_is_configured_with_bearer_token(self):
        """Test is_configured returns True with Bearer token."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'token': 'my-token'}
        ]
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.is_configured() == True
    
    def test_is_configured_with_basic_auth(self):
        """Test is_configured returns True with username+password."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user', 'password': 'pass'}
        ]
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.is_configured() == True
    
    def test_is_configured_returns_false_when_not_configured(self):
        """Test is_configured returns False when not configured."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.get_confluence_instances.return_value = []
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            
            assert channel.is_configured() == False


class TestConfluenceChannelReinit:
    """Test ConfluenceChannel reinit method."""
    
    def test_reinit_with_instances(self):
        """Test reinit with new instances."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.confluence = {'enabled': True}
        
        # Initial instances
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Old', 'url': 'https://old.atlassian.net/wiki', 'space': 'OLD'}
        ]
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            assert channel.base_url == 'https://old.atlassian.net/wiki'
            
            # Reinit with new instances
            mock_config.get_confluence_instances.return_value = [
                {'name': 'New', 'url': 'https://new.atlassian.net/wiki', 'space': 'NEW', 'username': 'user', 'password': 'pass'}
            ]
            channel.reinit()
            
            assert channel.base_url == 'https://new.atlassian.net/wiki'
            assert channel.username == 'user'
            assert channel.password == 'pass'
    
    def test_reinit_without_instances(self):
        """Test reinit with no instances - should initialize all auth fields."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.confluence = {'enabled': True}
        mock_config.get_confluence_instances.return_value = []
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            channel.reinit()
            
            # Should have all auth fields initialized
            assert channel.base_url == ''
            assert channel.username == ''
            assert channel.password == ''
            assert channel.token == ''
            assert channel.space == ''
