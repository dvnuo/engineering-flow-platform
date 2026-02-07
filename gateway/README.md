# Gateway Directory

## Directory Structure

```
gateway/
├── __init__.py
├── main.py                 # Gateway main entry point
├── app.py                  # Flask/FastAPI application
├── config.py              # Gateway configuration
├── routes/                 # API routes
│   ├── __init__.py
│   ├── api.py             # REST API endpoints
│   ├── web.py             # Web UI routes
│   └── ws.py              # WebSocket endpoints
├── middleware/            # Request middleware
│   ├── __init__.py
│   ├── auth.py            # Authentication
│   ├── logging.py         # Request logging
│   ├── rate_limit.py      # Rate limiting
│   └── cors.py            # CORS handling
├── services/              # Internal services
│   ├── __init__.py
│   ├── auth.py            # Authentication service
│   ├── config_service.py  # Configuration service
│   └── health_service.py   # Health check service
├── static/               # Static assets
│   ├── css/
│   ├── js/
│   └── images/
├── templates/            # Jinja2 templates
│   ├── base.html
│   ├── dashboard.html
│   ├── settings.html
│   └── (other templates)
└── utils/                 # Utilities
    ├── __init__.py
    ├── serializers.py    # Response formatting
    └── validators.py     # Request validation
```

## How It Works

### 1. Gateway Architecture
```
                    ┌─────────────────┐
                    │   Load Balancer │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Gateway       │
                    │  (This Module)  │
                    └────────┬────────┘
           ┌──────────────┼──────────────┐
           │              │              │
    ┌──────▼──────┐ ┌────▼────┐ ┌──────▼──────┐
    │   REST API  │ │ Web UI  │ │ WebSocket   │
    └─────────────┘ └─────────┘ └─────────────┘
```

### 2. Request Flow
```
HTTP Request → Middleware → Router → Handler → Service → Response
              ↓                              ↓
         Authentication              Business Logic
         Rate Limiting               Data Processing
         Logging                     External APIs
         Validation
```

### 3. API Endpoints

#### REST API
```python
# routes/api.py

@app.route('/api/v1/health')
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": VERSION
    })

@app.route('/api/v1/messages', methods=['POST'])
def send_message():
    """Send message to channel."""
    data = request.get_json()
    validate_message(data)
    result = message_service.send(data)
    return jsonify(result)

@app.route('/api/v1/config', methods=['GET', 'POST'])
def config管理():
    """Get or update configuration."""
    if request.method == 'GET':
        return jsonify(config_service.get())
    else:
        return jsonify(config_service.update(request.get_json()))
```

## What Problems It Solves

- **API Gateway**: Unified entry point for all services
- **Web Dashboard**: Visual interface for management
- **Authentication**: Secure access control
- **Rate Limiting**: Protect against abuse
- **Configuration Management**: Dynamic configuration updates
- **Real-time Updates**: WebSocket support for live data

## Configuration Options

### Core Gateway Configuration (config.yaml)

```yaml
# config.yaml
gateway:
  # Server settings
  host: "0.0.0.0"
  port: 8080
  debug: false
  workers: 4
  reload: false
  
  # SSL/TLS
  ssl:
    enabled: false
    cert: "/path/to/cert.pem"
    key: "/path/to/key.pem"
  
  # CORS
  cors:
    enabled: true
    origins:
      - "http://localhost:3000"
      - "https://example.com"
    methods:
      - "GET"
      - "POST"
      - "PUT"
      - "DELETE"
    headers:
      - "Content-Type"
      - "Authorization"
    credentials: true
    max_age: 86400
  
  # Rate limiting
  rate_limit:
    enabled: true
    strategy: "fixed_window"  # fixed_window, sliding_window
    limits:
      - endpoint: "/api/*"
        requests: 100
        period: 60            # seconds
      - endpoint: "/api/v1/messages"
        requests: 10
        period: 60
      - endpoint: "/api/v1/health"
        requests: 1000
        period: 60
  
  # Authentication
  auth:
    enabled: true
    type: "token"           # token, oauth2, api_key
    tokens:
      - name: "admin"
        token: "${ADMIN_TOKEN}"
        roles: ["admin", "user"]
      - name: "readonly"
        token: "${READONLY_TOKEN}"
        roles: ["read"]
    jwt:
      secret: "${JWT_SECRET}"
      algorithm: "HS256"
      expiration: 3600       # seconds
  
  # Web UI
  ui:
    enabled: true
    theme: "dark"
    title: "Engineering Flow Platform Dashboard"
    favicon: "/static/favicon.ico"
  
  # Logging
  logging:
    level: "INFO"
    format: "json"
    file: "logs/gateway.log"
    max_size: "100MB"
    backup_count: 5
    access_log: true
  
  # WebSocket
  websocket:
    enabled: true
    ping_interval: 30
    ping_timeout: 10
    max_connections: 100
```

### Per-Endpoint Configuration

