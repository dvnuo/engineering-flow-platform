# Gateway Directory

## 目录结构

```
gateway/
├── __init__.py
├── main.py             # Gateway 主程序
├── config.py           # Gateway 配置
├── static/            # 静态文件
│   └── (HTML/CSS/JS)
└── templates/         # Jinja2 模板
    └── (HTML templates)
```

## 工作原理

Gateway 是 OpsClaw 的 Web 网关：
1. **HTTP 服务**: 提供 REST API
2. **Web UI**: 仪表盘界面
3. **配置管理**: 动态配置更新
4. **状态监控**: 服务状态展示

## 解决的问题

- **API 网关**: 统一入口
- **Web 管理**: 可视化界面
- **配置热更新**: 无需重启
- **服务监控**: 状态可视化

## 配置方式

```yaml
gateway:
  host: "0.0.0.0"
  port: 8080
  debug: false
  
  ui:
    enabled: true
    theme: "dark"
```

## 运行方式

### 启动 Gateway
```bash
python -m gateway
```

### 访问 Web UI
```
http://localhost:8080
```

## 开发原则

### 1. API 设计
- RESTful 风格
- 统一的响应格式
- 错误码规范

### 2. 前端开发
- 响应式设计
- 暗色主题
- 实时更新

### 3. 性能优化
- 静态资源缓存
- GZIP 压缩
- 连接池管理
