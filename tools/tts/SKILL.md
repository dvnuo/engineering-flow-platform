# TTS Tool - Text to Speech

Convert text to speech using configured TTS engine.

## Usage

```bash
tts text="Hello, world!"
tts text="你好，世界！" channel="telegram"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| text | string | Yes | Text to convert to speech |
| channel | string | No | Channel ID for output format |

## Examples

Basic TTS:
```
tts text="Hello, world!"
```

Multi-language:
```
tts text="你好，今天天气怎么样？"
```

Channel-specific:
```
tts text="Reminder" channel="telegram"
```