```yaml
# Health endpoint
gateway:
  endpoints:
    /api/v1/health:
      methods: [GET]
      auth_required: false
      rate_limit: {requests: 1000, period: 60}
    
    /api/v1/messages:
      methods: [POST]
      auth_required: true
      rate_limit: {requests: 10, period: 60}
      validation:
        required: ["content", "channel"]
        types:
          content: str
          channel: str
    
    /api/v1/config:
      methods: [GET, POST]
      auth_required: true
      roles_required: ["admin"]
```

### Environment Variables

```bash
# Server
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8080
GATEWAY_DEBUG=false

# SSL
GATEWAY_SSL_ENABLED=false
GATEWAY_SSL_CERT=/path/to/cert.pem
GATEWAY_SSL_KEY=/path/to/key.pem

# Auth
AUTH_ENABLED=true
ADMIN_TOKEN=admin_secret_token
JWT_SECRET=your_jwt_secret_key

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/gateway.log
```

## How to Run

### Start Gateway
```bash
# Basic startup
python -m gateway

# Development mode (with auto-reload)
python -m gateway --debug

# Production mode
python -m gateway --workers 4

# Custom config
python -m gateway --config /path/to/config.yaml
```

### Access Web UI
```
# Dashboard
http://localhost:8080/

# API Health
http://localhost:8080/api/v1/health

# API Documentation
http://localhost:8080/docs
```

### Test Gateway
```bash
# Health check
curl http://localhost:8080/api/v1/health

# Send message
curl -X POST http://localhost:8080/api/v1/messages \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello", "channel": "discord"}'

# WebSocket connection
wss://localhost:8080/ws?token=${TOKEN}
```

## Development Principles

### 1. Route Implementation
```python
# routes/api.py

from flask import Blueprint, request, jsonify
from functools import wraps

api = Blueprint('api', __name__)

def require_auth(f):
    """Decorator for authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"error": "Missing token"}), 401
        user = validate_token(token)
        if not user:
            return jsonify({"error": "Invalid token"}), 403
        return f(user, *args, **kwargs)
    return decorated

@api.route('/api/v1/endpoint', methods=['GET'])
@require_auth
def endpoint(user):
    """Endpoint description."""
    return jsonify({"user": user.id, "data": "result"})
```

### 2. Middleware Pattern
```python
# middleware/rate_limit.py

from flask import request, g

class RateLimitMiddleware:
    """Rate limiting middleware."""
    
    def __init__(self, app, config: Dict[str, Any]):
        self.config = config
        app.before_request(self.check_limit)
    
    def check_limit(self):
        """Check rate limit for request."""
        key = request.remote_addr
        count = get_count(key)
        if count > self.config['limit']:
            return {"error": "Rate limit exceeded"}, 429
        increment(key)
```

### 3. WebSocket Handling
```python
# routes/ws.py

from flask_socketio import SocketIO, emit

socketio = SocketIO()

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    print('Client connected')
    emit('status', {'status': 'connected'})

@socketio.on('message')
def handle_message(data):
    """Handle incoming message."""
    result = process_message(data)
    emit('response', result)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle disconnection."""
    print('Client disconnected')
```

## API Reference

### Main Gateway (gateway/main.py)

```python
class Gateway:
    """Main gateway application."""
    
    def __init__(self, config_path: str = None):
        self.config = self.load_config(config_path)
        self.app = self.create_app()
    
    def load_config(self, path: str) -> GatewayConfig:
        """Load gateway configuration."""
        ...
    
    def create_app(self) -> Flask:
        """Create Flask application."""
        ...
    
    def run(self):
        """Start the gateway server."""
        ...
    
    def get_status(self) -> Dict[str, Any]:
        """Get gateway status."""
        ...
```

### API Routes (gateway/routes/api.py)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/health` | GET | No | Health check |
| `/api/v1/status` | GET | Yes | System status |
| `/api/v1/messages` | POST | Yes | Send message |
| `/api/v1/messages/{id}` | GET | Yes | Get message |
| `/api/v1/config` | GET | Yes | Get config |
| `/api/v1/config` | POST | Admin | Update config |
| `/api/v1/channels` | GET | Yes | List channels |
| `/api/v1/channels/{id}` | GET | Yes | Channel details |
| `/api/v1/sessions` | GET | Yes | List sessions |

## Troubleshooting

### Connection Issues
```bash
# Check if gateway is running
curl http://localhost:8080/api/v1/health

# Check logs
tail -f logs/gateway.log

# Check process
ps aux | grep gateway
```

### Authentication Errors
```bash
# Test token generation
python -c "from gateway.services.auth import generate_token; print(generate_token('admin'))"

# Validate token
curl -H "Authorization: Bearer ${TOKEN}" http://localhost:8080/api/v1/status
```

### Performance Issues
```bash
# Check response times
curl -w "\nTime: %{time_total}s\n" http://localhost:8080/api/v1/health

# Monitor connections
netstat -an | grep 8080
```
