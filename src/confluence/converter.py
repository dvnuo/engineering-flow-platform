"""
Confluence Format Converter - Convert between Markdown and Storage Format.

Uses:
- confluence-markdown-exporter for Storage → Markdown
- markdown-to-confluence for Markdown → Storage
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Track unsupported elements for warning
UNSUPPORTED_ELEMENTS = {
    "macros": ["info", "warning", "tip", "note", "panel", "status", "expand", "layout"],
    "description": "Elements that may not round-trip perfectly between Markdown and Storage"
}


class MarkdownConverter:
    """Convert between Confluence Storage Format and Markdown."""
    
    def __init__(self):
        self._markdown2confluence = None
        self._exporter = None
    
    def _get_markdown_to_confluence(self):
        """Lazy load markdown-to-confluence."""
        if self._markdown2confluence is None:
            try:
                from markdown_to_confluence import ConfluenceConverter
                self._markdown2confluence = ConfluenceConverter()
            except ImportError:
                logger.warning("markdown-to-confluence not installed, using fallback")
                self._markdown2confluence = None
        return self._markdown2confluence
    
    def _get_exporter(self):
        """Lazy load confluence-markdown-exporter."""
        if self._exporter is None:
            try:
                from confluence_markdown_exporter import ConfluenceMarkdownExporter
                self._exporter = ConfluenceMarkdownExporter
            except ImportError:
                logger.warning("confluence-markdown-exporter not installed, using fallback")
                self._exporter = None
        return self._exporter
    
    def markdown_to_storage(self, markdown_text: str) -> str:
        """
        Convert Markdown to Confluence Storage Format.
        
        Args:
            markdown_text: Markdown content
            
        Returns:
            Storage Format (XHTML) string
        """
        converter = self._get_markdown_to_confluence()
        
        if converter:
            try:
                return converter.convert(markdown_text)
            except Exception as e:
                logger.error(f"markdown-to-confluence conversion failed: {e}")
                # Fall through to basic converter
        
        # Fallback: basic conversion
        return self._basic_markdown_to_storage(markdown_text)
    
    def storage_to_markdown(self, storage_text: str) -> str:
        """
        Convert Confluence Storage Format to Markdown.
        
        Args:
            storage_text: Storage Format (XHTML) content
            
        Returns:
            Markdown string
        """
        exporter_class = self._get_exporter()
        
        if exporter_class:
            try:
                # exporter expects page data dict
                page_data = {"body": {"storage": {"value": storage_text}}}
                exporter = exporter_class(page_data)
                return exporter.to_markdown()
            except Exception as e:
                logger.error(f"confluence-markdown-exporter conversion failed: {e}")
                # Fall through to basic converter
        
        # Fallback: basic conversion
        return self._basic_storage_to_markdown(storage_text)
    
    def _basic_markdown_to_storage(self, md: str) -> str:
        """Basic Markdown to Storage conversion (fallback)."""
        import html
        
        lines = md.split('\n')
        result = []
        in_code_block = False
        code_lang = ""
        
        for line in lines:
            # Code blocks
            if line.startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    result.append(f'<ac:code-block lang="{code_lang}">')
                else:
                    in_code_block = False
                    result.append('</ac:code-block>')
                continue
            
            if in_code_block:
                result.append(line)
                continue
            
            # Headers
            if line.startswith('#### '):
                result.append(f'<h4>{html.escape(line[5:])}</h4>')
            elif line.startswith('### '):
                result.append(f'<h3>{html.escape(line[4:])}</h3>')
            elif line.startswith('## '):
                result.append(f'<h2>{html.escape(line[3:])}</h2>')
            elif line.startswith('# '):
                result.append(f'<h1>{html.escape(line[2:])}</h1>')
            # Bold/Italic
            elif '**' in line:
                line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
                line = re.sub(r'\*(.+?)\*', r'<em>\1</em>', line)
                result.append(f'<p>{line}</p>')
            # Lists
            elif line.startswith('- ') or line.startswith('* '):
                result.append(f'<ul><li>{html.escape(line[2:])}</li></ul>')
            elif line.strip().isdot() or re.match(r'^\d+\.\s', line):
                match = re.match(r'^(\d+)\.\s(.+)', line)
                if match:
                    result.append(f'<ol><li>{html.escape(match.group(2))}</li></ol>')
            # Links
            elif '[' in line and '](' in line:
                line = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', line)
                result.append(f'<p>{line}</p>')
            # Images
            elif '![' in line and '](' in line:
                line = re.sub(r'!\[(.*?)\]\((.+?)\)', 
                    r'<ac:image><ri:url ri:value="\2"/></ac:image>', line)
                result.append(line)
            # Horizontal rule
            elif line.strip() == '---':
                result.append('<hr/>')
            # Code inline
            elif '`' in line:
                line = re.sub(r'`(.+?)`', r'<code>\1</code>', line)
                result.append(f'<p>{line}</p>')
            elif line.strip():
                result.append(f'<p>{html.escape(line)}</p>')
        
        return '\n'.join(result)
    
    def _basic_storage_to_markdown(self, storage: str) -> str:
        """Basic Storage to Markdown conversion (fallback)."""
        import html
        import re
        
        md = storage
        
        # Code blocks
        md = re.sub(
            r'<ac:code-block lang="(\w+)">(.*?)</ac:code-block>',
            r'```\1\n\2\n```',
            md,
            flags=re.DOTALL
        )
        
        # Headers
        md = re.sub(r'<h1>(.*?)</h1>', r'# \1\n', md)
        md = re.sub(r'<h2>(.*?)</h2>', r'## \1\n', md)
        md = re.sub(r'<h3>(.*?)</h3>', r'### \1\n', md)
        md = re.sub(r'<h4>(.*?)</h4>', r'#### \1\n', md)
        
        # Bold/Italic
        md = re.sub(r'<strong>(.*?)</strong>', r'**\1**', md)
        md = re.sub(r'<b>(.*?)</b>', r'**\1**', md)
        md = re.sub(r'<em>(.*?)</em>', r'*\1*', md)
        md = re.sub(r'<i>(.*?)</i>', r'*\1*', md)
        
        # Links
        md = re.sub(r'<a href="([^"]+)">(.*?)</a>', r'[\2](\1)', md)
        
        # Images
        md = re.sub(
            r'<ac:image><ri:url ri:value="([^"]+)"[^/]*/></ac:image>',
            r'![](\1)',
            md
        )
        
        # Code
        md = re.sub(r'<code>(.*?)</code>', r'`\1`', md)
        
        # Lists (basic)
        md = re.sub(r'<ul><li>(.*?)</li></ul>', r'- \1\n', md)
        md = re.sub(r'<ol><li>(.*?)</li></ol>', r'1. \1\n', md)
        
        # Horizontal rule
        md = re.sub(r'<hr\s*/?>', r'---\n', md)
        
        # Paragraphs
        md = re.sub(r'<p>(.*?)</p>', r'\1\n', md, flags=re.DOTALL)
        
        # Strip remaining tags
        md = re.sub(r'<[^>]+>', '', md)
        
        # Unescape HTML entities
        md = html.unescape(md)
        
        # Clean up
        md = re.sub(r'\n\n\n+', '\n\n', md)
        
        return md.strip()


# Global instance
converter = MarkdownConverter()


# Convenience functions
def markdown_to_storage(markdown_text: str) -> str:
    """Convert Markdown to Confluence Storage Format."""
    return converter.markdown_to_storage(markdown_text)


def storage_to_markdown(storage_text: str) -> str:
    """Convert Confluence Storage Format to Markdown."""
    return converter.storage_to_markdown(storage_text)
