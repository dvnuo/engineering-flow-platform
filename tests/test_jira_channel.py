"""
Tests for Jira Channel multi-instance support.
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestJiraChannelMultiInstance:
    """Test Jira multi-instance functionality."""
    
    def test_get_instance_client_by_name(self):
        """Test getting Jira client by instance name."""
        from src.jira.api import JiraChannel
        
        # Create mock config that returns real dicts
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'api_token': 'token1'},
            {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV', 'username': 'user2', 'api_token': 'token2'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV', 'username': 'user2', 'api_token': 'token2'}
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            client = channel.get_instance_client(name='Development')
            
            assert client is not None
            assert client.base_url == 'https://dev.company.atlassian.net'
            assert client.project == 'DEV'
    
    def test_get_instance_client_by_url(self):
        """Test getting Jira client by URL."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'api_token': 'token1'},
            {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV', 'username': 'user2', 'api_token': 'token2'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'api_token': 'token1'}
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            client = channel.get_instance_client(url='https://company.atlassian.net')
            
            assert client is not None
            assert client.base_url == 'https://company.atlassian.net'
            assert client.project == 'PROD'
    
    def test_get_instance_client_returns_first_if_not_found(self):
        """Test that get_instance_client returns first instance as default."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'api_token': 'token1'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'api_token': 'token1'}
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            client = channel.get_instance_client(name='NonExistent')
            
            # Should return first instance as default
            assert client is not None
            assert client.project == 'PROD'
    
    def test_get_instance_client_no_instances(self):
        """Test get_instance_client when no instances configured returns default channel."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = []
        mock_config.find_jira_instance.return_value = None
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            client = channel.get_instance_client(name='Production')
            
            # Returns default channel with empty config when no instances
            assert client is not None
            assert client.base_url == ''
    
    def test_get_instance_client_single_instance(self):
        """Test get_instance_client with single instance (backward compat)."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user', 'api_token': 'token'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user', 'api_token': 'token'}
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            client = channel.get_instance_client()
            
            assert client is not None
            assert client.base_url == 'https://company.atlassian.net'


class TestJiraChannelBasic:
    """Test basic Jira Channel functionality."""
    
    def test_jira_channel_init(self):
        """Test Jira channel initialization."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.jira = {
            'url': 'https://company.atlassian.net',
            'username': 'user@company.com',
            'api_token': 'test-token',
            'project': 'PROJ'
        }
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user@company.com', 'api_token': 'test-token'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.base_url == 'https://company.atlassian.net'
            assert channel.username == 'user@company.com'
            assert channel.project == 'PROJ'
    
    def test_jira_channel_init_defaults(self):
        """Test Jira channel initialization with defaults."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.jira = {}
        mock_config.get_jira_instances.return_value = []
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.base_url == ''
            assert channel.username == ''
    
    def test_jira_channel_enabled(self):
        """Test Jira channel enabled property."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.jira = {'enabled': True}
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.enabled == True
