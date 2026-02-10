# WebChat 功能实现待办

## 已完成 ✅
- [x] 基础布局 (topbar + sidebar + chat)
- [x] 主题切换 (Light/Dark)
- [x] 发送消息 (/api/chat)
- [x] 技能选择器 (/api/skills)
- [x] 使用统计 (/api/usage)
- [x] Markdown 渲染

## 待实现 🔧

### 高优先级
- [ ] **Recent Chats** - 侧边栏显示最近会话
- [ ] **Settings 页面** - 主题设置、API 配置等
- [ ] **File Explorer** - 文件浏览界面

### 中优先级
- [ ] **Markdown 增强** - 代码高亮 (highlight.js)
- [ ] **代码复制按钮** - 一键复制代码块
- [ ] **图片上传** - 支持发送图片

### 低优先级
- [ ] **SSE 流式响应** - 需要 LLM streaming 支持 (#163)
- [ ] **工具执行卡片** - 展示工具调用结果
- [ ] **审批卡片** - exec 审批 UI

## 文件位置

- HTML: `src/gateway/templates/webchat.html`
- CSS: `src/gateway/static/css/webchat.css`
- JS: `src/gateway/static/js/webchat.js`
- 后端: `src/gateway/webchat.py`

## 参考

- OpenClaw UI: `/root/.openclaw/workspace/openclaw-main/ui/src/ui/`
