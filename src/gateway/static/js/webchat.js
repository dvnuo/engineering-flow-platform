// WebChat JavaScript

(function() {
    'use strict';

    // Debug flag - initialized early to avoid reference errors
    let debugEnabled = document.getElementById('debugEnabled');
    
    // Helper to check if debug mode is on - handles null debugEnabled
    function isDebugEnabled() {
        try {
            return debugEnabled && debugEnabled.checked;
        } catch (e) {
            return false;
        }
    }
    
    // Helper to check if message is a tool placeholder that should be hidden
    function isToolPlaceholder(content, role) {
        // Only filter placeholder content for assistant/tool messages
        if (role !== 'assistant' && role !== 'tool') {
            return false;
        }
        if (!content) return false;
        return /\[Tool\s+.*\s+result\]/.test(content) || /Tool\s+.*\s+Result/.test(content);
    }

    // DOM Elements
    const messagesContainer = document.getElementById('messages');
    const messageInput = document.getElementById('messageInput');
    const sendButton = document.getElementById('sendButton');
    const fileInput = document.getElementById('fileInput');
    const uploadButton = document.getElementById('uploadButton');

    // Parse file
    // Helper functions
    function getFileIcon(contentType) {
        if (contentType.startsWith('image/')) return '<span class="file-type-badge img">IMG</span>';
        if (contentType === 'application/pdf') return '<span class="file-type-badge pdf">PDF</span>';
        if (contentType.includes('word')) return '📝';
        if (contentType.includes('spreadsheet') || contentType === 'text/csv') return '📊';
        return '<span class="file-type-badge txt">TXT</span>';
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    const typingIndicator = document.getElementById('typing');
    const statusSpan = document.getElementById('status');

    // Helper to update status
    function setStatus(message, type) {
        if (statusSpan) {
            statusSpan.textContent = message;
        }
    }

    const tokenCountSpan = document.getElementById('tokenCount');
    const costDisplaySpan = document.getElementById('costDisplay');
    const statsButton = document.getElementById('statsButton');
    const statsPanel = document.getElementById('statsPanel');
    const closeStatsButton = document.getElementById('closeStats');
    const statsContent = document.getElementById('statsContent');
    const skillSelector = document.getElementById('skillSelector');
    const skillDropdown = document.getElementById('skillDropdown');
    const skillList = document.getElementById('skillList');
    const pendingAttachmentsEl = document.getElementById('pendingAttachments');
    const themeToggle = document.getElementById('themeToggle');
    const newChatBtn = document.querySelector('[data-action="new-chat"]');

    // State
    const THEME_KEY = 'efp-theme';
    const SESSION_ID_KEY = 'efp-session-id';

    let isLoading = false;
    let totalTokens = 0;
    let totalCost = 0;
    let skills = [];
    let selectedSkillIndex = -1;
    let skillsLoaded = false;
    let currentSessionId = localStorage.getItem(SESSION_ID_KEY) || null;
    let pendingAttachments = [];
    const SKILLS_API_ENDPOINT = '/api/skills';
    console.log('[WebChat] Initial sessionId from localStorage:', currentSessionId);


    function createWebchatSessionId() {
        const now = new Date();
        const timestamp = now.getFullYear() +
            String(now.getMonth() + 1).padStart(2, '0') +
            String(now.getDate()).padStart(2, '0') +
            '_' +
            String(now.getHours()).padStart(2, '0') +
            String(now.getMinutes()).padStart(2, '0') +
            String(now.getSeconds()).padStart(2, '0');
        return 'webchat_' + timestamp;
    }

    function ensureCurrentSessionId() {
        if (!currentSessionId) {
            currentSessionId = createWebchatSessionId();
            localStorage.setItem(SESSION_ID_KEY, currentSessionId);
        }
        return currentSessionId;
    }

    function getTheme() {
        return localStorage.getItem(THEME_KEY) || 'light';
    }

    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
    }

    function toggleTheme() {
        const currentTheme = getTheme();
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        setTheme(newTheme);
    }

    function initTheme() {
        setTheme(getTheme());
    }

    initTheme();

    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }

    const sidebar = document.getElementById('sidebar');
    const layout = document.querySelector('.layout');
    const toggleSidebarBtn = document.getElementById('toggleSidebar');

    function toggleSidebar() {
        if (layout && sidebar && window.innerWidth <= 768) {
            layout.classList.toggle('sidebar-open');
            sidebar.classList.toggle('open');
        }
    }

    if (toggleSidebarBtn) {
        toggleSidebarBtn.addEventListener('click', toggleSidebar);
    }

    document.addEventListener('click', function(e) {
        if (
            window.innerWidth <= 768 &&
            layout && layout.classList.contains('sidebar-open') &&
            sidebar && !sidebar.contains(e.target) &&
            toggleSidebarBtn && !toggleSidebarBtn.contains(e.target)
        ) {
            layout.classList.remove('sidebar-open');
            sidebar.classList.remove('open');
        }
    });

    if (statsButton) {
        statsButton.addEventListener('click', showStats);
    }
    if (closeStatsButton) {
        closeStatsButton.addEventListener('click', hideStats);
    }

    async function showStats() {
        if (!statsPanel || !statsContent) return;

        statsPanel.classList.add('show');
        statsContent.innerHTML = '<div class="loading">Loading...</div>';

        try {
            const response = await fetch('/api/usage?days=30');
            const data = await response.json();

            if (data.error) {
                statsContent.innerHTML = '<div class="no-data">Error loading stats</div>';
                return;
            }

            let html = '';
            const global = data.global || {};
            html += `
      <div class="stats-section">
        <h3>Global (Last ${data.period_days || 30} days)</h3>
        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-label">Requests</div>
            <div class="stat-value">${global.request_count || 0}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Total Cost</div>
            <div class="stat-value cost">$${(global.total_cost_usd || global.total_cost || 0).toFixed(4)}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Input Tokens</div>
            <div class="stat-value">${global.total_input_tokens || global.total_input || 0}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Output Tokens</div>
            <div class="stat-value">${global.total_output_tokens || global.total_output || 0}</div>
          </div>
        </div>
      </div>
    `;

            const byProvider = data.by_provider || {};
            if (Object.keys(byProvider).length > 0) {
                html += '<div class="stats-section"><h3>By Provider</h3><div class="stats-grid">';
                for (const [provider, stats] of Object.entries(byProvider)) {
                    html += `
          <div class="stat-item">
            <div class="stat-label">${escapeHtml(provider)}</div>
            <div class="stat-value cost">$${(stats.cost || 0).toFixed(4)}</div>
            <div class="stat-model">${stats.requests || 0} requests</div>
          </div>
        `;
                }
                html += '</div></div>';
            }

            const byModel = data.by_model || {};
            if (Object.keys(byModel).length > 0) {
                html += '<div class="stats-section"><h3>By Model</h3>';
                for (const [model, stats] of Object.entries(byModel)) {
                    html += `
          <div class="stat-item">
            <div class="stat-label">${escapeHtml(model)}</div>
            <div class="stat-value cost">$${(stats.cost || 0).toFixed(4)}</div>
            <div class="stat-model">${(stats.input_tokens || 0).toLocaleString()} in / ${(stats.output_tokens || 0).toLocaleString()} out</div>
          </div>
        `;
                }
                html += '</div>';
            }

            if (!html) {
                html = '<div class="no-data">No usage data yet</div>';
            }

            statsContent.innerHTML = html;
        } catch (error) {
            statsContent.innerHTML = '<div class="no-data">Error loading stats</div>';
        }
    }

    function hideStats() {
        if (statsPanel) {
            statsPanel.classList.remove('show');
        }
    }
    // ========== Helper Functions ==========

    /**
     * Render skill list in dropdown
     */
    function renderSkillList() {
        if (!skills.length) {
            skillList.innerHTML = '<div class="skill-item"><span class="skill-desc">No skills available</span></div>';
            return;
        }

        const query = messageInput.value.slice(1).toLowerCase();
        let filteredSkills = skills;

        if (query) {
            filteredSkills = skills.filter(s =>
                s.name.toLowerCase().includes(query) ||
                s.description.toLowerCase().includes(query)
            );
        }

        skillList.innerHTML = filteredSkills.map((skill, index) => `
            <div class="skill-item"
                 role="option"
                 aria-selected="${index === 0 ? 'true' : 'false'}"
                 data-command="/${skill.name}"
                 data-index="${index}"
                 tabindex="0">
                <span class="skill-emoji" aria-hidden="true">${skill.emoji || '🔧'}</span>
                <span class="skill-name">/${skill.name}</span>
                <span class="skill-desc">${skill.description || ''}</span>
                ${skill.examples && skill.examples.length ?
                    `<span class="skill-examples">${skill.examples[0]}</span>` : ''}
            </div>
        `).join('');

        // Add click and touch handlers
        skillList.querySelectorAll('.skill-item').forEach(item => {
            const selectSkill = function(e) {
                e.preventDefault();
                const command = this.dataset.command;
                messageInput.value = command + ' ';
                messageInput.focus();
                hideSkillSelector();
            };
            item.addEventListener('click', selectSkill);
            item.addEventListener('touchstart', selectSkill, { passive: false });
        });

        // Select first item by default
        if (filteredSkills.length > 0) {
            selectedSkillIndex = 0;
            skillList.children[0]?.classList.add('selected');
        } else {
            selectedSkillIndex = -1;
        }
    }

    /**
     * Navigate skill list
     */
    function navigateSkillList(direction) {
        const items = skillList.querySelectorAll('.skill-item');
        if (!items.length) return;

        // Remove current selection and update ARIA
        if (selectedSkillIndex >= 0 && selectedSkillIndex < items.length) {
            items[selectedSkillIndex].classList.remove('selected');
            items[selectedSkillIndex].setAttribute('aria-selected', 'false');
        }

        // Calculate new index
        selectedSkillIndex += direction;
        if (selectedSkillIndex < 0) selectedSkillIndex = items.length - 1;
        if (selectedSkillIndex >= items.length) selectedSkillIndex = 0;

        // Add new selection and update ARIA
        const selectedItem = items[selectedSkillIndex];
        selectedItem.classList.add('selected');
        selectedItem.setAttribute('aria-selected', 'true');
        selectedItem.scrollIntoView({ block: 'nearest' });

        // Announce to screen readers
        const ariaLive = document.getElementById('skillAriaLive');
        if (ariaLive) {
            const skill = skills[selectedSkillIndex];
            ariaLive.textContent = `/${skill.name}: ${skill.description}`;
        }
    }

    // Auto-resize textarea and handle selector close on input change
    messageInput.addEventListener('input', function() {
        // Auto-resize
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        
        // Handle selector close on value change
        const value = this.value;
        
        // Close skill selector when / is deleted or not at position 0
        if (skillSelector.classList.contains('active') && !value.startsWith('/')) {
            hideSkillSelector();
        }
            });

    function renderPendingAttachments() {
        if (!pendingAttachmentsEl) return;

        if (!pendingAttachments.length) {
            pendingAttachmentsEl.innerHTML = '';
            pendingAttachmentsEl.classList.remove('active');
            return;
        }

        pendingAttachmentsEl.classList.add('active');
        pendingAttachmentsEl.innerHTML = pendingAttachments.map((a) => `
            <div class="pending-attachment-chip" data-file-id="${escapeHtml(a.file_id || a.local_id)}">
                <span>${escapeHtml(a.filename || 'attachment')}</span>
                <span class="pending-attachment-status">${escapeHtml(a.status || '')}</span>
                ${a.error ? `<span class="pending-attachment-error">${escapeHtml(a.error)}</span>` : ''}
                <button type="button"
                        class="pending-attachment-remove"
                        data-remove-file-id="${escapeHtml(a.file_id || a.local_id)}"
                        aria-label="Remove attachment">×</button>
            </div>
        `).join('');
    }

    function shouldParseAttachment(file, uploaded) {
        const name = (file?.name || uploaded?.filename || '').toLowerCase();
        const type = (uploaded?.content_type || file?.type || '').toLowerCase();

        if (type.startsWith('image/')) return false;

        return (
            type.includes('pdf') ||
            type.includes('word') ||
            type.includes('excel') ||
            type.includes('spreadsheet') ||
            type.includes('csv') ||
            type.includes('text') ||
            name.endsWith('.pdf') ||
            name.endsWith('.docx') ||
            name.endsWith('.xlsx') ||
            name.endsWith('.csv') ||
            name.endsWith('.txt')
        );
    }

    async function uploadFile(file) {
        const requestSessionId = ensureCurrentSessionId();
        const localId = 'local-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        const item = { local_id: localId, file_id: '', filename: file.name, content_type: file.type || '', size: file.size || 0, status: 'uploading', error: '' };
        pendingAttachments.push(item);
        renderPendingAttachments();
        setStatus('Uploading file...', 'uploading');

        try {
            const formData = new FormData();
            formData.append('file', file);
            const response = await fetch(`/api/files/upload?session_id=${encodeURIComponent(requestSessionId)}`, { method: 'POST', body: formData });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Upload failed');
            item.file_id = data.file_id;
            item.filename = data.filename || file.name;
            item.content_type = data.content_type || file.type || '';
            item.size = data.size || file.size || 0;
            const needsParse = shouldParseAttachment(file, data);
            item.status = needsParse ? 'parsing' : 'ready';
            renderPendingAttachments();
            if (needsParse) {
                const parseResp = await fetch(`/api/files/parse?session_id=${encodeURIComponent(requestSessionId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: data.file_id, options: {} }) });
                const parseData = await parseResp.json();
                if (!parseResp.ok || !parseData.success) throw new Error(parseData.error || 'Parse failed');
                item.status = 'ready';
                renderPendingAttachments();
            }
            setStatus('File ready: ' + item.filename, 'success');
        } catch (error) {
            item.status = 'error';
            item.error = error.message || String(error);
            setStatus('Upload failed: ' + item.error, 'error');
            renderPendingAttachments();
        }
    }

    uploadButton?.addEventListener('click', () => fileInput?.click());

    fileInput?.addEventListener('change', async (e) => {
        const files = Array.from(e.target.files || []);
        for (const file of files) await uploadFile(file);
        fileInput.value = '';
    });

    pendingAttachmentsEl?.addEventListener('click', async (event) => {
        const btn = event.target.closest('[data-remove-file-id]');
        if (!btn) return;
        const id = btn.dataset.removeFileId;
        const item = pendingAttachments.find((a) => a.file_id === id || a.local_id === id);
        pendingAttachments = pendingAttachments.filter((a) => a.file_id !== id && a.local_id !== id);
        renderPendingAttachments();
        if (item?.file_id) {
            try {
                const requestSessionId = ensureCurrentSessionId();
                await fetch(
                    `/api/files/${encodeURIComponent(item.file_id)}?session_id=${encodeURIComponent(requestSessionId)}`,
                    { method: 'DELETE' }
                );
            } catch (_error) {}
        }
    });

    // Drag and drop file upload - works on entire chat container
    const chatContainer = document.querySelector('.chat-container');
    const chatInputArea = messageInput.closest('.input-area') || messageInput.parentElement;

    function handleDragOver(e) {
        e.preventDefault();
        chatContainer.classList.add('drag-over');
    }

    function handleDragLeave(e) {
        e.preventDefault();
        // Only remove class if leaving the container entirely
        if (!chatContainer.contains(e.relatedTarget)) {
            chatContainer.classList.remove('drag-over');
        }
    }

    async function handleDrop(e) {
        e.preventDefault();
        chatContainer.classList.remove('drag-over');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            for (const file of files) {
                await uploadFile(file);
            }
        }
    }

    // Add listeners to chat container
    chatContainer.addEventListener('dragover', handleDragOver);
    chatContainer.addEventListener('dragleave', handleDragLeave);
    chatContainer.addEventListener('drop', handleDrop);

    // Send on Enter (Shift+Enter for new line)
    messageInput.addEventListener('keydown', function(e) {
        // Skill selector navigation
        if (skillSelector.classList.contains('active')) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                navigateSkillList(1);
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                navigateSkillList(-1);
                return;
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                const selected = skillList.querySelector('.skill-item.selected');
                if (selected) {
                    const command = selected.dataset.command;
                    messageInput.value = command + ' ';
                    messageInput.focus();
                    hideSkillSelector();
                }
                return;
            }
            if (e.key === 'Escape') {
                hideSkillSelector();
                return;
            }
        }

        // Show skill selector on / (insert / first if not already present)
        if (e.key === '/' && messageInput.selectionStart === 0) {
            e.preventDefault();
            // Only insert a leading / if it is not already there
            if (messageInput.value.charAt(0) !== '/') {
                messageInput.value = '/' + messageInput.value;
                messageInput.setSelectionRange(1, 1);
            }
            showSkillSelector();
            return;
        }

        // Send message (with IME protection)
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229 
            && !skillSelector.classList.contains('active')) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Paste handler for image upload
    messageInput.addEventListener('paste', async function(e) {
        const items = e.clipboardData?.items;
        if (!items) return;

        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (file) await uploadFile(file);
                return;
            }
        }
    });

    sendButton.addEventListener('click', sendMessage);



    /**
     * Add a message to the chat
     * @param {string} role - 'user', 'assistant', 'tool', or 'error'
     * @param {string} content - Message content
     * @param {string} [timestamp] - Optional timestamp
     * @param {Object} [toolCalls] - Optional tool calls array for assistant messages
     */
    function addMessage(role, content, timestamp, toolCalls = null, displayBlocks = null) {
        // Remove welcome message if present
        const welcome = messagesContainer.querySelector('.welcome-message');
        if (welcome) {
            welcome.remove();
        }

        const div = document.createElement('div');
        div.className = `message ${role}`;

        // Set avatar based on role
        let avatar;
        switch (role) {
            case 'user':
                avatar = 'U';
                break;
            case 'assistant':
                avatar = 'AI';
                break;
            case 'tool':
                avatar = '🔧';
                break;
            default:
                avatar = '?';
        }

        const time = timestamp ? formatSmartDate(timestamp) : formatSmartDate(new Date());

        // Handle different message types
        let badge = '';
        let messageContent = content || '';

        if (role === 'tool') {
            // Only show tool result badge in debug mode
            if (isDebugEnabled()) {
                badge = '<span class="tool-badge">🔧 Tool Result</span>';
            }
        } else if (role === 'assistant' && toolCalls && toolCalls.length > 0) {
            // Only show tool call badge in debug mode
            if (isDebugEnabled()) {
                badge = '<span class="tool-calls-badge">⚙️ Calling Tools</span>';
                const toolNames = toolCalls.map(tc => tc.function?.name || tc.name).join(', ');
                messageContent = `_Calling: ${toolNames}_`;
            }
        }

        const processedContent = messageContent;

        // Render markdown only for non-user, non-error roles; user and error messages are escaped/plain text
        const contentHtml = (role === 'assistant' || role === 'tool')
            ? renderDisplayBlocks(displayBlocks, processedContent)
            : escapeHtml(processedContent).replace(/\n/g, '<br>');

        div.innerHTML = `
            <div class="avatar" aria-hidden="true">${avatar}</div>
            <div>
                ${badge}
                <div class="message-bubble">${contentHtml}</div>
                <div class="message-timestamp" aria-label="Message time">${time}</div>
            </div>
        `;

        messagesContainer.appendChild(div);
        scrollToBottom();

        // Mark assistant messages as pending thinking process
        if (role === 'assistant') {
            markPendingAssistant(div);
        }

        enhanceRenderedMessage(div);

    }


    /**
     * Render markdown text to HTML
     * @param {string} text - Markdown text to render
     * @returns {string} HTML
     */
    // Configure marked.js once at module level
    (function() {
        try {
            marked.setOptions({
                breaks: true,        // Convert \n to <br>
                gfm: true,          // GitHub Flavored Markdown
            });
        } catch (e) {
            console.warn('Failed to configure marked:', e);
        }
    })();

    function renderMarkdown(text) {
        if (!text || typeof text !== 'string') {
            return '';
        }

        // Input length validation to prevent ReDoS and memory issues
        const MAX_INPUT_LENGTH = 100000; // 100KB limit
        if (text.length > MAX_INPUT_LENGTH) {
            text = text.substring(0, MAX_INPUT_LENGTH);
        }

        // Use marked.js for proper markdown rendering
        try {
            // Extract <pre> blocks first to preserve their newlines
            const preBlocks = [];
            let html = text.replace(/<pre[\s\S]*?<\/pre>/g, function(match) {
                // Normalize any <br> inside <pre> back to newlines before extracting
                const normalized = match.replace(/<br>/g, '\n');
                const placeholder = '__PRE_BLOCK_' + preBlocks.length + '__';
                preBlocks.push(normalized);
                return placeholder;
            });

            // Parse markdown (marked will handle escaping)
            html = marked.parse(html);

            // Convert newlines to <br> outside of <pre> blocks
            html = html.replace(/__PRE_BLOCK_(\d+)__/g, function(_, index) {
                return preBlocks[Number(index)];
            });

            // Restore <pre> blocks with their original newlines
            html = html.replace(/__PRE_BLOCK_(\d+)__/g, function(_, index) {
                return preBlocks[Number(index)];
            });

            return html;
        } catch (e) {
            // Fallback to simple rendering with XSS protection
            console.warn('Markdown rendering failed:', e);
            return escapeHtml(text).replace(/\n/g, '<br>');
        }
    }

    function parseDisplayBlocks(raw) {
        if (!Array.isArray(raw) || raw.length === 0) {
            return null;
        }
        const blocks = raw
            .filter((block) => block && typeof block === 'object' && typeof block.type === 'string')
            .map((block) => ({ ...block, type: block.type.trim() }))
            .filter((block) => block.type.length > 0)
            .filter((block) => hasRenderableDisplayBlock(block));
        return blocks.length > 0 ? blocks : null;
    }

    function renderDisplayBlocks(blocks, fallbackMarkdown = '') {
        const parsedBlocks = parseDisplayBlocks(blocks);
        if (!parsedBlocks) {
            return renderMarkdown(fallbackMarkdown || '');
        }
        return parsedBlocks.map(renderSingleDisplayBlock).join('');
    }

    function getBlockText(block, preferCode = false) {
        if (!block || typeof block !== 'object') {
            return '';
        }
        const textFields = preferCode
            ? ['code', 'content', 'text', 'message', 'output', 'result', 'value']
            : ['content', 'text', 'output', 'result', 'value', 'message'];
        for (const field of textFields) {
            const value = block[field];
            if (value === null || value === undefined) {
                continue;
            }
            const textValue = String(value);
            if (textValue.trim().length === 0) {
                continue;
            }
            return textValue;
        }
        return '';
    }

    function hasRenderableDisplayBlock(block) {
        if (!block || typeof block !== 'object') {
            return false;
        }
        const blockType = String(block.type || '').toLowerCase();
        if (!blockType) {
            return false;
        }
        if (blockType === 'table') {
            const headers = Array.isArray(block.headers) ? block.headers : (Array.isArray(block.columns) ? block.columns : []);
            const rows = Array.isArray(block.rows) ? block.rows : [];
            return headers.length > 0 || rows.length > 0 || getBlockText(block).length > 0;
        }
        return getBlockText(block, blockType === 'code').length > 0;
    }

    function hasMeaningfulDisplayBlocks(blocks) {
        const parsedBlocks = parseDisplayBlocks(blocks);
        if (!parsedBlocks) {
            return false;
        }
        return parsedBlocks.some((block) => hasRenderableDisplayBlock(block));
    }

    function renderSingleDisplayBlock(block) {
        const blockType = String(block.type || '').toLowerCase();
        if (blockType === 'code') {
            return renderCodeBlock(block);
        }
        if (blockType === 'table') {
            return renderTableBlock(block);
        }
        if (blockType === 'callout') {
            const tone = String(block.tone || 'info').toLowerCase();
            const title = String(block.title || '').trim();
            const body = renderMarkdown(getBlockText(block, false));
            return `
                <div class="message-block">
                    <div class="message-callout is-${escapeHtml(tone)}">
                        ${title ? `<div class="message-callout-title">${escapeHtml(title)}</div>` : ''}
                        <div class="message-callout-content">${body}</div>
                    </div>
                </div>
            `;
        }
        if (blockType === 'tool_result') {
            const status = String(block.status || 'info').toLowerCase();
            const title = String(block.title || 'Tool result').trim() || 'Tool result';
            const body = renderMarkdown(getBlockText(block, false));
            return `
                <div class="message-block">
                    <div class="message-tool-result is-${escapeHtml(status)}">
                        <div class="message-tool-result-title">${escapeHtml(title)}</div>
                        <div class="message-tool-result-content">${body}</div>
                    </div>
                </div>
            `;
        }
        return `<div class="message-block">${renderMarkdown(getBlockText(block, false))}</div>`;
    }

    function renderCodeBlock(block) {
        const language = String(block.language || block.lang || '').trim();
        const codeText = getBlockText(block, true);
        const escapedLanguage = escapeHtml(language || 'text');
        const escapedCode = escapeHtml(codeText);
        const classLanguage = language.toLowerCase().replace(/[^a-z0-9_+-]/g, '');
        return `
            <div class="message-block message-codeblock">
                <div class="message-codeblock-toolbar">
                    <span class="message-codeblock-lang">${escapedLanguage}</span>
                    <button type="button" class="copy-code-button">Copy</button>
                </div>
                <pre><code class="${classLanguage ? `language-${classLanguage}` : ''}">${escapedCode}</code></pre>
            </div>
        `;
    }

    function renderTableBlock(block) {
        const headers = Array.isArray(block.headers)
            ? block.headers
            : (Array.isArray(block.columns) ? block.columns : []);
        const rows = Array.isArray(block.rows) ? block.rows : [];
        if (!headers.length && !rows.length) {
            return `<div class="message-block message-table-wrap">${renderMarkdown(getBlockText(block))}</div>`;
        }
        const headHtml = headers.length
            ? `<thead><tr>${headers.map((h) => `<th>${escapeHtml(String(h ?? ''))}</th>`).join('')}</tr></thead>`
            : '';
        const bodyHtml = rows.length
            ? `<tbody>${rows.map((row) => `<tr>${(Array.isArray(row) ? row : []).map((cell) => `<td>${escapeHtml(String(cell ?? ''))}</td>`).join('')}</tr>`).join('')}</tbody>`
            : '';
        return `<div class="message-block message-table-wrap"><table>${headHtml}${bodyHtml}</table></div>`;
    }

    async function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.position = 'absolute';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
    }

    function enhanceRenderedMessage(root) {
        if (!root) return;
        if (typeof hljs !== 'undefined') {
            root.querySelectorAll('pre code').forEach((block) => {
                if (!block.classList.contains('hljs')) {
                    hljs.highlightElement(block);
                }
            });
        }
        root.querySelectorAll('pre').forEach((pre) => {
            let toolbar = pre.previousElementSibling;
            if (!toolbar || !toolbar.classList.contains('message-codeblock-toolbar')) {
                toolbar = document.createElement('div');
                toolbar.className = 'message-codeblock-toolbar';
                toolbar.innerHTML = '<span class="message-codeblock-lang"></span><button type="button" class="copy-code-button">Copy</button>';
                pre.parentNode.insertBefore(toolbar, pre);
            }
            const code = pre.querySelector('code');
            const langEl = toolbar.querySelector('.message-codeblock-lang');
            if (langEl && (!langEl.textContent || langEl.textContent === 'text')) {
                const classMatch = code?.className.match(/language-([a-z0-9_+-]+)/i);
                langEl.textContent = classMatch ? classMatch[1] : 'text';
            }
            const copyButton = toolbar.querySelector('.copy-code-button');
            if (copyButton && !copyButton.dataset.bound) {
                copyButton.dataset.bound = 'true';
                copyButton.addEventListener('click', async function() {
                    try {
                        await copyText(code ? code.textContent || '' : pre.textContent || '');
                        this.textContent = 'Copied!';
                        this.classList.add('copied');
                        setTimeout(() => {
                            this.textContent = 'Copy';
                            this.classList.remove('copied');
                        }, 2000);
                    } catch (error) {
                        this.textContent = 'Copy failed';
                        setTimeout(() => { this.textContent = 'Copy'; }, 2000);
                    }
                });
            }
        });
    }

    // Close skill and file selector when clicking outside
    document.addEventListener('click', function(e) {
        if (!skillSelector.contains(e.target)) {
            hideSkillSelector();
        }
    });

    // Handle spoiler click-to-reveal
    messagesContainer.addEventListener('click', function(e) {
        if (e.target.classList.contains('spoiler')) {
            e.target.classList.toggle('revealed');
        }
    });

    /**
     * Escape HTML special characters
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Scroll messages container to bottom
     */
    function scrollToBottom() {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    /**
     * Add a streaming message placeholder for SSE
     */
    function createStreamingMessage() {
        const div = document.createElement('div');
        div.className = 'message assistant streaming-message';
        div.innerHTML = `
            <div class="avatar" aria-hidden="true">AI</div>
            <div>
                <div class="message-bubble"><span class="streaming-text"></span><span class="cursor-blink"></span></div>
            </div>
        `;
        messagesContainer.appendChild(div);
        scrollToBottom();
        return div;
    }

    /**
     * Update streaming message content
     */
    function updateStreamingMessage(div, content) {
        const bubble = div.querySelector('.message-bubble');
        const textSpan = bubble.querySelector('.streaming-text');
        textSpan.innerHTML = renderMarkdown(content);
        scrollToBottom();
    }

    /**
     * Finish streaming message
     */
    function finishStreamingMessage(div) {
        div.classList.remove('streaming-message');
        div.classList.add('streaming-finished');
        const bubble = div.querySelector('.message-bubble');
        const timestamp = document.createElement('div');
        timestamp.className = 'message-timestamp';
        timestamp.setAttribute('aria-label', 'Message time');
        timestamp.textContent = formatSmartDate(new Date());
        bubble.appendChild(timestamp);

        enhanceRenderedMessage(div);
    }

    /**
     * Send message to the server with SSE streaming
     */
    async function sendMessage() {
        if (isLoading) return;

        const content = messageInput.value.trim();

        const busyAttachments = pendingAttachments.filter((a) => a.status === 'uploading' || a.status === 'parsing');
        if (busyAttachments.length) {
            setStatus(`Waiting for ${busyAttachments.length} attachment(s)...`, 'uploading');
            return;
        }

        const readyAttachments = pendingAttachments.filter((a) => a.file_id && a.status !== 'error');
        const attachmentIds = readyAttachments.map((a) => a.file_id);

        if (!content && attachmentIds.length === 0) return;

        isLoading = true;
        sendButton.disabled = true;

        messageInput.value = '';
        messageInput.style.height = 'auto';
        messageInput.blur();
        messageInput.focus();

        const displayContent = content || readyAttachments.map((a) => `📎 ${a.filename}`).join('\n') || '📎 Attachment';
        addMessage('user', displayContent);

        pendingAttachments = [];
        renderPendingAttachments();

        statusSpan.textContent = 'Thinking...';
        typingIndicator.classList.remove('show');
        resetEvents();

        await sendMessageFallback(content, attachmentIds);

        isLoading = false;
        sendButton.disabled = false;
        messageInput.focus();
    }

    /**
     * Fallback: Send message using regular API
     */
    async function sendMessageFallback(content, attachmentIds = []) {
        try {
            const requestSessionId = ensureCurrentSessionId();

            console.log('[WebChat] Generated session_id:', requestSessionId);

            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message: content || (attachmentIds.length ? '[attachment]' : ''),
                    session_id: requestSessionId,
                    attachments: attachmentIds
                })
            });

            const data = await response.json();
            console.log('[WebChat] API response:', JSON.stringify(data, null, 2));

            if (data.error) {
                addMessage('error', `Error: ${data.error}`);
                statusSpan.textContent = 'Error';
                return;
            }

            // Update current session ID from response and persist
            if (data.session_id) {
                currentSessionId = data.session_id;
                localStorage.setItem(SESSION_ID_KEY, currentSessionId);
            }

            // Helper: check if session has a final assistant message
            function hasFinalAssistant(sessionData) {
                if (!sessionData.messages || !sessionData.messages.length) return false;
                // Find last assistant message
                const lastMsg = [...sessionData.messages].reverse().find(m => m.role === 'assistant');
                if (!lastMsg) return false;
                const hasTextContent = typeof lastMsg.content === 'string' && lastMsg.content.trim().length > 0;
                const hasDisplayBlocks = hasMeaningfulDisplayBlocks(lastMsg.display_blocks);
                return hasTextContent || hasDisplayBlocks;
            }

            // Try to fetch session and poll if needed
            let sessionData = null;
            let pollCount = 0;
            const maxPoll = 12; // e.g. 12*1.5s = 18s max
            const pollInterval = 1500;
            let gotFinal = false;

            async function pollSessionUntilFinal() {
                while (pollCount < maxPoll) {
                    pollCount++;
                    const sessionResponse = await fetch('/api/sessions/' + encodeURIComponent(currentSessionId));
                    sessionData = await sessionResponse.json();
                    if (hasFinalAssistant(sessionData)) {
                        gotFinal = true;
                        break;
                    }
                    statusSpan.textContent = 'Waiting for final reply... (' + pollCount + ')';
                    await new Promise(r => setTimeout(r, pollInterval));
                }
            }

            // First try
            const sessionResponse = await fetch('/api/sessions/' + encodeURIComponent(currentSessionId));
            sessionData = await sessionResponse.json();
            if (!hasFinalAssistant(sessionData)) {
                // Assistant reply not ready, start polling
                statusSpan.textContent = 'Waiting for final reply...';
                await pollSessionUntilFinal();
            } else {
                gotFinal = true;
            }

            // Render messages if got final, else fallback
            if (gotFinal && sessionData && sessionData.messages && sessionData.messages.length > 0) {
                messagesContainer.innerHTML = '';
                sessionData.messages.forEach(msg => {
                    const role = msg.role || 'user';
                    if (!isDebugEnabled()) {
                        if (role === 'tool') return;
                        if (!isDebugEnabled() && isToolPlaceholder(msg.content, role)) return;
                    }
                    const content = msg.content || '';
                    const timestamp = msg.timestamp || msg.created_at;
                    addMessage(
                        role,
                        content,
                        timestamp,
                        msg.tool_calls,
                        msg.display_blocks || null
                    );
                });
                scrollToBottom();
                // Show thinking events from session metadata
                const metadata = sessionData.metadata || {};
                const thinkingEvents = metadata.thinking_events || [];
                if (thinkingEvents.length > 0) {
                    const assistantMessages = messagesContainer.querySelectorAll('.message.assistant');
                    if (assistantMessages.length > 0) {
                        const lastAssistant = assistantMessages[assistantMessages.length - 1];
                        if (!lastAssistant.classList.contains('has-thinking')) {
                            const eventsSnapshot = thinkingEvents.map(event => ({
                                type: event.type,
                                data: event.data || {},
                                display: event.display || {
                                    icon: '📌',
                                    name: event.type,
                                    message: JSON.stringify(event.data || {}).substring(0, 50)
                                }
                            }));
                            lastAssistant.classList.add('has-thinking');
                            const bubble = lastAssistant.querySelector('.message-bubble');
                            if (bubble) {
                                const toggleBtn = document.createElement('button');
                                toggleBtn.className = 'thinking-process-toggle';
                                const eventCount = eventsSnapshot.length;
                                toggleBtn.innerHTML = `
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <circle cx="12" cy="12" r="10"/>
                                        <path d="M12 16v-4"/>
                                        <path d="M12 8h.01"/>
                                    </svg>
                                    <span>View Thinking Process (${eventCount} steps)</span>
                                    <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <polyline points="6 9 12 15 18 9"/>
                                    </svg>
                                `;
                                const processContent = document.createElement('div');
                                processContent.className = 'thinking-process-content';
                                processContent.style.display = 'none';
                                let timelineHtml = '<div class="thinking-timeline">';
                                eventsSnapshot.forEach((event, index) => {
                                    const display = event.display;
                                    const isLast = index === eventsSnapshot.length - 1;
                                    timelineHtml += `
                                        <div class="thinking-item ${isLast ? 'last' : ''}">
                                            <div class="thinking-icon">${display.icon}</div>
                                            <div class="thinking-details">
                                                <div class="thinking-name">${display.name}</div>
                                                <div class="thinking-message">${escapeHtml(display.message)}</div>
                                            </div>
                                        </div>`;
                                });
                                timelineHtml += '</div>';
                                processContent.innerHTML = timelineHtml;
                                toggleBtn.addEventListener('click', function() {
                                    const isHidden = processContent.style.display === 'none';
                                    processContent.style.display = isHidden ? 'block' : 'none';
                                    toggleBtn.querySelector('.chevron').style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
                                });
                                bubble.appendChild(toggleBtn);
                                bubble.appendChild(processContent);
                            }
                        }
                    }
                }
            } else {
                // Fallback to simple response display
                addMessage(
                    'assistant',
                    data.response || '[No reply received. Please try again later.]',
                    null,
                    null,
                    data.display_blocks || null
                );
            }

            if (data.usage) {
                if (data.usage.total_tokens) {
                    totalTokens += data.usage.total_tokens;
                    tokenCountSpan.textContent = `Tokens: ${totalTokens}`;
                }
                if (data.usage.cost) {
                    totalCost += data.usage.cost;
                    costDisplaySpan.textContent = `Cost: $${totalCost.toFixed(4)}`;
                }
            }

            statusSpan.textContent = 'Ready';

            // Refresh sessions list to show the new session
            loadRecentSessions();
        } catch (error) {
            addMessage('error', `Connection error: ${error.message}`);
            statusSpan.textContent = 'Disconnected';
        }
    }

    // Expose sendMessage globally for testing
    window.webchatSendMessage = sendMessage;
    window.webchatAddMessage = addMessage;

    // ========== Recent Sessions ==========

    const recentSessionsList = document.getElementById('recentSessionsList');
    const refreshSessionsBtn = document.getElementById('refreshSessions');
    const SESSIONS_LIMIT = 20;
    let sessionsOffset = 0;
    let sessionsHasMore = true;
    let sessionsLoading = false;
    let sessionsObserver = null;

    // Create sentinel element for infinite scroll
    function createSessionsSentinel() {
        const sentinel = document.createElement('div');
        sentinel.id = 'sessions-sentinel';
        sentinel.className = 'loading-sessions';
        sentinel.textContent = 'Loading more...';
        sentinel.style.display = 'none';
        return sentinel;
    }

    /**
     * Load recent sessions from API with pagination
     */
    async function loadRecentSessions(reset = false) {
        if (!recentSessionsList) return;

        // Reset if requested
        if (reset) {
            sessionsOffset = 0;
            sessionsHasMore = true;
            recentSessionsList.innerHTML = '';
            // Recreate sentinel
            const sentinel = createSessionsSentinel();
            recentSessionsList.appendChild(sentinel);
        }

        if (!sessionsHasMore || sessionsLoading) return;

        sessionsLoading = true;

        // Show loading on first load
        if (sessionsOffset === 0) {
            const loadingMsg = document.createElement('div');
            loadingMsg.id = 'sessions-loading';
            loadingMsg.className = 'loading-sessions';
            loadingMsg.textContent = 'Loading...';
            recentSessionsList.insertBefore(loadingMsg, recentSessionsList.firstChild);
        }

        try {
            const response = await fetch(`/api/sessions?limit=${SESSIONS_LIMIT}&offset=${sessionsOffset}`);
            const data = await response.json();

            if (data.error || !data.sessions) {
                recentSessionsList.innerHTML = '<div class="loading-sessions">No recent sessions</div>';
                sessionsHasMore = false;
                return;
            }

            // Clear loading message on first load
            if (sessionsOffset === 0) {
                const loadingMsg = document.getElementById('sessions-loading');
                if (loadingMsg) loadingMsg.remove();
            }

            const sessions = data.sessions || [];
            sessionsHasMore = data.has_more !== false;

            if (sessions.length === 0) {
                if (sessionsOffset === 0) {
                    recentSessionsList.innerHTML = '<div class="loading-sessions">No recent sessions</div>';
                }
                sessionsHasMore = false;
                return;
            }

            // Get current active session
            const currentActiveId = recentSessionsList.querySelector('.recent-session-item.active')?.getAttribute('data-session-id');

            // Append new sessions
            sessions.forEach((session, index) => {
                const sessionId = session.session_id || ('session_' + (sessionsOffset + index));
                const isActive = sessionId === currentActiveId || (index === 0 && sessionsOffset === 0 && !currentActiveId);
                const sessionName = session.name || session.session_id || ('Chat ' + (sessionsOffset + index + 1));

                const item = document.createElement('div');
                item.className = `recent-session-item ${isActive ? 'active' : ''}`;
                item.setAttribute('data-session-id', sessionId);
                item.innerHTML = `
                    <svg class="recent-session-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <div class="recent-session-info">
                        <div class="recent-session-name">${escapeHtml(sessionName)}</div>
                        <div class="recent-session-preview">${escapeHtml(session.last_message || '')}</div>
                    </div>
                    <div class="recent-session-actions">
                        <button type="button" class="session-action-btn" data-session-action="rename" title="Rename session">Rename</button>
                        <button type="button" class="session-action-btn danger" data-session-action="delete" title="Delete session">Delete</button>
                    </div>
                `;

                // Add click handler
                item.addEventListener('click', function(e) {
                    const actionEl = e.target.closest('[data-session-action]');
                    if (actionEl) {
                        e.preventDefault();
                        e.stopPropagation();
                        const sid = this.getAttribute('data-session-id');
                        const action = actionEl.getAttribute('data-session-action');
                        if (action === 'rename' && sid) {
                            renameSessionById(sid, sessionName);
                        } else if (action === 'delete' && sid) {
                            deleteSessionById(sid);
                        }
                        return;
                    }

                    e.preventDefault();
                    const sid = this.getAttribute('data-session-id');
                    console.log('Clicked session:', sid);
                    if (sid && sid !== 'undefined') {
                        loadSession(sid);
                        recentSessionsList.querySelectorAll('.recent-session-item').forEach(i => i.classList.remove('active'));
                        this.classList.add('active');
                    }
                });

                // Insert before sentinel so sentinel stays at bottom
                const sentinel = document.getElementById('sessions-sentinel');
                if (sentinel) {
                    recentSessionsList.insertBefore(item, sentinel);
                } else {
                    recentSessionsList.appendChild(item);
                }
            });

            // Update offset for next load
            sessionsOffset += sessions.length;

            // Ensure sentinel exists at bottom
            let sentinel = document.getElementById('sessions-sentinel');
            if (!sentinel) {
                sentinel = createSessionsSentinel();
                recentSessionsList.appendChild(sentinel);
            }

            // Always update sentinel visibility at the end
            if (sentinel) {
                sentinel.style.display = sessionsHasMore ? 'block' : 'none';
                sentinel.textContent = sessionsHasMore ? 'Loading more...' : 'No more sessions';
            }

            // Setup intersection observer for infinite scroll (only once)
            if (sessionsHasMore && !sessionsObserver) {
                sessionsObserver = new IntersectionObserver((entries) => {
                    entries.forEach(entry => {
                        if (entry.isIntersecting && sessionsHasMore && !sessionsLoading) {
                            loadRecentSessions(false);
                        }
                    });
                }, { rootMargin: '100px' });

                if (sentinel) {
                    sessionsObserver.observe(sentinel);
                }
            }

            // Auto-load first session if none selected
            if (sessionsOffset === sessions.length) {
                // If we have a saved sessionId, load that one
                if (currentSessionId) {
                    const savedItem = recentSessionsList.querySelector(`[data-session-id="${currentSessionId}"]`);
                    if (savedItem) {
                        console.log('Auto-loading saved session:', currentSessionId);
                        // Add active class to the saved session
                        recentSessionsList.querySelectorAll('.recent-session-item').forEach(i => i.classList.remove('active'));
                        savedItem.classList.add('active');
                        loadSession(currentSessionId);
                    } else {
                        // Saved session not in list, load first available
                        const firstSession = recentSessionsList.querySelector('.recent-session-item');
                        if (firstSession) {
                            const firstSessionId = firstSession.getAttribute('data-session-id');
                            console.log('Saved session not found, loading first:', firstSessionId);
                            loadSession(firstSessionId);
                        }
                    }
                } else {
                    // No saved session, load first available
                    const firstSession = recentSessionsList.querySelector('.recent-session-item');
                    if (firstSession) {
                        const firstSessionId = firstSession.getAttribute('data-session-id');
                        console.log('Auto-loading first session:', firstSessionId);
                        loadSession(firstSessionId);
                    }
                }
            }

        } catch (error) {
            console.error('Error loading sessions:', error);
            if (sessionsOffset === 0) {
                recentSessionsList.innerHTML = '<div class="loading-sessions">Error loading sessions</div>';
            }
        } finally {
            sessionsLoading = false;
        }
    }

    async function renameSessionById(sessionId, currentName) {
        const inputName = prompt('Rename session', currentName || 'New Chat');
        if (inputName === null) return;
        const name = String(inputName || '').trim();
        if (!name) {
            alert('Session name cannot be empty.');
            return;
        }
        if (name === String(currentName || '').trim()) return;

        try {
            const response = await fetch('/api/sessions/' + encodeURIComponent(sessionId) + '/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            const data = await response.json();
            if (!response.ok || data.error) {
                alert(data.error || 'Failed to rename session.');
                return;
            }
            loadRecentSessions(true);
        } catch (error) {
            console.error('Error renaming session:', error);
            alert('Failed to rename session.');
        }
    }

    async function deleteSessionById(sessionId) {
        const confirmed = confirm('Delete this session? This cannot be undone.');
        if (!confirmed) return;

        try {
            const response = await fetch('/api/sessions/' + encodeURIComponent(sessionId), {
                method: 'DELETE',
            });
            const data = await response.json();
            if (!response.ok || data.error) {
                alert(data.error || 'Failed to delete session.');
                return;
            }

            if (currentSessionId === sessionId) {
                currentSessionId = null;
                localStorage.removeItem(SESSION_ID_KEY);
                messagesContainer.innerHTML = `
                    <div class="welcome-message">
                        <h2>👋 New Chat</h2>
                        <p>Start a new conversation</p>
                    </div>
                `;
                statusSpan.textContent = 'Ready';
                if (newChatBtn) newChatBtn.classList.add('active');
            }
            loadRecentSessions(true);
        } catch (error) {
            console.error('Error deleting session:', error);
            alert('Failed to delete session.');
        }
    }

    // Refresh button handler
    if (refreshSessionsBtn) {
        refreshSessionsBtn.addEventListener('click', () => {
            loadRecentSessions(true);
        });
    }

    /**
     * Load a specific session
     */
    async function loadSession(sessionId) {
        console.log('loadSession called with:', sessionId);
        if (!sessionId || sessionId === 'undefined' || sessionId === 'null') {
            console.error('Invalid session ID:', sessionId);
            return;
        }

        currentSessionId = sessionId;
        messagesContainer.innerHTML = '<div class="loading">Loading...</div>';
        statusSpan.textContent = `Loading: ${sessionId}`;

        try {
            const response = await fetch('/api/sessions/' + encodeURIComponent(sessionId));
            const data = await response.json();

            if (data.error) {
                messagesContainer.innerHTML = `<div class="welcome-message">
                    <h2>👋 Error</h2>
                    <p>${escapeHtml(data.error)}</p>
                </div>`;
                statusSpan.textContent = 'Error loading session';
                return;
            }

            // Clear and render messages
            messagesContainer.innerHTML = '';

            const messages = data.messages || [];
            if (messages.length === 0) {
                messagesContainer.innerHTML = `<div class="welcome-message">
                    <h2>👋 ${escapeHtml(data.name || 'New Chat')}</h2>
                    <p>Start a conversation</p>
                </div>`;
            } else {
                messages.forEach(msg => {
                    const role = msg.role || 'user';
                    // Skip tool placeholder messages when debug is off
                    if (!isDebugEnabled() && isToolPlaceholder(msg.content, role)) {
                        return;
                    }
                    addMessage(
                        role,
                        msg.content || '',
                        msg.timestamp || msg.created_at,
                        msg.tool_calls,
                        msg.display_blocks || null
                    );
                });
            }

            // Show thinking events from session metadata
            const metadata = data.metadata || {};
            const thinkingEvents = metadata.thinking_events || [];
            if (thinkingEvents.length > 0) {
                // Get the last assistant message
                const assistantMessages = messagesContainer.querySelectorAll('.message.assistant');
                if (assistantMessages.length > 0) {
                    const lastAssistant = assistantMessages[assistantMessages.length - 1];
                    if (!lastAssistant.classList.contains('has-thinking')) {
                        // Create events snapshot from stored events
                        const eventsSnapshot = thinkingEvents.map(event => ({
                            type: event.type,
                            data: event.data || {},
                            display: event.display || {
                                icon: '📌',
                                name: event.type,
                                message: JSON.stringify(event.data || {}).substring(0, 50)
                            }
                        }));

                        // Manually add thinking process button
                        lastAssistant.classList.add('has-thinking');
                        const bubble = lastAssistant.querySelector('.message-bubble');
                        if (bubble) {
                            // Create toggle button
                            const toggleBtn = document.createElement('button');
                            toggleBtn.className = 'thinking-process-toggle';
                            const eventCount = eventsSnapshot.length;
                            toggleBtn.innerHTML = `
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <circle cx="12" cy="12" r="10"/>
                                    <path d="M12 16v-4"/>
                                    <path d="M12 8h.01"/>
                                </svg>
                                <span>View Thinking Process (${eventCount} steps)</span>
                                <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="6 9 12 15 18 9"/>
                                </svg>
                            `;

                            // Create content
                            const processContent = document.createElement('div');
                            processContent.className = 'thinking-process-content';
                            processContent.style.display = 'none';

                            let timelineHtml = '<div class="thinking-timeline">';
                            eventsSnapshot.forEach((event, index) => {
                                const display = event.display;
                                const isLast = index === eventsSnapshot.length - 1;
                                timelineHtml += `
                                    <div class="thinking-item ${isLast ? 'last' : ''}">
                                        <div class="thinking-icon">${display.icon}</div>
                                        <div class="thinking-details">
                                            <div class="thinking-name">${display.name}</div>
                                            <div class="thinking-message">${escapeHtml(display.message)}</div>
                                        </div>
                                    </div>
                                `;
                            });
                            timelineHtml += '</div>';
                            processContent.innerHTML = timelineHtml;

                            bubble.appendChild(toggleBtn);
                            bubble.appendChild(processContent);

                            // Toggle
                            let expanded = false;
                            toggleBtn.addEventListener('click', function(e) {
                                e.preventDefault();
                                expanded = !expanded;
                                if (expanded) {
                                    processContent.style.display = 'block';
                                    toggleBtn.classList.add('expanded');
                                    toggleBtn.innerHTML = `
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <circle cx="12" cy="12" r="10"/>
                                            <path d="M12 16v-4"/>
                                            <path d="M12 8h.01"/>
                                        </svg>
                                        <span>Hide Thinking Process</span>
                                        <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <polyline points="18 15 12 9 6 15"/>
                                        </svg>
                                    `;
                                } else {
                                    processContent.style.display = 'none';
                                    toggleBtn.classList.remove('expanded');
                                    toggleBtn.innerHTML = `
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <circle cx="12" cy="12" r="10"/>
                                            <path d="M12 16v-4"/>
                                            <path d="M12 8h.01"/>
                                        </svg>
                                        <span>View Thinking Process (${eventCount} steps)</span>
                                        <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <polyline points="6 9 12 15 18 9"/>
                                        </svg>
                                    `;
                                }
                            });
                        }
                    }
                }
            }

            // Remove active state from New Chat button when loading a session
            if (newChatBtn) newChatBtn.classList.remove('active');

            statusSpan.textContent = 'Ready';

        } catch (error) {
            console.error('Error loading session:', error);
            messagesContainer.innerHTML = `<div class="welcome-message">
                <h2>👋 Error</h2>
                <p>Failed to load session</p>
            </div>`;
            statusSpan.textContent = 'Error';
        }
    }

    // Load recent sessions on page load
    loadRecentSessions(true);

    // Set New Chat button active if no persisted session
    if (!localStorage.getItem(SESSION_ID_KEY) && newChatBtn) {
        newChatBtn.classList.add('active');
    }

    // ========== Sidebar Actions ==========

    document.addEventListener('click', function(e) {
        const action = e.target.closest('[data-action]')?.dataset.action;
        console.log('Sidebar click action:', action);
        if (!action) return;

        if (action === 'new-chat') {
            currentSessionId = null;
            localStorage.removeItem(SESSION_ID_KEY);  // Clear persisted session
            messagesContainer.innerHTML = `
                <div class="welcome-message">
                    <h2>👋 New Chat</h2>
                    <p>Start a new conversation</p>
                </div>
            `;
            statusSpan.textContent = 'Ready';
            recentSessionsList.querySelectorAll('.recent-session-item').forEach(i => i.classList.remove('active'));
            if (newChatBtn) newChatBtn.classList.add('active');
        } else if (action === 'files' || action === 'server-files') {
            showFileExplorer();
        } else if (action === 'settings') {
            showSettings();
        }
    });

    // ========== File Explorer ==========

    const fileExplorerPanel = document.getElementById('fileExplorerPanel');
    const closeFileExplorer = document.getElementById('closeFileExplorer');
    const fileExplorerContent = document.getElementById('fileExplorerContent');
    const serverFilesUploadInput = document.createElement('input');
    serverFilesUploadInput.type = 'file';
    serverFilesUploadInput.style.display = 'none';
    document.body.appendChild(serverFilesUploadInput);
    let serverFilesCurrentPath = '';
    let selectedServerFilePaths = new Set();

    async function parseJsonSafe(response) {
        try {
            return await response.json();
        } catch (error) {
            return null;
        }
    }

    function getServerFilesErrorMessage(payload, fallback = 'Request failed') {
        if (!payload) return fallback;
        if (payload.error) {
            return payload.detail ? `${payload.error}: ${payload.detail}` : payload.error;
        }
        return fallback;
    }

    function updateServerFilesToolbarState() {
        const downloadBtn = fileExplorerContent.querySelector('#serverFilesDownloadBtn');
        const deleteBtn = fileExplorerContent.querySelector('#serverFilesDeleteBtn');
        if (!downloadBtn || !deleteBtn) return;
        const count = selectedServerFilePaths.size;
        downloadBtn.disabled = count === 0;
        deleteBtn.disabled = count === 0;
        downloadBtn.textContent = count > 0 ? `Download (${count})` : 'Download';
        deleteBtn.textContent = count > 0 ? `Delete (${count})` : 'Delete';
    }

    async function uploadServerFile(file) {
        if (!file) return;
        setStatus(`Uploading ${file.name}...`, 'uploading');

        const formData = new FormData();
        formData.append('path', serverFilesCurrentPath || '');
        formData.append('file', file);

        try {
            const response = await fetch('/api/server-files/upload', {
                method: 'POST',
                body: formData
            });
            const payload = await parseJsonSafe(response);

            if (!response.ok || (payload && payload.success === false)) {
                const errorMessage = getServerFilesErrorMessage(payload, `Failed to upload ${file.name}`);
                setStatus(`Upload failed: ${errorMessage}`, 'error');
                return;
            }

            const extractedCount = payload?.mode === 'zip_extract' ? payload.extracted_count || 0 : null;
            const successMessage = extractedCount !== null
                ? `Uploaded ${file.name} and extracted ${extractedCount} item(s)`
                : `Uploaded ${file.name}`;
            setStatus(successMessage, 'success');
            await showFileExplorer(serverFilesCurrentPath);
        } catch (error) {
            console.error('Server file upload error:', error);
            setStatus(`Upload failed: ${error.message}`, 'error');
        }
    }

    async function deleteServerFiles(paths) {
        if (!paths.length) return;
        const confirmed = confirm(`Delete ${paths.length} selected item(s)? This cannot be undone.`);
        if (!confirmed) return;

        setStatus(`Deleting ${paths.length} item(s)...`, 'uploading');
        try {
            const response = await fetch('/api/server-files/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths })
            });
            const payload = await parseJsonSafe(response);

            if (!response.ok || (payload && payload.success === false)) {
                const errorMessage = getServerFilesErrorMessage(payload, 'Failed to delete selected items');
                setStatus(`Delete failed: ${errorMessage}`, 'error');
                return;
            }

            setStatus(`Deleted ${paths.length} item(s)`, 'success');
            selectedServerFilePaths = new Set();
            await showFileExplorer(serverFilesCurrentPath);
        } catch (error) {
            console.error('Server file delete error:', error);
            setStatus(`Delete failed: ${error.message}`, 'error');
        }
    }

    function downloadServerFiles(paths) {
        if (!paths.length) return;
        const url = new URL('/api/server-files/download', window.location.origin);
        paths.forEach(path => url.searchParams.append('paths', path));

        const link = document.createElement('a');
        link.href = url.toString();
        link.download = '';
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    async function showFileExplorer(path = '') {
        fileExplorerPanel.classList.add('show');
        fileExplorerContent.innerHTML = '<div class="loading">Loading...</div>';
        const feTitle = document.getElementById('fileExplorerTitle');
        if (feTitle) feTitle.textContent = 'Server Files';

        try {
            const browseUrl = path ? `/api/server-files?path=${encodeURIComponent(path)}` : '/api/server-files';
            const response = await fetch(browseUrl);
            const data = await response.json();

            if (data.error) {
                fileExplorerContent.innerHTML = `<div class="loading">${escapeHtml(data.error)}</div>`;
                return;
            }

            const rootPath = data.root_path || '';
            const activePath = data.path || rootPath;
            serverFilesCurrentPath = activePath;
            selectedServerFilePaths = new Set();
            let pathParts = [];
            if (rootPath && activePath.startsWith(rootPath)) {
                const relativePath = activePath.slice(rootPath.length).replace(/^\/+/, '');
                pathParts = relativePath ? relativePath.split('/') : [];
            } else {
                pathParts = activePath.split('/').filter(p => p);
            }

            let pathHtml = '<div class="file-explorer-path">';
            pathHtml += '<button data-path="">workspace</button>';
            let currentPath = rootPath;
            pathParts.forEach(part => {
                currentPath = currentPath ? `${currentPath}/${part}` : part;
                pathHtml += '<span class="separator">/</span>';
                pathHtml += '<button data-path="' + currentPath + '">' + escapeHtml(part) + '</button>';
            });
            pathHtml += '</div>';
            pathHtml += `
                <div class="file-explorer-toolbar" style="display:flex;gap:8px;margin:8px 0 10px 0;">
                    <button type="button" id="serverFilesUploadBtn">Upload</button>
                    <button type="button" id="serverFilesDownloadBtn" disabled>Download</button>
                    <button type="button" id="serverFilesDeleteBtn" disabled>Delete</button>
                </div>
            `;

            if (data.items.length === 0) {
                fileExplorerContent.innerHTML = pathHtml + '<div class="file-explorer-empty">Empty directory</div>';
                const uploadBtn = fileExplorerContent.querySelector('#serverFilesUploadBtn');
                if (uploadBtn) {
                    uploadBtn.addEventListener('click', () => {
                        serverFilesUploadInput.value = '';
                        serverFilesUploadInput.click();
                    });
                }
                return;
            }

            fileExplorerContent.innerHTML = pathHtml + `
                <div class="file-explorer-list">
                    ${data.items.map(item => `
                        <div class="file-explorer-item" data-path="${item.path}" data-is-dir="${item.is_dir}">
                            <input type="checkbox" class="server-file-select" data-path="${item.path}" title="Select for download/delete" />
                            ${item.is_dir ?
                                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' :
                                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
                            }
                            <span class="file-name">${escapeHtml(item.name)}</span>
                        </div>
                    `).join('')}
                </div>
            `;

            const uploadBtn = fileExplorerContent.querySelector('#serverFilesUploadBtn');
            if (uploadBtn) {
                uploadBtn.addEventListener('click', () => {
                    serverFilesUploadInput.value = '';
                    serverFilesUploadInput.click();
                });
            }
            const deleteBtn = fileExplorerContent.querySelector('#serverFilesDeleteBtn');
            if (deleteBtn) {
                deleteBtn.addEventListener('click', () => {
                    deleteServerFiles(Array.from(selectedServerFilePaths));
                });
            }
            const downloadBtn = fileExplorerContent.querySelector('#serverFilesDownloadBtn');
            if (downloadBtn) {
                downloadBtn.addEventListener('click', () => {
                    downloadServerFiles(Array.from(selectedServerFilePaths));
                });
            }
            fileExplorerContent.querySelectorAll('.server-file-select').forEach(checkbox => {
                checkbox.addEventListener('click', e => e.stopPropagation());
                checkbox.addEventListener('change', function() {
                    const selectedPath = this.dataset.path;
                    if (this.checked) {
                        selectedServerFilePaths.add(selectedPath);
                    } else {
                        selectedServerFilePaths.delete(selectedPath);
                    }
                    updateServerFilesToolbarState();
                });
            });
            updateServerFilesToolbarState();

            // Add click handlers
            fileExplorerContent.querySelectorAll('.file-explorer-path button').forEach(btn => {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    showFileExplorer(this.dataset.path);
                });
            });

            fileExplorerContent.querySelectorAll('.file-explorer-item').forEach(item => {
                item.addEventListener('click', function(e) {
                    if (e.target.closest('.server-file-select')) return;
                    e.stopPropagation();
                    const isDir = this.dataset.isDir === 'true';
                    const path = this.dataset.path;
                    console.log('File item clicked:', { path, isDir });
                    if (isDir) {
                        showFileExplorer(path);
                    } else {
                        // Double-click to view file content
                        showFileViewer(path);
                    }
                });

                // Right-click for options
                item.addEventListener('contextmenu', function(e) {
                    e.preventDefault();
                    const path = this.dataset.path;
                    const isDir = this.dataset.isDir === 'true';

                    // Insert path in chat
                    messageInput.value += path;
                    messageInput.focus();
                    fileExplorerPanel.classList.remove('show');
                });
            });

        } catch (error) {
            console.error('Error loading files:', error);
            fileExplorerContent.innerHTML = '<div class="loading">Error loading files</div>';
        }
    }

    serverFilesUploadInput.addEventListener('change', async function() {
        const file = this.files && this.files[0];
        if (!file) return;
        await uploadServerFile(file);
    });

    if (closeFileExplorer) {
        closeFileExplorer.addEventListener('click', function() {
            fileExplorerPanel.classList.remove('show');
        });
    }

    // ========== File Viewer ==========

    const fileViewerPanel = document.getElementById('fileViewerPanel');
    const closeFileViewer = document.getElementById('closeFileViewer');
    const fileViewerTitleText = document.getElementById('fileViewerTitleText');
    const fileViewerContent = document.getElementById('fileViewerContent');

    function getFileNameFromPath(path) {
        const parts = (path || '').split('/').filter(Boolean);
        return parts.length ? parts[parts.length - 1] : 'workspace-file';
    }

    function shouldInlinePreview(path) {
        const lowerPath = (path || '').toLowerCase();
        const imageExt = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'];
        const pdfExt = ['.pdf'];
        const audioExt = ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'];
        const videoExt = ['.mp4', '.webm', '.mov', '.mkv', '.avi'];
        return [...imageExt, ...pdfExt, ...audioExt, ...videoExt].some(ext => lowerPath.endsWith(ext));
    }

    function renderInlinePreview(path) {
        const encodedPath = encodeURIComponent(path);
        const contentUrl = `/api/server-files/content?path=${encodedPath}`;
        const lowerPath = (path || '').toLowerCase();
        const isImage = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'].some(ext => lowerPath.endsWith(ext));
        const isPdf = lowerPath.endsWith('.pdf');
        const isAudio = ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'].some(ext => lowerPath.endsWith(ext));
        const isVideo = ['.mp4', '.webm', '.mov', '.mkv', '.avi'].some(ext => lowerPath.endsWith(ext));

        let viewer = `<div class="file-explorer-error">Preview not available</div>`;
        if (isImage) {
            viewer = `<img src="${contentUrl}" alt="${escapeHtml(getFileNameFromPath(path))}" style="max-width:100%;max-height:70vh;object-fit:contain;" />`;
        } else if (isPdf) {
            viewer = `<iframe src="${contentUrl}" style="width:100%;height:70vh;border:0;" title="${escapeHtml(getFileNameFromPath(path))}"></iframe>`;
        } else if (isAudio) {
            viewer = `<audio controls src="${contentUrl}" style="width:100%;"></audio>`;
        } else if (isVideo) {
            viewer = `<video controls src="${contentUrl}" style="max-width:100%;max-height:70vh;"></video>`;
        }

        fileViewerContent.innerHTML = `
            <div class="file-viewer-info">
                <span>${escapeHtml(path)}</span>
            </div>
            <div class="file-viewer-media">${viewer}</div>
        `;
    }

    async function showFileViewer(path) {
        fileViewerPanel.classList.add('show');
        fileViewerContent.innerHTML = '<div class="loading">Loading...</div>';
        fileViewerTitleText.textContent = getFileNameFromPath(path);

        try {
            if (shouldInlinePreview(path)) {
                renderInlinePreview(path);
                return;
            }

            const response = await fetch(`/api/server-files/read?path=${encodeURIComponent(path)}`);
            const data = await response.json();

            if (data.error) {
                if ((data.error || '').includes('/api/server-files/content')) {
                    renderInlinePreview(path);
                    return;
                }
                fileViewerContent.innerHTML = `<div class="file-explorer-error">${escapeHtml(data.error)}</div>`;
                return;
            }

            fileViewerTitleText.textContent = data.name;

            // Render file content with syntax highlighting
            const escapedContent = escapeHtml(data.content || '');
            const language = data.language || 'text';

            fileViewerContent.innerHTML = `
                <div class="file-viewer-info">
                    <span>${escapeHtml(data.path)}</span>
                    <span>${formatBytes(data.size || 0)}</span>
                </div>
                <pre class="file-viewer-code"><code class="language-${language}">${escapedContent}</code></pre>
            `;

            // Apply syntax highlighting
            if (typeof hljs !== 'undefined') {
                fileViewerContent.querySelectorAll('pre code').forEach((block) => {
                    hljs.highlightElement(block);
                });
            }

        } catch (error) {
            console.error('Error reading file:', error);
            fileViewerContent.innerHTML = `<div class="file-explorer-error">${escapeHtml(error.message)}</div>`;
        }
    }

    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    if (closeFileViewer) {
        closeFileViewer.addEventListener('click', function() {
            fileViewerPanel.classList.remove('show');
        });
    }

    // ========== Settings ==========

    const settingsPanel = document.getElementById('settingsPanel');
    const closeSettings = document.getElementById('closeSettings');
    const saveSettingsBtn = document.getElementById('saveSettings');

    // Settings form elements
    const llmProvider = document.getElementById('llmProvider');
    const llmModel = document.getElementById('llmModel');
    const llmApiKey = document.getElementById('llmApiKey');
    const jiraEnabled = document.getElementById('jiraEnabled');
    const jiraUrl = document.getElementById('jiraUrl');
    const jiraUsername = document.getElementById('jiraUsername');
    const jiraApiToken = document.getElementById('jiraApiToken');
    const confluenceEnabled = document.getElementById('confluenceEnabled');
    const confluenceUrl = document.getElementById('confluenceUrl');
    const confluenceUsername = document.getElementById('confluenceUsername');
    const confluenceApiToken = document.getElementById('confluenceApiToken');
    const githubEnabled = document.getElementById('githubEnabled');
    const githubToken = document.getElementById('githubToken');
    const githubBaseUrl = document.getElementById('githubBaseUrl');
    const gitName = document.getElementById('gitName');
    const gitEmail = document.getElementById('gitEmail');


    // Runtime v2 native mode only supports GitHub Copilot.
    const providerModels = {
        github_copilot: [
            { value: 'gpt-4o', label: 'GPT-4o' },
            { value: 'gpt-5.4-mini', label: 'GPT-5.4 mini' },
            { value: 'gpt-5-mini', label: 'GPT-5 mini' },
            { value: 'gpt-5', label: 'GPT-5' },
            { value: 'gpt-5.1-codex', label: 'GPT-5.1-Codex' },
            { value: 'gpt-5.1-codex-max', label: 'GPT-5.1-Codex-Max' },
            { value: 'gpt-5.2', label: 'GPT-5.2' },
            { value: 'gpt-5.3-codex-max', label: 'GPT-5.3-Codex-Max' },
            { value: 'gpt-5.4', label: 'GPT-5.4' },
            { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
        ],
    };

    // Update model dropdown based on selected provider
    function updateModelDropdown(provider, currentModel = '') {
        const models = providerModels[provider] || [];
        llmModel.innerHTML = models.map(m =>
            `<option value="${m.value}" ${m.value === currentModel ? 'selected' : ''}>${m.label}</option>`
        ).join('');
    }

    // Listen for provider changes
    if (llmProvider) {
        llmProvider.addEventListener('change', function() {
            updateModelDropdown(this.value);
            // Show/hide GitHub Copilot auth button
            const copilotSection = document.getElementById('copilotAuthSection');
            const copilotStatus = document.getElementById('copilotAuthStatus');
            if (this.value === 'github_copilot') {
                if (copilotSection) copilotSection.style.display = 'block';
                if (copilotStatus) copilotStatus.style.display = 'none';
            } else {
                if (copilotSection) copilotSection.style.display = 'none';
                if (copilotStatus) copilotStatus.style.display = 'none';
            }
        });
    }

    // GitHub Copilot Authorization
    const copilotAuthBtn = document.getElementById('copilotAuthBtn');
    const copilotAuthStatus = document.getElementById('copilotAuthStatus');
    const copilotSsoUrl = document.getElementById('copilotSsoUrl');
    const copilotDeviceUrl = document.getElementById('copilotDeviceUrl');
    const copilotUserCode = document.getElementById('copilotUserCode');
    const copilotCopyCode = document.getElementById('copilotCopyCode');
    const copilotTimer = document.getElementById('copilotTimer');
    const copilotStatusText = document.getElementById('copilotStatusText');
    const copilotProgress = document.getElementById('copilotProgress');

    let copilotAuthInterval = null;
    let copilotTimerInterval = null;
    let copilotCurrentTimer = 30;

    async function startCopilotAuth() {
        if (!copilotAuthStatus || !copilotSsoUrl || !copilotDeviceUrl || !copilotUserCode) return;

        try {
            const response = await fetch('/api/copilot/auth/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();

            if (data.error) {
                alert('Failed to start authorization: ' + data.error);
                return;
            }

            // Store auth data
            copilotAuthStatus.dataset.authId = data.auth_id;
            copilotAuthStatus.dataset.deviceCode = data.device_code;

            // Update UI
            copilotAuthStatus.style.display = 'block';
            copilotAuthStatus.classList.remove('success');
            copilotSsoUrl.href = data.verification_url;
            copilotDeviceUrl.href = data.verification_complete_url;
            copilotUserCode.textContent = data.user_code;
            copilotTimer.textContent = data.expires_in + 's';
            copilotTimer.className = 'copilot-timer';
            copilotStatusText.textContent = 'Waiting for authorization...';
            copilotProgress.classList.add('active');

            // Start countdown timer
            copilotCurrentTimer = data.expires_in || 30;
            if (copilotTimerInterval) clearInterval(copilotTimerInterval);
            copilotTimerInterval = setInterval(() => {
                copilotCurrentTimer--;
                if (copilotTimer.textContent !== copilotCurrentTimer + 's') {
                    copilotTimer.textContent = copilotCurrentTimer + 's';
                }
                if (copilotCurrentTimer <= 10) {
                    copilotTimer.classList.add('warning');
                }
                if (copilotCurrentTimer <= 0) {
                    copilotTimer.classList.add('error');
                    stopCopilotAuth();
                    copilotStatusText.textContent = 'Authorization timed out. Please try again.';
                    copilotProgress.classList.remove('active');
                }
            }, 1000);

            // Start polling for authorization status
            copilotAuthInterval = setInterval(async () => {
                try {
                    const checkResponse = await fetch('/api/copilot/auth/check', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            auth_id: data.auth_id,
                            device_code: data.device_code
                        })
                    });
                    const checkData = await checkResponse.json();

                    if (checkData.status === 'authorized') {
                        stopCopilotAuth();
                        copilotAuthStatus.classList.add('success');
                        copilotStatusText.textContent = 'Authorization successful!';
                        copilotProgress.classList.remove('active');
                        // Fill in the API key
                        if (llmApiKey) {
                            llmApiKey.value = checkData.token;
                        }
                        // Hide status after 3 seconds
                        setTimeout(() => {
                            copilotAuthStatus.style.display = 'none';
                        }, 3000);
                    } else if (checkData.status === 'expired' || checkData.status === 'declined') {
                        stopCopilotAuth();
                        copilotStatusText.textContent = checkData.message || 'Authorization failed. Please try again.';
                        copilotProgress.classList.remove('active');
                    }
                } catch (e) {
                    console.error('Error checking auth status:', e);
                }
            }, (data.interval || 5) * 1000);

        } catch (error) {
            console.error('Error starting Copilot auth:', error);
            alert('Failed to start authorization: ' + error.message);
        }
    }

    function stopCopilotAuth() {
        if (copilotAuthInterval) {
            clearInterval(copilotAuthInterval);
            copilotAuthInterval = null;
        }
        if (copilotTimerInterval) {
            clearInterval(copilotTimerInterval);
            copilotTimerInterval = null;
        }
    }

    if (copilotAuthBtn) {
        copilotAuthBtn.addEventListener('click', startCopilotAuth);
    }

    if (copilotCopyCode) {
        copilotCopyCode.addEventListener('click', function() {
            const code = copilotUserCode.textContent;
            navigator.clipboard.writeText(code).then(() => {
                this.textContent = 'Copied!';
                this.classList.add('copied');
                setTimeout(() => {
                    this.textContent = 'Copy';
                    this.classList.remove('copied');
                }, 2000);
            });
        });
    }

    // Password toggle buttons
    document.querySelectorAll('.toggle-password').forEach(button => {
        button.addEventListener('click', function() {
            const targetId = this.getAttribute('data-target');
            const input = document.getElementById(targetId);
            if (input) {
                const isPassword = input.type === 'password';
                input.type = isPassword ? 'text' : 'password';
                this.classList.toggle('showing', isPassword);
            }
        });
    });

    // ========== Multi-instance Support ==========
    let jiraInstancesData = [];
    let confluenceInstancesData = [];

    // Expose functions globally for onclick handlers
    window.addJiraInstance = function() {
        jiraInstancesData = collectJiraInstances();
        jiraInstancesData.push({ name: '', url: '', username: '', password: '', token: '', project: '' });
        renderJiraInstances(jiraInstancesData);
    };

    window.removeJiraInstance = function(index) {
        jiraInstancesData = collectJiraInstances();
        jiraInstancesData.splice(index, 1);
        renderJiraInstances(jiraInstancesData);
    };

    window.addConfluenceInstance = function() {
        confluenceInstancesData = collectConfluenceInstances();
        confluenceInstancesData.push({ name: '', url: '', username: '', password: '', token: '', space: '' });
        renderConfluenceInstances(confluenceInstancesData);
    };

    window.removeConfluenceInstance = function(index) {
        confluenceInstancesData = collectConfluenceInstances();
        confluenceInstancesData.splice(index, 1);
        renderConfluenceInstances(confluenceInstancesData);
    };

    function renderJiraInstances(instances) {
        jiraInstancesData = instances || [];
        const container = document.getElementById('jiraInstancesContainer');
        if (!container) return;

        container.innerHTML = jiraInstancesData.map((inst, idx) => `
            <div class="instance-item" data-index="${idx}">
                <div class="instance-header">
                    <span class="instance-name">${inst.name || 'Instance ' + (idx + 1)}</span>
                    <button type="button" class="btn-remove-instance" onclick="removeJiraInstance(${idx})">Remove</button>
                </div>
                <div class="instance-fields">
                    <input type="text" placeholder="Name" value="${inst.name || ''}" data-field="name">
                    <input type="text" placeholder="URL" value="${inst.url || ''}" data-field="url">
                    <input type="text" placeholder="Username" value="${inst.username || ''}" data-field="username">
                    <input type="password" placeholder="Password (Basic Auth)" value="${inst.password || ''}" data-field="password">
                    <input type="password" placeholder="Token (Bearer Auth)" value="${inst.token || ''}" data-field="token">
                    <input type="text" placeholder="Project" value="${inst.project || ''}" data-field="project">
                </div>
            </div>
        `).join('') + `
            <button type="button" class="btn-add-instance" onclick="addJiraInstance()">+ Add Jira Instance</button>
        `;
    }

    function collectJiraInstances() {
        const container = document.getElementById('jiraInstancesContainer');
        if (!container) return [];

        const items = container.querySelectorAll('.instance-item');
        const instances = [];
        items.forEach(item => {
            const fields = {};
            item.querySelectorAll('input').forEach(input => {
                fields[input.dataset.field] = input.value;
            });
            if (fields.name || fields.url) {
                instances.push(fields);
            }
        });
        return instances;
    }

    function renderConfluenceInstances(instances) {
        confluenceInstancesData = instances || [];
        const container = document.getElementById('confluenceInstancesContainer');
        if (!container) return;

        container.innerHTML = confluenceInstancesData.map((inst, idx) => `
            <div class="instance-item" data-index="${idx}">
                <div class="instance-header">
                    <span class="instance-name">${inst.name || 'Instance ' + (idx + 1)}</span>
                    <button type="button" class="btn-remove-instance" onclick="removeConfluenceInstance(${idx})">Remove</button>
                </div>
                <div class="instance-fields">
                    <input type="text" placeholder="Name" value="${inst.name || ''}" data-field="name">
                    <input type="text" placeholder="URL" value="${inst.url || ''}" data-field="url">
                    <input type="text" placeholder="Username" value="${inst.username || ''}" data-field="username">
                    <input type="password" placeholder="Password (Basic Auth)" value="${inst.password || ''}" data-field="password">
                    <input type="password" placeholder="Token (Bearer Auth)" value="${inst.token || ''}" data-field="token">
                    <input type="text" placeholder="Space" value="${inst.space || ''}" data-field="space">
                </div>
            </div>
        `).join('') + `
            <button type="button" class="btn-add-instance" onclick="addConfluenceInstance()">+ Add Confluence Instance</button>
        `;
    }

    function collectConfluenceInstances() {
        const container = document.getElementById('confluenceInstancesContainer');
        if (!container) return [];

        const items = container.querySelectorAll('.instance-item');
        const instances = [];
        items.forEach(item => {
            const fields = {};
            item.querySelectorAll('input').forEach(input => {
                fields[input.dataset.field] = input.value;
            });
            if (fields.name || fields.url) {
                instances.push(fields);
            }
        });
        return instances;
    }

    async function showSettings() {
        settingsPanel.classList.add('show');

        // Hide copilot auth status initially
        const copilotAuthStatus = document.getElementById('copilotAuthStatus');
        const copilotAuthSection = document.getElementById('copilotAuthSection');
        if (copilotAuthStatus) copilotAuthStatus.style.display = 'none';
        if (copilotAuthSection) copilotAuthSection.style.display = 'none';

        // Load config from server
        try {
            const response = await fetch('/api/config');
            const data = await response.json();

            if (data.config) {
                const config = data.config;

                // LLM settings
                if (config.llm) {
                    const provider = providerModels[config.llm.provider] ? config.llm.provider : 'github_copilot';
                    const model = config.llm.model || 'gpt-5.4-mini';
                    llmProvider.value = provider;
                    // Update model dropdown with current provider and model
                    updateModelDropdown(provider, model);
                    llmApiKey.value = config.llm.api_key || '';

                    // Show/hide GitHub Copilot auth button based on provider
                    if (copilotAuthSection && provider === 'github_copilot') {
                        copilotAuthSection.style.display = 'block';
                    }
                } else {
                    // Default to github_copilot with gpt-4o
                    llmProvider.value = 'github_copilot';
                    updateModelDropdown('github_copilot', 'gpt-5.4-mini');
                    if (copilotAuthSection) copilotAuthSection.style.display = 'block';
                }

                // Jira settings - support multiple instances
                if (config.jira) {
                    jiraEnabled.checked = config.jira.enabled || false;
                    // Support both old format (url, username) and new format (instances)
                    let jiraInstances = config.jira.instances;
                    if (!jiraInstances || jiraInstances.length === 0) {
                        // Convert old format to new format for backward compatibility
                        if (config.jira.url) {
                            jiraInstances = [{
                                name: 'Default',
                                url: config.jira.url || '',
                                username: config.jira.username || '',
                                password: config.jira.password || '',
                                token: config.jira.token || '',
                                project: config.jira.project || ''
                            }];
                        } else {
                            jiraInstances = [];
                        }
                    }
                    renderJiraInstances(jiraInstances);
                }

                // Confluence settings - support multiple instances
                if (config.confluence) {
                    confluenceEnabled.checked = config.confluence.enabled || false;
                    // Support both old format (url, username) and new format (instances)
                    let confluenceInstances = config.confluence.instances;
                    if (!confluenceInstances || confluenceInstances.length === 0) {
                        // Convert old format to new format for backward compatibility
                        if (config.confluence.url) {
                            confluenceInstances = [{
                                name: 'Default',
                                url: config.confluence.url || '',
                                username: config.confluence.username || '',
                                password: config.confluence.password || '',
                                token: config.confluence.token || '',
                                space: config.confluence.space || ''
                            }];
                        } else {
                            confluenceInstances = [];
                        }
                    }
                    renderConfluenceInstances(confluenceInstances);
                }

                // GitHub settings
                if (config.github) {
                    githubEnabled.checked = config.github.enabled || false;
                    githubToken.value = config.github.api_token || '';
                    githubBaseUrl.value = config.github.base_url || '';
                }

                // Git settings
                if (config.git && config.git.user) {
                    gitName.value = config.git.user.name || '';
                    gitEmail.value = config.git.user.email || '';
                }

                // Debug settings
                if (config.debug) {
                    debugEnabled.checked = config.debug.enabled || false;
                }
            }
        } catch (error) {
            console.error('Error loading config:', error);
        }
    }

    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener('click', async function() {
            const provider = 'github_copilot';
            const apiBase = 'https://api.githubcopilot.com';

            const config = {
                llm: {
                    provider: provider,
                    model: llmModel.value,
                    api_key: llmApiKey.value,
                    api_base: apiBase,
                },
                jira: {
                    enabled: jiraEnabled.checked,
                    instances: collectJiraInstances(),
                },
                confluence: {
                    enabled: confluenceEnabled.checked,
                    instances: collectConfluenceInstances(),
                },
                github: {
                    enabled: githubEnabled.checked,
                    api_token: githubToken.value,
                    base_url: githubBaseUrl.value,
                },
                git: {
                    user: {
                        name: gitName.value,
                        email: gitEmail.value,
                    },
                },
                debug: {
                    enabled: debugEnabled.checked,
                },
            };

            try {
                const response = await fetch('/api/config/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config),
                });

                const data = await response.json();
                if (data.success) {
                    settingsPanel.classList.remove('show');
                } else {
                    alert('Error saving configuration: ' + (data.error || 'Unknown error'));
                }
            } catch (error) {
                alert('Error saving configuration: ' + error.message);
            }
        });
    }

    if (closeSettings) {
        closeSettings.addEventListener('click', function() {
            settingsPanel.classList.remove('show');
        });
    }

    // Debug: Expose session functions globally for testing
    window.webchatDebugSession = {
        get: () => localStorage.getItem('efp-session-id'),
        set: (id) => {
            localStorage.setItem('efp-session-id', id);
            return localStorage.getItem('efp-session-id');
        },
        clear: () => localStorage.removeItem('efp-session-id'),
        log: () => console.log('[WebChat] session_id:', localStorage.getItem('efp-session-id'))
    };

    // ========== Real-time Agent Events (WebSocket) ==========

    let eventWs = null;
    let currentAgentEventDiv = null;
    let currentEvents = [];  // Store events for current request
    let pendingEventsSnapshot = null;  // Snapshot saved when complete event fires
    let pendingAssistantMessage = null;  // Track the assistant message waiting for thinking process

    /**
     * Reset events for new request
     */
    function resetEvents() {
        currentEvents = [];
        pendingEventsSnapshot = null;
        pendingAssistantMessage = null;
        console.log('[Events] Reset events for new request');
    }

    /**
     * Mark a message element as pending thinking process
     */
    function markPendingAssistant(element) {
        pendingAssistantMessage = element;
        console.log('[Events] Marked pending assistant message');

        // If we have a pending snapshot, show the thinking process now
        if (pendingEventsSnapshot && pendingEventsSnapshot.length > 0) {
            console.log('[Events] Showing pending thinking process with', pendingEventsSnapshot.length, 'events');
            showThinkingProcessButtonWithEvents(pendingEventsSnapshot);
            pendingEventsSnapshot = null;
        }
    }

    /**
     * Connect to WebSocket for real-time agent events
     */
    let wsConnected = false;

    function connectEventWebSocket() {
        // Prevent multiple connections
        if (wsConnected && eventWs && eventWs.readyState === WebSocket.OPEN) {
            console.log('[WebSocket] Already connected');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/events`;

        console.log('[WebSocket] Connecting to:', wsUrl);

        try {
            eventWs = new WebSocket(wsUrl);

            eventWs.onopen = function() {
                wsConnected = true;
                console.log('[WebSocket] Connected to event stream');
            };

            eventWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    handleAgentEvent(data);
                } catch (e) {
                    console.error('[WebSocket] Error parsing event:', e);
                }
            };

            eventWs.onclose = function() {
                wsConnected = false;
                console.log('[WebSocket] Disconnected from event stream');
                // Reconnect after 3 seconds
                setTimeout(connectEventWebSocket, 3000);
            };

            eventWs.onerror = function(error) {
                console.error('[WebSocket] Error:', error);
            };

        } catch (error) {
            console.error('[WebSocket] Failed to connect:', error);
        }
    }

    /**
     * Handle incoming agent events
     */
    function handleAgentEvent(data) {
        const type = data.type;
        const eventData = data.data || {};
        const timestamp = data.ts || Date.now() / 1000;

        console.log('[Agent Event]', type, eventData);

        // Skip non-relevant events
        if (type === 'connected' || type === 'pong') return;

        // Store event
        const eventRecord = {
            type: type,
            data: eventData,
            timestamp: timestamp,
            display: getEventDisplay(type, eventData)
        };

        currentEvents.push(eventRecord);
        console.log('[Events] Added event, total:', currentEvents.length, 'type:', type);

        switch (type) {
            case 'skill_matched':
                showAgentEvent('skill-matched', `🎯 Skill: ${eventData.skill || 'Unknown'}`);
                break;

            case 'iteration_start':
                showAgentEvent('iteration-start', `🔄 Iteration ${eventData.iteration || 1}${eventData.total ? '/' + eventData.total : ''}`);
                break;

            case 'llm_thinking':
                showAgentEvent('llm-thinking', `🤔 ${eventData.message || 'LLM is thinking...'}`);
                break;

            case 'tool_call':
                // Only show tool calls when debug is enabled
                if (isDebugEnabled()) {
                    showAgentEvent('tool-call', `🔧 Calling: ${eventData.tool || 'Unknown tool'}`);
                }
                break;

            case 'tool_result':
                // Only show tool results when debug is enabled
                if (isDebugEnabled()) {
                    // Show detailed result or error
                    const success = eventData.success;
                    const tool = eventData.tool || 'Unknown tool';
                    const result = eventData.result;

                    if (success) {
                        // Success: show brief result preview
                        const preview = result ? (result.length > 100 ? result.substring(0, 100) + '...' : result) : '(no result)';
                        showAgentEvent('tool-result', `✅ ${tool}\nResult: ${preview}`);
                    } else {
                        // Error: result already contains "Error:" prefix from __str__, don't duplicate
                        const errorMsg = result || 'Unknown error (no details)';
                        showAgentEvent('tool-result', `❌ ${tool}\n${errorMsg}`);
                    }
                }
                break;

            case 'confirmation':
                showAgentEvent('confirmation', `⚠️ ${eventData.message || 'Confirmation required'}`);
                break;

            case 'iteration_end':
                hideAgentEvent();
                break;

            case 'complete':
                console.log('[Events] Complete, total events:', currentEvents.length);
                hideAgentEvent();

                // Save snapshot and wait for assistant message
                if (currentEvents.length > 0) {
                    pendingEventsSnapshot = currentEvents.slice();
                    console.log('[Events] Saved pending snapshot with', pendingEventsSnapshot.length, 'events');

                    // If assistant message already exists, show now
                    if (pendingAssistantMessage) {
                        showThinkingProcessButtonWithEvents(pendingEventsSnapshot);
                        pendingEventsSnapshot = null;
                    } else {
                        console.log('[Events] Waiting for assistant message...');
                    }
                }
                break;

            default:
                showAgentEvent('agent-event', `${type}: ${JSON.stringify(eventData).substring(0, 50)}...`);
                setTimeout(hideAgentEvent, 2000);
        }
    }

    /**
     * Get display info for event type
     */
    function getEventDisplay(type, data) {
        const eventIcons = {
            'skill_matched': '🎯',
            'iteration_start': '🔄',
            'llm_thinking': '🤔',
            'tool_call': '🔧',
            'tool_result': '✅',
            'confirmation': '⚠️',
            'iteration_end': '📍',
            'complete': '🎉'
        };

        const eventNames = {
            'skill_matched': 'Skill Matched',
            'iteration_start': 'Iteration Start',
            'llm_thinking': 'LLM Thinking',
            'tool_call': 'Tool Call',
            'tool_result': 'Tool Result',
            'confirmation': 'Confirmation',
            'iteration_end': 'Iteration End',
            'complete': 'Complete'
        };

        let message = '';
        switch (type) {
            case 'skill_matched':
                message = `Skill: ${data.skill || 'Unknown'}`;
                break;
            case 'iteration_start':
                message = `Iteration ${data.iteration || 1}${data.total ? '/' + data.total : ''}`;
                break;
            case 'llm_thinking':
                message = data.message || 'LLM is thinking...';
                break;
            case 'tool_call':
                // Only show in debug mode
                if (isDebugEnabled()) {
                    const argsStr = data.args ? JSON.stringify(data.args, null, 2) : '';
                    message = `🔧 ${data.tool || 'Unknown tool'}\n📝 Args: ${argsStr || 'none'}`;
                } else {
                    message = '';
                }
                break;
            case 'tool_result':
                // Only show in debug mode
                if (isDebugEnabled()) {
                    const statusIcon = data.success ? '✅' : '❌';
                    message = `${statusIcon} ${data.tool || 'Tool'} Result:\n${data.result || '(no result)'}`;
                } else {
                    message = '';
                }
                break;
            case 'confirmation':
                message = data.message || 'Confirmation required';
                break;
            case 'complete':
                message = 'Execution complete';
                break;
            default:
                message = `${type}: ${JSON.stringify(data).substring(0, 50)}`;
        }

        return {
            icon: eventIcons[type] || '📌',
            name: eventNames[type] || type,
            message: message,
            details: data
        };
    }

    /**
     * Show agent event in chat
     */
    function showAgentEvent(eventClass, message) {
        // Remove welcome message if present
        const welcome = messagesContainer.querySelector('.welcome-message');
        if (welcome) {
            welcome.remove();
        }

        // Remove any existing agent event
        hideAgentEvent();

        currentAgentEventDiv = document.createElement('div');
        currentAgentEventDiv.className = `message agent-event ${eventClass}`;
        currentAgentEventDiv.innerHTML = `
            <div class="avatar" aria-hidden="true">⚡</div>
            <div>
                <div class="agent-event-content">${escapeHtml(message)}</div>
                <div class="agent-event-label">Agent Activity</div>
            </div>
        `;

        messagesContainer.appendChild(currentAgentEventDiv);
        scrollToBottom();
    }

    /**
     * Hide current agent event
     */
    function hideAgentEvent() {
        if (currentAgentEventDiv) {
            currentAgentEventDiv.remove();
            currentAgentEventDiv = null;
        }
    }

    /**
     * Show "View Thinking Process" button with specific events snapshot
     */
    function showThinkingProcessButtonWithEvents(eventsSnapshot) {
        if (!eventsSnapshot || eventsSnapshot.length === 0) {
            console.log('[Events] No events to show');
            return;
        }

        console.log('[Events] Showing thinking process with', eventsSnapshot.length, 'events');

        // Use the pending assistant message (most reliable)
        let lastAssistantMessage = pendingAssistantMessage;

        // Fallback: find the most recent assistant message without has-thinking
        if (!lastAssistantMessage) {
            const assistantMessages = messagesContainer.querySelectorAll('.message.assistant');
            for (let i = assistantMessages.length - 1; i >= 0; i--) {
                if (!assistantMessages[i].classList.contains('has-thinking')) {
                    lastAssistantMessage = assistantMessages[i];
                    break;
                }
            }
        }

        if (!lastAssistantMessage) {
            console.log('[Events] No assistant message found to attach thinking process');
            return;
        }

        lastAssistantMessage.classList.add('has-thinking');
        pendingAssistantMessage = null;  // Clear the pending reference

        // Create toggle button
        const toggleBtn = document.createElement('button');
        toggleBtn.className = 'thinking-process-toggle';
        const eventCount = eventsSnapshot.length;
        toggleBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <path d="M12 16v-4"/>
                <path d="M12 8h.01"/>
            </svg>
            <span>View Thinking Process (${eventCount} steps)</span>
            <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"/>
            </svg>
        `;

        // Create thinking process content
        const processContent = document.createElement('div');
        processContent.className = 'thinking-process-content';
        processContent.style.display = 'none';

        // Build event timeline using the snapshot
        let timelineHtml = '<div class="thinking-timeline">';
        eventsSnapshot.forEach((event, index) => {
            const display = event.display;
            const isLast = index === eventsSnapshot.length - 1;
            timelineHtml += `
                <div class="thinking-item ${isLast ? 'last' : ''}">
                    <div class="thinking-icon">${display.icon}</div>
                    <div class="thinking-details">
                        <div class="thinking-name">${display.name}</div>
                        <div class="thinking-message">${escapeHtml(display.message)}</div>
                    </div>
                </div>
            `;
        });
        timelineHtml += '</div>';

        processContent.innerHTML = timelineHtml;

        // Insert after the message bubble
        const bubble = lastAssistantMessage.querySelector('.message-bubble');
        if (bubble) {
            bubble.appendChild(toggleBtn);
            bubble.appendChild(processContent);
        }

        // Toggle functionality
        let expanded = false;
        toggleBtn.addEventListener('click', function(e) {
            e.preventDefault();
            expanded = !expanded;

            if (expanded) {
                processContent.style.display = 'block';
                toggleBtn.classList.add('expanded');
                toggleBtn.innerHTML = `
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M12 16v-4"/>
                        <path d="M12 8h.01"/>
                    </svg>
                    <span>Hide Thinking Process</span>
                    <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="18 15 12 9 6 15"/>
                    </svg>
                `;
            } else {
                processContent.style.display = 'none';
                toggleBtn.classList.remove('expanded');
                toggleBtn.innerHTML = `
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M12 16v-4"/>
                        <path d="M12 8h.01"/>
                    </svg>
                    <span>View Thinking Process (${eventCount} steps)</span>
                    <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                `;
            }

            scrollToBottom();
        });

        // Clear events AFTER this function completes
        if (!window._eventsCleared) {
            window._eventsCleared = true;
            setTimeout(() => {
                currentEvents = [];
                window._eventsCleared = false;
                console.log('[Events] Events cleared');
            }, 100);
        }
    }

    /**
     * Show thinking process button (legacy - uses currentEvents)
     */
    function showThinkingProcessButton() {
        showThinkingProcessButtonWithEvents(currentEvents.slice());
    }

    // Connect to WebSocket on page load
    connectEventWebSocket();

    // File explorer toggle buttons
    const toggleServerFiles = document.getElementById('toggleServerFiles');
    const fileExplorerTitle = document.getElementById('fileExplorerTitle');

    if (toggleServerFiles) {
        toggleServerFiles.addEventListener('click', function() {
            toggleServerFiles.classList.add('active');
            if (fileExplorerTitle) fileExplorerTitle.textContent = 'Server Files';
            showFileExplorer(serverFilesCurrentPath || '');
        });
    }

})();
