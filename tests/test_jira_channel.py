"""
Tests for Jira Channel multi-instance support.
"""

import pytest
from tests._optional_runtime_deps import skip_if_missing_ruamel_yaml

skip_if_missing_ruamel_yaml("full runtime dependencies unavailable (missing ruamel.yaml)")

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
            {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'password': 'pass1'},
            {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV', 'username': 'user2', 'password': 'pass2'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV', 'username': 'user2', 'password': 'pass2'}
        
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
            {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'password': 'pass1'},
            {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV', 'username': 'user2', 'password': 'pass2'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'password': 'pass1'}
        
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
            {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'password': 'pass1'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD', 'username': 'user1', 'password': 'pass1'}
        
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
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user', 'password': 'pass'}
        ]
        mock_config.find_jira_instance.return_value = {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user', 'password': 'pass'}
        
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
            'password': 'test-password',
            'project': 'PROJ'
        }
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user@company.com', 'password': 'test-password'}
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
            assert channel.password == ''
            assert channel.token == ''
    
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


class TestJiraChannelAuth:
    """Test Jira authentication methods."""
    
    def test_bearer_token_auth(self):
        """Test Bearer token authentication."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'token': 'my-bearer-token'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.token == 'my-bearer-token'
            assert channel._get_auth_type() == 'Bearer'
            header = channel._get_auth_header()
            assert header == {'Authorization': 'Bearer my-bearer-token'}
    
    def test_basic_auth_with_password(self):
        """Test Basic auth with username and password."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user@company.com', 'password': 'secret'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.username == 'user@company.com'
            assert channel.password == 'secret'
            assert channel._get_auth_type() == 'Basic'
            header = channel._get_auth_header()
            assert 'Authorization' in header
            assert header['Authorization'].startswith('Basic ')
    
    def test_is_configured_with_bearer_token(self):
        """Test is_configured returns True with Bearer token."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'token': 'my-token'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.is_configured() == True
    
    def test_is_configured_with_basic_auth(self):
        """Test is_configured returns True with username+password."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = [
            {'name': 'Default', 'url': 'https://company.atlassian.net', 'project': 'PROJ', 'username': 'user', 'password': 'pass'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.is_configured() == True
    
    def test_is_configured_returns_false_when_not_configured(self):
        """Test is_configured returns False when not configured."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.get_jira_instances.return_value = []
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            
            assert channel.is_configured() == False


class TestJiraChannelReinit:
    """Test JiraChannel reinit method."""
    
    def test_reinit_with_instances(self):
        """Test reinit with new instances."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.jira = {'enabled': True}
        
        # Initial instances
        mock_config.get_jira_instances.return_value = [
            {'name': 'Old', 'url': 'https://old.atlassian.net', 'project': 'OLD'}
        ]
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            assert channel.base_url == 'https://old.atlassian.net'
            
            # Reinit with new instances
            mock_config.get_jira_instances.return_value = [
                {'name': 'New', 'url': 'https://new.atlassian.net', 'project': 'NEW', 'username': 'user', 'password': 'pass'}
            ]
            channel.reinit()
            
            assert channel.base_url == 'https://new.atlassian.net'
            assert channel.username == 'user'
            assert channel.password == 'pass'
    
    def test_reinit_without_instances(self):
        """Test reinit with no instances - should initialize all auth fields."""
        from src.jira.api import JiraChannel
        
        mock_config = MagicMock()
        mock_config.jira = {'enabled': True}
        mock_config.get_jira_instances.return_value = []
        
        with patch('src.jira.api.config', mock_config):
            channel = JiraChannel()
            channel.reinit()
            
            # Should have all auth fields initialized
            assert channel.base_url == ''
            assert channel.username == ''
            assert channel.password == ''
            assert channel.token == ''
            assert channel.project == ''
