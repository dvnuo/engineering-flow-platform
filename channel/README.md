# Channel Directory

## 目录结构

```
channel/
├── __init__.py
└── (channel implementations)
```

## 工作原理

Channel 是消息通道的抽象接口，负责：
1. **接收消息**: 从各平台 (Discord, WhatsApp, etc.) 接收消息
2. **发送消息**: 将响应发送回对应平台
3. **格式转换**: 平台消息 ↔ 内部格式

## 解决的问题

- **多平台支持**: 统一的接口适配不同消息平台
- **消息路由**: 根据 channel 类型处理消息
- **格式适配**: 各平台消息格式不同

## 配置方式

```yaml
channels:
  - type: discord
    token: ${DISCORD_TOKEN}
  - type: whatsapp
    account: ${WA_ACCOUNT}
```

## 运行方式

Channel 由主程序自动加载：

```bash
python main.py
```

## 开发原则

### 1. 接口规范
```python
class Channel:
    def receive() -> Message:
        """接收消息"""
    
    def send(response: Response):
        """发送响应"""
```

### 2. 消息格式
- 统一使用内部 Message 格式
- 适配各平台特有的格式

### 3. 错误处理
- 网络错误重试
- 格式错误降级处理
