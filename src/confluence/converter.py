"""
Confluence Format Converter - Convert between Markdown and Storage Format.

This module provides conversion between Markdown and Confluence Storage Format.
Currently uses a custom regex-based converter for both directions.
"""

import logging
import re
import html

logger = logging.getLogger(__name__)

# Track unsupported elements for warning
UNSUPPORTED_ELEMENTS = {
    "macros": ["info", "warning", "tip", "note", "panel", "status", "expand", "layout"],
    "description": "Elements that may not round-trip perfectly between Markdown and Storage"
}


class MarkdownConverter:
    """Convert between Confluence Storage Format and Markdown using regex-based conversion."""
    
    def __init__(self):
        pass  # No external dependencies needed
    
    def markdown_to_storage(self, markdown_text: str) -> str:
        """
        Convert Markdown to Confluence Storage Format.
        
        Uses a custom regex-based converter.
        
        Args:
            markdown_text: Markdown content
            
        Returns:
            Storage Format (XHTML) string
        """
        return self._basic_markdown_to_storage(markdown_text)
    
    def storage_to_markdown(self, storage_text: str) -> str:
        """
        Convert Confluence Storage Format to Markdown.
        
        Uses custom converter to handle Confluence-specific elements.
        
        Args:
            storage_text: Storage Format (XHTML) content
            
        Returns:
            Markdown string
        """
        # Use custom converter for Storage → Markdown
        try:
            result = self._convert_storage_to_markdown(storage_text)
            logger.debug("Successfully converted Storage to Markdown")
            return result
        except Exception as e:
            logger.error(f"Storage to Markdown conversion failed: {e}")
            # Fallback
            return self._basic_storage_to_markdown(storage_text)
    
    def _convert_storage_to_markdown(self, storage: str) -> str:
        """Convert Confluence Storage Format to Markdown with proper handling."""
        import html
        import re
        
        md = storage
        
        # Handle code blocks first (must be before other tags)
        md = re.sub(
            r'<ac:code-block[^>]*lang="([^"]*)"[^>]*>(.*?)</ac:code-block>',
            lambda m: f"```{m.group(1) if m.group(1) else ''}\n{self._unescape_html(m.group(2))}\n```",
            md,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Handle Confluence-specific macros - convert to blockquotes with note
        md = re.sub(
            r'<ac:structured-macro ac:name="(info|warning|tip|note|panel)"[^>]*>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>.*?</ac:structured-macro>',
            lambda m: f"> **[{m.group(1).upper()}]** {self._strip_tags(m.group(2))}",
            md,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Handle expand macro
        md = re.sub(
            r'<ac:structured-macro ac:name="expand"[^>]*>.*?<ac:parameter ac:name="title">([^<]*)</ac:parameter>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>.*?</ac:structured-macro>',
            lambda m: f"### {m.group(1)}\n{m.group(2)}",
            md,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Handle images with attachments
        md = re.sub(
            r'<ac:image[^>]*><ri:attachment[^>]*ri:filename="([^"]*)"[^/]*/></ac:image>',
            r'![\1](attachment:\1)',
            md,
            flags=re.IGNORECASE
        )
        
        # Handle images with URL
        md = re.sub(
            r'<ac:image[^>]*><ri:url[^>]*ri:value="([^"]*)"[^/]*/></ac:image>',
            r'![](\1)',
            md,
            flags=re.IGNORECASE
        )
        
        # Handle links
        md = re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>',
            r'[\2](\1)',
            md,
            flags=re.IGNORECASE
        )
        
        # Headers
        md = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<h5[^>]*>(.*?)</h5>', r'##### \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<h6[^>]*>(.*?)</h6>', r'###### \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        
        # Bold/Italic
        md = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<u[^>]*>(.*?)</u>', r'_\1_', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<strike[^>]*>(.*?)</strike>', r'~~\1~~', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<s[^>]*>(.*?)</s>', r'~~\1~~', md, flags=re.IGNORECASE | re.DOTALL)
        
        # Code inline
        md = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', md, flags=re.IGNORECASE | re.DOTALL)
        
        # Lists - handle nested structures
        md = re.sub(r'<ul[^>]*>\s*<li[^>]*>(.*?)</li>\s*</ul>', r'- \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r'<ol[^>]*>\s*<li[^>]*>(.*?)</li>\s*</ol>', r'1. \1\n', md, flags=re.IGNORECASE | re.DOTALL)
        
        # Horizontal rule
        md = re.sub(r'<hr\s*/?>', r'---\n', md, flags=re.IGNORECASE)
        
        # Line breaks
        md = re.sub(r'<br\s*/?>', '\n', md, flags=re.IGNORECASE)
        
        # Paragraphs
        md = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', md, flags=re.IGNORECASE | re.DOTALL)
        
        # Tables - basic support
        md = self._convert_tables(md)
        
        # Remove remaining Confluence-specific tags
        md = re.sub(r'<ac:[^>]*>.*?</ac:[^>]*>', '', md, flags=re.DOTALL | re.IGNORECASE)
        md = re.sub(r'<ri:[^>]*>', '', md, flags=re.IGNORECASE)
        
        # Strip remaining HTML tags
        md = re.sub(r'<[^>]+>', '', md)
        
        # Unescape HTML entities
        md = html.unescape(md)
        
        # Clean up whitespace
        md = re.sub(r'\n{3,}', '\n\n', md)
        
        return md.strip()
    
    def _convert_tables(self, md: str) -> str:
        """Convert HTML tables to Markdown."""
        import re
        
        # Find table blocks
        table_pattern = r'<table[^>]*>(.*?)</table>'
        
        def replace_table(match):
            table_html = match.group(1)
            
            # Extract rows
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
            if not rows:
                return ''
            
            md_rows = []
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                if cells:
                    # Strip tags from cells
                    clean_cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                    md_rows.append('| ' + ' | '.join(clean_cells) + ' |')
            
            if len(md_rows) >= 2:
                # Add header separator
                sep = '| ' + ' | '.join(['---'] * len(re.findall(r'<td[^>]*>', rows[0]))) + ' |'
                md_rows.insert(1, sep)
            
            return '\n'.join(md_rows)
        
        return re.sub(table_pattern, replace_table, md, flags=re.DOTALL | re.IGNORECASE)
    
    def _strip_tags(self, text: str) -> str:
        """Strip HTML tags from text."""
        import re
        text = re.sub(r'<[^>]+>', '', text)
        import html
        return html.unescape(text).strip()
    
    def _unescape_html(self, text: str) -> str:
        """Unescape HTML entities."""
        import html
        return html.unescape(text)
    
    def _basic_markdown_to_storage(self, md: str) -> str:
        """Basic Markdown to Storage conversion (fallback)."""
        import html
        import re
        
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
                    result.append(f'<ac:code-block lang="{html.escape(code_lang)}">')
                else:
                    in_code_block = False
                    result.append('</ac:code-block>')
                continue
            
            if in_code_block:
                # Escape content inside code blocks
                result.append(html.escape(line))
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
            # Bold
            elif '**' in line:
                line = html.escape(line)
                line = re.sub(r'&ast;&ast;(.+?)&ast;&ast;', r'<strong>\1</strong>', line)
                result.append(f'<p>{line}</p>')
            # Italic
            elif '*' in line:
                line = html.escape(line)
                line = re.sub(r'&ast;([^*]+)&ast;', r'<em>\1</em>', line)
                result.append(f'<p>{line}</p>')
            # Lists
            elif line.startswith('- ') or line.startswith('* '):
                result.append(f'<ul><li>{html.escape(line[2:])}</li></ul>')
            elif re.match(r'^\d+\.\s', line):
                match = re.match(r'^(\d+)\.\s(.+)', line)
                if match:
                    result.append(f'<ol><li>{html.escape(match.group(2))}</li></ol>')
            # Images (must check before links, since both have ]( )
            elif '![' in line and '](' in line:
                # Escape both alt text and URL
                def image_repl(m):
                    alt_text = html.escape(m.group(1))
                    url = html.escape(m.group(2), quote=True)
                    return f'<ac:image><ri:url ri:value="{url}"/></ac:image>'
                line = re.sub(r'!\[(.*?)\]\((.+?)\)', image_repl, line)
                result.append(line)
            # Links
            elif '](' in line:
                # Escape both link text and URL
                def link_repl(m):
                    link_text = html.escape(m.group(1))
                    url = html.escape(m.group(2), quote=True)
                    return f'<a href="{url}">{link_text}</a>'
                line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_repl, line)
                result.append(f'<p>{line}</p>')
            # Horizontal rule
            elif line.strip() == '---':
                result.append('<hr/>')
            # Code inline
            elif '`' in line:
                line = re.sub(r'`([^`]+)`', lambda m: f'<code>{html.escape(m.group(1))}</code>', line)
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
        
        # Lists
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
