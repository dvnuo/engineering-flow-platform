"""
Jira Markup Converter - Convert between Markdown and Jira wiki/ADF formats.

Main path (Server/DC): wiki/renderer <-> Markdown
Compatibility path (Cloud): ADF <-> Markdown
"""

import logging
import re
import html
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class JiraMarkupConverter:
    """Convert between Markdown and Jira markup formats."""
    
    # ========== Wiki -> Markdown ==========
    
    def wiki_to_markdown(self, wiki_text: str) -> str:
        """Convert Jira wiki markup to Markdown.
        
        Args:
            wiki_text: Jira wiki/renderer markup string
            
        Returns:
            Markdown formatted string
        """
        if not wiki_text:
            return ""
        
        md = wiki_text
        
        # Headers (must be at line start)
        md = re.sub(r'^h1\. (.+)$', r'# \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h2\. (.+)$', r'## \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h3\. (.+)$', r'### \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h4\. (.+)$', r'#### \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h5\. (.+)$', r'##### \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h6\. (.+)$', r'###### \1', md, flags=re.MULTILINE)
        
        # Bold/Italic
        md = re.sub(r'\*(.+?)\*', r'**\1**', md)  # *bold*
        md = re.sub(r'_(.+?)_', r'*\1*', md)  # _italic_
        
        # Inline code
        md = re.sub(r'\{\{(.+?)\}\}', r'`\1`', md)  # {{code}}
        
        # Code blocks
        md = re.sub(r'\{code:(\w+)\}(.*?)\{code\}', 
                     r'```\1\n\2\n```', md, flags=re.DOTALL)
        md = re.sub(r'\{code\}(.*?)\{code\}', 
                     r'```\n\1\n```', md, flags=re.DOTALL)
        
        # Links [text|url]
        md = re.sub(r'\[(.+?)\|(.+?)\]', r'[\1](\2)', md)
        
        # Images !url!
        md = re.sub(r'!(.+?)!', r'![](\1)', md)
        
        # Lists
        md = re.sub(r'^\* (.+)$', r'- \1', md, flags=re.MULTILINE)
        md = re.sub(r'^# (.+)$', r'1. \1', md, flags=re.MULTILINE)
        
        # Quote {quote}...{quote}
        md = re.sub(r'\{quote\}(.*?)\{quote\}', r'> \1', md, flags=re.DOTALL)
        
        # Horizontal rule
        md = re.sub(r'^----+$', r'---', md, flags=re.MULTILINE)
        
        # Line breaks
        md = re.sub(r'\\$', r'  ', md, flags=re.MULTILINE)  # Trailing \ for hard break
        
        return md.strip()
    
    # ========== Markdown -> Wiki ==========
    
    def markdown_to_wiki(self, md_text: str) -> str:
        """Convert Markdown to Jira wiki markup.
        
        Args:
            md_text: Markdown formatted string
            
        Returns:
            Jira wiki markup string
        """
        if not md_text:
            return ""
        
        lines = md_text.split('\n')
        result = []
        in_code_block = False
        code_lang = ""
        
        for line in lines:
            # Code blocks
            if line.startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    if code_lang:
                        result.append(f'{{code:{code_lang}}}')
                    else:
                        result.append('{code}')
                else:
                    in_code_block = False
                    result.append('{code}')
                continue
            
            if in_code_block:
                result.append(line)
                continue
            
            # Headers
            if line.startswith('###### '):
                result.append(f'h6. {line[7:]}')
            elif line.startswith('##### '):
                result.append(f'h5. {line[6:]}')
            elif line.startswith('#### '):
                result.append(f'h4. {line[5:]}')
            elif line.startswith('### '):
                result.append(f'h3. {line[4:]}')
            elif line.startswith('## '):
                result.append(f'h2. {line[3:]}')
            elif line.startswith('# '):
                result.append(f'h1. {line[2:]}')
            # Bold (only, not italic)
            elif '**' in line:
                line = re.sub(r'\*\*(.+?)\*\*', r'*\1*', line)
                result.append(line)
            # Italic (only, not bold)
            elif '*' in line:
                line = re.sub(r'\*([^*]+)\*', r'_\1_', line)
                result.append(line)
            # Inline code
            elif '`' in line:
                line = re.sub(r'`([^`]+)`', r'{{\1}}', line)
                result.append(line)
            # Images ![alt](url) - must check before links
            if '![' in line and '](' in line:
                line = re.sub(r'!\[(.*?)\]\((.+?)\)', r'!\2!', line)
            # Links [text](url)
            if '](' in line:
                line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'[\1|\2]', line)
            result.append(line)
            # Lists
            elif line.startswith('- ') or line.startswith('* '):
                result.append(f'* {line[2:]}')
            elif re.match(r'^\d+\.\s', line):
                match = re.match(r'^(\d+)\.\s(.+)', line)
                if match:
                    result.append(f'# {match.group(2)}')
            # Quote
            elif line.startswith('> '):
                result.append(f'{{quote}}{line[2:]}{{quote}}')
            # Horizontal rule
            elif line.strip() == '---':
                result.append('----')
            elif line.strip():
                result.append(line)
        
        return '\n'.join(result)
    
    # ========== ADF -> Markdown (Cloud) ==========
    
    def adf_to_markdown(self, adf_node: Any) -> str:
        """Convert Atlassian Document Format (ADF) to Markdown.
        
        Args:
            adf_node: ADF JSON node (dict or list)
            
        Returns:
            Markdown formatted string
        """
        if not adf_node:
            return ""
        
        # Handle both dict and list formats
        if isinstance(adf_node, list):
            return '\n'.join(self._adf_node_to_markdown(node) for node in adf_node)
        
        return self._adf_node_to_markdown(adf_node)
    
    def _adf_node_to_markdown(self, node: Any) -> str:
        """Convert a single ADF node to Markdown."""
        if not isinstance(node, dict):
            return str(node)
        
        node_type = node.get('type', '')
        content = node.get('content', [])
        
        if node_type == 'doc':
            return '\n\n'.join(self._adf_node_to_markdown(c) for c in content)
        
        elif node_type == 'paragraph':
            text = ''.join(self._adf_node_to_markdown(c) for c in content)
            return text
        
        elif node_type == 'heading':
            level = node.get('attrs', {}).get('level', 1)
            text = ''.join(self._adf_node_to_markdown(c) for c in content)
            return f"{'#' * level} {text}"
        
        elif node_type == 'bulletList':
            return '\n'.join(f"- {self._adf_node_to_markdown(item)}" 
                            for item in content if item.get('type') == 'listItem')
        
        elif node_type == 'orderedList':
            return '\n'.join(f"{i+1}. {self._adf_node_to_markdown(item)}" 
                            for i, item in enumerate(content) if item.get('type') == 'listItem')
        
        elif node_type == 'listItem':
            return ''.join(self._adf_node_to_markdown(c) for c in content)
        
        elif node_type == 'codeBlock':
            lang = node.get('attrs', {}).get('language', '')
            text = ''.join(self._adf_node_to_markdown(c) for c in content)
            return f"```{lang}\n{text}\n```"
        
        elif node_type == 'blockquote':
            text = '\n'.join(self._adf_node_to_markdown(c) for c in content)
            return '\n'.join(f"> {line}" for line in text.split('\n'))
        
        elif node_type == 'rule':
            return '---'
        
        # Mark-based elements (inline)
        elif node_type in ('text', 'str'):
            marks = node.get('marks', [])
            text = node.get('text', '')
            
            for mark in marks:
                mark_type = mark.get('type', '')
                if mark_type == 'strong':
                    text = f"**{text}**"
                elif mark_type == 'emphasis':
                    text = f"*{text}*"
                elif mark_type == 'code':
                    text = f"`{text}`"
                elif mark_type == 'link':
                    url = mark.get('attrs', {}).get('href', '')
                    text = f"[{text}]({url})"
            
            return text
        
        return ''
    
    # ========== Markdown -> ADF (Cloud) ==========
    
    def markdown_to_adf(self, md_text: str) -> Dict[str, Any]:
        """Convert Markdown to Atlassian Document Format (ADF).
        
        Args:
            md_text: Markdown formatted string
            
        Returns:
            ADF JSON structure
        """
        if not md_text:
            return {"type": "doc", "version": 1, "content": []}
        
        content = self._parse_md_to_adf_content(md_text)
        return {
            "type": "doc",
            "version": 1,
            "content": content
        }
    
    def _parse_md_to_adf_content(self, md_text: str) -> list:
        """Parse Markdown text into ADF content nodes."""
        lines = md_text.split('\n')
        content = []
        in_code_block = False
        code_lang = ""
        code_lines = []
        
        for line in lines:
            # Code blocks
            if line.startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    code_lines = []
                else:
                    # End code block
                    content.append({
                        "type": "codeBlock",
                        "attrs": {"language": code_lang} if code_lang else {},
                        "content": [{"type": "text", "text": '\n'.join(code_lines)}]
                    })
                    in_code_block = False
                    code_lang = ""
                    code_lines = []
                continue
            
            if in_code_block:
                code_lines.append(line)
                continue
            
            # Headers
            if line.startswith('###### '):
                content.append(self._adf_heading(line[7:], 6))
            elif line.startswith('##### '):
                content.append(self._adf_heading(line[6:], 5))
            elif line.startswith('#### '):
                content.append(self._adf_heading(line[5:], 4))
            elif line.startswith('### '):
                content.append(self._adf_heading(line[4:], 3))
            elif line.startswith('## '):
                content.append(self._adf_heading(line[3:], 2))
            elif line.startswith('# '):
                content.append(self._adf_heading(line[2:], 1))
            # Lists
            elif line.startswith('- ') or line.startswith('* '):
                content.append(self._adf_bullet_item(line[2:]))
            elif re.match(r'^\d+\.\s', line):
                match = re.match(r'^(\d+)\.\s(.+)', line)
                if match:
                    content.append(self._adf_ordered_item(match.group(2), int(match.group(1))))
            # Blockquote
            elif line.startswith('> '):
                content.append(self._adf_blockquote(line[2:]))
            # Horizontal rule
            elif line.strip() == '---':
                content.append({"type": "rule"})
            # Paragraph
            elif line.strip():
                content.append(self._adf_paragraph(line))
        
        return content
    
    def _adf_heading(self, text: str, level: int) -> dict:
        """Create ADF heading node."""
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": [self._adf_text_with_marks(text)]
        }
    
    def _adf_paragraph(self, text: str) -> dict:
        """Create ADF paragraph node."""
        return {
            "type": "paragraph",
            "content": [self._adf_text_with_marks(text)]
        }
    
    def _adf_text_with_marks(self, text: str) -> dict:
        """Create ADF text node with marks (bold, italic, etc)."""
        # Simple implementation - just text without marks
        # A full implementation would parse **, *, ` etc
        return {"type": "text", "text": text}
    
    def _adf_bullet_item(self, text: str) -> dict:
        """Create ADF bullet list item."""
        return {
            "type": "bulletList",
            "content": [{
                "type": "listItem",
                "content": [self._adf_paragraph(text)]
            }]
        }
    
    def _adf_ordered_item(self, text: str, num: int) -> dict:
        """Create ADF ordered list item."""
        return {
            "type": "orderedList",
            "attrs": {"order": num},
            "content": [{
                "type": "listItem",
                "content": [self._adf_paragraph(text)]
            }]
        }
    
    def _adf_blockquote(self, text: str) -> dict:
        """Create ADF blockquote."""
        return {
            "type": "blockquote",
            "content": [self._adf_paragraph(text)]
        }
    
    # ========== Utility ==========
    
    def is_adf(self, data: Any) -> bool:
        """Check if data is ADF format.
        
        Args:
            data: Data to check
            
        Returns:
            True if data appears to be ADF JSON
        """
        if not isinstance(data, dict):
            return False
        return data.get("type") == "doc"


# Global instance
converter = JiraMarkupConverter()


# Convenience functions
def wiki_to_markdown(wiki_text: str) -> str:
    """Convert Jira wiki to Markdown."""
    return converter.wiki_to_markdown(wiki_text)


def markdown_to_wiki(md_text: str) -> str:
    """Convert Markdown to Jira wiki."""
    return converter.markdown_to_wiki(md_text)


def adf_to_markdown(adf_node: Any) -> str:
    """Convert ADF to Markdown."""
    return converter.adf_to_markdown(adf_node)


def markdown_to_adf(md_text: str) -> Dict[str, Any]:
    """Convert Markdown to ADF."""
    return converter.markdown_to_adf(md_text)
