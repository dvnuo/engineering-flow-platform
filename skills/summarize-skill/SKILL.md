---
name: summarize
description: Summarize URLs, files, and text content using LLM or extractive methods
metadata:
  emoji: 📝
  requires:
    bins: [curl]
    anyBins: []
    env: []
    config: []
---
# Summarize Skill - Content Summarization

Summarize URLs, files, and text content using LLM or extractive methods.

## Skill Signature

\`\`\`python
summarize(
    url: str = None,
    text: str = None,
    file_path: str = None,
    max_length: int = 500,
    model: str = "gpt-3.5-turbo"
) -> SkillResult
\`\`\`

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | No* | URL to summarize |
| `text` | string | No* | Text to summarize |
| `file_path` | string | No* | Local file path to summarize |
| `max_length` | integer | No | Maximum summary length (default: 500 chars) |
| `model` | string | No | LLM model to use (default: gpt-3.5-turbo) |

*Exactly one of `url`, `text`, or `file_path` is required.

## Examples

### Summarize URL

\`\`\`python
# Summarize a web article
summarize(url="https://example.com/article")

# Summarize with custom length
summarize(url="https://example.com/long-article", max_length=1000)
\`\`\`

### Summarize Text

\`\`\`python
# Summarize provided text
summarize(text="Your long text content here...")

# Quick summary
summarize(text="Your text...", max_length=200)
\`\`\`

### Summarize File

\`\`\`python
# Summarize a local file
summarize(file_path="/path/to/document.md")

# Summarize with custom length
summarize(file_path="/path/to/long-file.txt", max_length=800)
\`\`\`

### Quick Summary

\`\`\`python
# Quick extractive summary (first paragraph)
quick_summary(text="Your text content...")

# With length limit
quick_summary(text="Your text...", max_length=200)
\`\`\`

## Use Cases

### 1. Web Content Summarization

\`\`\`python
# Summarize a news article
summarize(url="https://news.example.com/tech-article")

# Summarize documentation
summarize(url="https://docs.example.com/guide")
\`\`\`

### 2. Document Summarization

\`\`\`python
# Summarize meeting notes
summarize(file_path="/notes/meeting.txt")

# Summarize a report
summarize(file_path="/docs/annual-report.pdf")
\`\`\`

### 3. Quick Notes

\`\`\`python
# Quick extractive summary
quick_summary(text="Long paragraph...")

# Email summarization
quick_summary(text="Email content...")
\`\`\`

## Output Format

\`\`\`
Summary (N chars):

[Summary content...]

---
Metadata:
- Original length: N characters
- Summary length: N characters
- Model: gpt-3.5-turbo
- Source: url/file_path/text
\`\`\`

## Tips

1. **Use `max_length`** to control summary length
2. **Use `quick_summary`** for fast extractive summarization
3. **Long content** is automatically truncated (5KB limit)
4. **Multiple sources** - prefer URL or file for full content
