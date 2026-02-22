"""
Tests for multi-instance Jira and Confluence configuration.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestJiraInstances:
    """Test Jira multi-instance configuration."""
    
    def test_get_jira_instances_single(self):
        """Test getting single Jira instance (backward compatibility)."""
        from config import Config
        
        # Mock config with old format
        config = Config.__new__(Config)
        config._config = {
            'jira': {
                'enabled': True,
                'url': 'https://company.atlassian.net',
                'username': 'user@company.com',
                'api_token': 'test-token',
                'project': 'PROJ'
            }
        }
        
        instances = config.get_jira_instances()
        assert len(instances) == 1
        assert instances[0]['url'] == 'https://company.atlassian.net'
        assert instances[0]['project'] == 'PROJ'
    
    def test_get_jira_instances_multiple(self):
        """Test getting multiple Jira instances."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'jira': {
                'enabled': True,
                'instances': [
                    {'name': 'Production', 'url': 'https://company.atlassian.net', 'project': 'PROD'},
                    {'name': 'Development', 'url': 'https://dev.company.atlassian.net', 'project': 'DEV'}
                ]
            }
        }
        
        instances = config.get_jira_instances()
        assert len(instances) == 2
        assert instances[0]['name'] == 'Production'
        assert instances[1]['name'] == 'Development'
    
    def test_find_jira_instance_by_url(self):
        """Test finding Jira instance by URL."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'jira': {
                'enabled': True,
                'instances': [
                    {'name': 'Production', 'url': 'https://company.atlassian.net'},
                    {'name': 'Development', 'url': 'https://dev.company.atlassian.net'}
                ]
            }
        }
        
        result = config.find_jira_instance(url='https://dev.company.atlassian.net')
        assert result is not None
        assert result['name'] == 'Development'
    
    def test_find_jira_instance_by_name(self):
        """Test finding Jira instance by name."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'jira': {
                'enabled': True,
                'instances': [
                    {'name': 'Production', 'url': 'https://company.atlassian.net'},
                    {'name': 'Development', 'url': 'https://dev.company.atlassian.net'}
                ]
            }
        }
        
        result = config.find_jira_instance(name='Production')
        assert result is not None
        assert result['url'] == 'https://company.atlassian.net'
    
    def test_find_jira_instance_not_found(self):
        """Test finding non-existent Jira instance returns first instance as default."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'jira': {
                'enabled': True,
                'instances': [
                    {'name': 'Production', 'url': 'https://company.atlassian.net'}
                ]
            }
        }
        
        result = config.find_jira_instance(name='NonExistent')
        # Returns first instance as default fallback
        assert result is not None
        assert result['name'] == 'Production'
    
    def test_find_jira_instance_no_config(self):
        """Test finding Jira instance when not configured."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {'jira': {'enabled': False}}
        
        result = config.find_jira_instance(name='Production')
        assert result is None


class TestConfluenceInstances:
    """Test Confluence multi-instance configuration."""
    
    def test_get_confluence_instances_single(self):
        """Test getting single Confluence instance (backward compatibility)."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'confluence': {
                'enabled': True,
                'url': 'https://company.atlassian.net/wiki',
                'username': 'user@company.com',
                'api_token': 'test-token',
                'space': 'DEV'
            }
        }
        
        instances = config.get_confluence_instances()
        assert len(instances) == 1
        assert instances[0]['url'] == 'https://company.atlassian.net/wiki'
        assert instances[0]['space'] == 'DEV'
    
    def test_get_confluence_instances_multiple(self):
        """Test getting multiple Confluence instances."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'confluence': {
                'enabled': True,
                'instances': [
                    {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki', 'space': 'TEAM'},
                    {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki', 'space': 'DEV'}
                ]
            }
        }
        
        instances = config.get_confluence_instances()
        assert len(instances) == 2
        assert instances[0]['name'] == 'Company Wiki'
        assert instances[1]['name'] == 'Dev Wiki'
    
    def test_find_confluence_instance_by_url(self):
        """Test finding Confluence instance by URL."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'confluence': {
                'enabled': True,
                'instances': [
                    {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki'},
                    {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki'}
                ]
            }
        }
        
        result = config.find_confluence_instance(url='https://dev.company.atlassian.net/wiki')
        assert result is not None
        assert result['name'] == 'Dev Wiki'
    
    def test_find_confluence_instance_by_name(self):
        """Test finding Confluence instance by name."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'confluence': {
                'enabled': True,
                'instances': [
                    {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki'},
                    {'name': 'Dev Wiki', 'url': 'https://dev.company.atlassian.net/wiki'}
                ]
            }
        }
        
        result = config.find_confluence_instance(name='Company Wiki')
        assert result is not None
        assert result['url'] == 'https://company.atlassian.net/wiki'
    
    def test_find_confluence_instance_not_found(self):
        """Test finding non-existent Confluence instance returns first instance as default."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'confluence': {
                'enabled': True,
                'instances': [
                    {'name': 'Company Wiki', 'url': 'https://company.atlassian.net/wiki'}
                ]
            }
        }
        
        result = config.find_confluence_instance(name='NonExistent')
        # Returns first instance as default fallback
        assert result is not None
        assert result['name'] == 'Company Wiki'


class TestBackwardCompatibility:
    """Test backward compatibility with old single-instance format."""
    
    def test_jira_old_format_migration(self):
        """Test that old Jira format is migrated to new format."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'jira': {
                'enabled': True,
                'url': 'https://old.company.atlassian.net',
                'username': 'user@company.com',
                'api_token': 'token',
                'project': 'OLD'
            }
        }
        
        instances = config.get_jira_instances()
        assert len(instances) == 1
        assert instances[0]['url'] == 'https://old.company.atlassian.net'
        assert instances[0]['project'] == 'OLD'
    
    def test_confluence_old_format_migration(self):
        """Test that old Confluence format is migrated to new format."""
        from config import Config
        
        config = Config.__new__(Config)
        config._config = {
            'confluence': {
                'enabled': True,
                'url': 'https://old.company.atlassian.net/wiki',
                'username': 'user@company.com',
                'api_token': 'token',
                'space': 'OLD'
            }
        }
        
        instances = config.get_confluence_instances()
        assert len(instances) == 1
        assert instances[0]['url'] == 'https://old.company.atlassian.net/wiki'
        assert instances[0]['space'] == 'OLD'
