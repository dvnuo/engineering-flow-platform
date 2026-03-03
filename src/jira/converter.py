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
        """Convert Jira wiki markup to Markdown."""
        if not wiki_text:
            return ""
        
        md = wiki_text
        
        # Headers
        md = re.sub(r'^h1\. (.+)$', r'# \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h2\. (.+)$', r'## \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h3\. (.+)$', r'### \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h4\. (.+)$', r'#### \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h5\. (.+)$', r'##### \1', md, flags=re.MULTILINE)
        md = re.sub(r'^h6\. (.+)$', r'###### \1', md, flags=re.MULTILINE)
        
        # Bold/Italic (exclude list markers: * followed by space)
        md = re.sub(r'\*(?!\s)(.+?)(?<!\s)\*', r'**\1**', md)
        md = re.sub(r'_(.+?)_', r'*\1*', md)
        
        # Inline code
        md = re.sub(r'\{\{(.+?)\}\}', r'`\1`', md)
        
        # Code blocks
        md = re.sub(r'\{code:(\w+)\}(.*?)\{code\}', r'```\1\n\2\n```', md, flags=re.DOTALL)
        md = re.sub(r'\{code\}(.*?)\{code\}', r'```\n\1\n```', md, flags=re.DOTALL)
        
        # Links
        md = re.sub(r'\[(.+?)\|(.+?)\]', r'[\1](\2)', md)
        
        # Images
        md = re.sub(r'!(.+?)!', r'![](\1)', md)
        
        # Lists
        md = re.sub(r'^\* (.+)$', r'- \1', md, flags=re.MULTILINE)
        md = re.sub(r'^# (.+)$', r'1. \1', md, flags=re.MULTILINE)
        
        # Quote (handle multi-line properly)
        def _quote_replacer(match):
            content = match.group(1)
            lines = content.split('\n')
            return '\n'.join('> ' + line for line in lines)
        
        md = re.sub(r'\{quote\}(.*?)\{quote\}', _quote_replacer, md, flags=re.DOTALL)
        
        # Horizontal rule
        md = re.sub(r'^----+$', r'---', md, flags=re.MULTILINE)
        
        return md.strip()
    
    # ========== Markdown -> Wiki ==========
    
    def markdown_to_wiki(self, md_text: str) -> str:
        """Convert Markdown to Jira wiki markup."""
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
            
            # Headers (line-based, use if/continue)
            if line.startswith('###### '):
                result.append(f'h6. {line[7:]}')
                continue
            elif line.startswith('##### '):
                result.append(f'h5. {line[6:]}')
                continue
            elif line.startswith('#### '):
                result.append(f'h4. {line[5:]}')
                continue
            elif line.startswith('### '):
                result.append(f'h3. {line[4:]}')
                continue
            elif line.startswith('## '):
                result.append(f'h2. {line[3:]}')
                continue
            elif line.startswith('# '):
                result.append(f'h1. {line[2:]}')
                continue
            
            # Apply inline conversions cumulatively (use if, not elif)
            # Italic first (single asterisk, avoid matching bold **...**)
            if '*' in line:
                line = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'_\1_', line)
            # Bold (after italic to avoid reprocessing)
            if '**' in line:
                line = re.sub(r'\*\*(.+?)\*\*', r'*\1*', line)
            # Inline code
            if '`' in line:
                line = re.sub(r'`([^`]+)`', r'{{\1}}', line)
            # Images (before links)
            if '![' in line and '](' in line:
                line = re.sub(r'!\[(.*?)\]\((.+?)\)', r'!\2!', line)
            # Links
            if '](' in line:
                line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'[\1|\2]', line)
            
            # Line-based (use if/elif)
            if line.startswith('- ') or line.startswith('* '):
                result.append(f'* {line[2:]}')
            elif re.match(r'^\d+\.\s', line):
                match = re.match(r'^(\d+)\.\s(.+)', line)
                if match:
                    result.append(f'# {match.group(2)}')
            elif line.startswith('> '):
                result.append(f'{{quote}}{line[2:]}{{quote}}')
            elif line.strip() == '---':
                result.append('----')
            elif line.strip():
                result.append(line)
            else:
                result.append('')
        
        return '\n'.join(result)
    
    # ========== ADF -> Markdown ==========
    
    def adf_to_markdown(self, adf_node: Any) -> str:
        """Convert ADF to Markdown."""
        if not adf_node:
            return ""
        
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
            return ''.join(self._adf_node_to_markdown(c) for c in content)
        
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
    
    # ========== Markdown -> ADF ==========
    
    def markdown_to_adf(self, md_text: str) -> Dict[str, Any]:
        """Convert Markdown to ADF."""
        if not md_text:
            return {"type": "doc", "version": 1, "content": []}
        
        content = self._parse_md_to_adf_content(md_text)
        return {"type": "doc", "version": 1, "content": content}
    
    def _parse_md_to_adf_content(self, md_text: str) -> list:
        """Parse Markdown text into ADF content nodes."""
        lines = md_text.split('\n')
        content = []
        in_code_block = False
        code_lang = ""
        code_lines = []
        
        for line in lines:
            if line.startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    code_lines = []
                else:
                    content.append({
                        "type": "codeBlock",
                        "attrs": {"language": code_lang} if code_lang else {},
                        "content": [{"type": "text", "text": '\n'.join(code_lines)}]
                    })
                    in_code_block = False
                    code_lang = ""
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
        return {"type": "heading", "attrs": {"level": level}, "content": [self._adf_text(text)]}
    
    def _adf_paragraph(self, text: str) -> dict:
        return {"type": "paragraph", "content": [self._adf_text(text)]}
    
    def _adf_text(self, text: str) -> dict:
        return {"type": "text", "text": text}
    
    def _adf_bullet_item(self, text: str) -> dict:
        return {"type": "bulletList", "content": [{"type": "listItem", "content": [self._adf_paragraph(text)]}]}
    
    def _adf_ordered_item(self, text: str, num: int) -> dict:
        return {"type": "orderedList", "attrs": {"order": num}, "content": [{"type": "listItem", "content": [self._adf_paragraph(text)]}]}
    
    def _adf_blockquote(self, text: str) -> dict:
        return {"type": "blockquote", "content": [self._adf_paragraph(text)]}
    
    def is_adf(self, data: Any) -> bool:
        """Check if data is ADF format."""
        if not isinstance(data, dict):
            return False
        return data.get("type") == "doc"


converter = JiraMarkupConverter()

# Convenience functions
def wiki_to_markdown(wiki_text: str) -> str:
    return converter.wiki_to_markdown(wiki_text)

def markdown_to_wiki(md_text: str) -> str:
    return converter.markdown_to_wiki(md_text)

def adf_to_markdown(adf_node: Any) -> str:
    return converter.adf_to_markdown(adf_node)

def markdown_to_adf(md_text: str) -> Dict[str, Any]:
    return converter.markdown_to_adf(md_text)
