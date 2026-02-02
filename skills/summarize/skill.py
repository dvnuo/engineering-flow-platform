"""
Summarize skill - Summarize URLs, files, and text content.

Uses wttr.in for weather and Open-Meteo as fallback.
"""

import json
import subprocess
from typing import Any, Dict, Optional

from skills.decorator import skill, SkillResult

# Skill metadata
SKILL_NAME = "summarize"
SKILL_DESCRIPTION = "Summarize URLs, files, and text content"


@skill(name=SKILL_NAME, description=SKILL_DESCRIPTION)
async def summarize(
    message: str = "",
    url: Optional[str] = None,
    text: Optional[str] = None,
    file_path: Optional[str] = None,
    max_length: int = 500,
    model: str = "gpt-3.5-turbo",
) -> SkillResult:
    """Summarize content from URL, text, or file.
    
    Args:
        url: URL to summarize
        text: Text to summarize
        file_path: Local file path to summarize
        max_length: Maximum summary length (default: 500 chars)
        model: LLM model to use (default: gpt-3.5-turbo)
    
    Returns:
        SkillResult with summary
    """
    # Extract parameters from message if not provided
    if not url and not text and not file_path:
        import re
        url_match = re.search(r'(https?://[^\s]+)', message)
        if url_match:
            url = url_match.group(1)
        file_match = re.search(r'(/[^\s]+)', message)
        if file_match and not url_match:
            file_path = file_match.group(1)
        if not url and not file_path:
            text = message
            text = re.sub(r'(summarize|summary of|summary)\s*:?\s*', '', text, flags=re.IGNORECASE)
            text = text.strip()
    
    try:
        content = ""
        
        # Get content from one of the sources
        if url:
            # Fetch URL content
            result = subprocess.run(
                ["curl", "-s", url],
                capture_output=True,
                text=True,
                timeout=30
            )
            content = result.stdout[:5000]  # Limit to 5KB
        elif file_path:
            # Read local file
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()[:5000]
            except FileNotFoundError:
                return SkillResult(success=False, output=f"File not found: {file_path}")
        elif text:
            content = text[:5000]
        else:
            return SkillResult(success=False, output="Please provide url, text, or file_path")
        
        if not content:
            return SkillResult(success=False, output="No content to summarize")
        
        # Simple extractive summarization (first few sentences)
        sentences = content.replace('?', '.').replace('!', '.').split('.')
        summary_parts = []
        current_length = 0
        
        for sentence in sentences:
            if current_length >= max_length:
                break
            sentence = sentence.strip()
            if len(sentence) > 20:  # Skip very short sentences
                summary_parts.append(sentence)
                current_length += len(sentence)
        
        summary = '. '.join(summary_parts[:5])  # Max 5 sentences
        
        if not summary:
            summary = content[:max_length]
        
        return SkillResult(
            success=True,
            output=f"Summary ({len(summary)} chars):\n\n{summary}",
            data={
                "original_length": len(content),
                "summary_length": len(summary),
                "model": model,
                "source": url or file_path or "text"
            }
        )
        
    except subprocess.TimeoutExpired:
        return SkillResult(success=False, output="Request timed out")
    except Exception as e:
        return SkillResult(success=False, output=f"Error: {str(e)}")


# Quick summary command for simple use
@skill(name="quick_summary", description="Quick summary of text")
async def quick_summary(text: str, max_length: int = 200) -> SkillResult:
    """Quick summary of text (first paragraph or sentences)."""
    if not text:
        return SkillResult(success=False, output="No text provided")
    
    # Split by common delimiters
    paragraphs = text.split('\n\n')
    first_para = paragraphs[0] if paragraphs else text
    
    sentences = first_para.split('.')
    summary = '. '.join(sentences[:3]).strip()
    
    if len(summary) > max_length:
        summary = summary[:max_length] + "..."
    
    return SkillResult(
        success=True,
        output=summary,
        data={"original_length": len(text), "summary_length": len(summary)}
    )
