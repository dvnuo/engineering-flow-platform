## Summary

Add WebChat UI for direct browser-based chat with the CodeW assistant, eliminating the need for Discord or other third-party apps.

## Features

### WebChat Interface
- Modern dark theme with animations
- Real-time typing indicator (three animated dots)
- Message history with auto-scroll
- Token usage tracking in status bar
- Session management (clear history)

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | GET | WebChat UI |
| `/api/chat` | POST | Send message, get response |
| `/api/sessions` | GET | List active sessions |
| `/api/usage` | GET | Get usage statistics |
| `/api/clear` | POST | Clear session history |
| `/api/queue/status` | GET | Queue status |

### Usage

```bash
# Start the server
python main.py

# Open in browser
http://localhost:8000/chat
```

## Changes

### New Files
- `gateway/webchat.py` - Complete WebChat implementation
  - HTML/CSS/JS frontend (no external dependencies)
  - API handlers for chat, sessions, usage, clear
  - Session management integration
  - Usage tracking integration

- `tests/test_webchat.py` - WebChat tests
  - Template validation
  - Route registration tests

### Modified Files
- `gateway/server.py` - Added WebChat routes and `/api/queue/status`

## Technical Details

### Frontend
- Pure HTML/CSS/JS (no external dependencies)
- Native fetch API for message sending
- Auto-resizing textarea
- Message animations
- Responsive design

### Backend
- Integrated into existing Gateway HTTP server
- Reuses `session_manager` for session handling
- Reuses `agent.core.Agent` for message processing
- Integrated with `usage_tracker` for token statistics

## Known Limitations

1. **Usage Tracking**: LLMClient returns usage from API response
2. **Session ID**: Dynamic session_id with timestamp-based default for multi-session support
3. **Compactor**: SessionCompactor._generate_summary() is a placeholder (requires LLM integration)

## Future Improvements

- [ ] LLM-powered session compaction
- [ ] WebSocket for real-time updates
- [ ] Markdown rendering for code blocks
- [ ] File attachments support
