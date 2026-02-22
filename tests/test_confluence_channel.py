"""
Tests for Confluence Channel multi-instance support.
"""

import pytest
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
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'api_token': 'token1'},
            {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'api_token': 'token2'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'api_token': 'token2'}
        
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
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'api_token': 'token1'},
            {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user2', 'api_token': 'token2'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'api_token': 'token1'}
        
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
            {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'api_token': 'token1'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM', 'username': 'user1', 'api_token': 'token1'}
        
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
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user', 'api_token': 'token'}
        ]
        mock_config.find_confluence_instance.return_value = {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user', 'api_token': 'token'}
        
        with patch('src.confluence.api.config', mock_config):
            channel = ConfluenceChannel()
            client = channel.get_instance_client()
            
            assert client is not None
            assert client.base_url == 'https://company.atlassian.net/wiki'


class TestConfluenceChannelBasic:
    """Test basic Confluence Channel functionality."""
    
    def test_confluence_channel_init(self):
        """Test Confluence channel initialization."""
        from src.confluence.api import ConfluenceChannel
        
        mock_config = MagicMock()
        mock_config.confluence = {
            'url': 'https://company.atlassian.net/wiki',
            'username': 'user@company.com',
            'api_token': 'test-token',
            'space': 'DEV'
        }
        mock_config.get_confluence_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net/wiki', 'space': 'DEV', 'username': 'user@company.com', 'api_token': 'test-token'}
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
