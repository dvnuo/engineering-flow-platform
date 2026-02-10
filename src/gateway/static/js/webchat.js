// WebChat JavaScript

(function() {
    'use strict';
    
    // DOM Elements
    const messagesContainer = document.getElementById('messages');
    const messageInput = document.getElementById('messageInput');
    const sendButton = document.getElementById('sendButton');
    const typingIndicator = document.getElementById('typing');
    const statusSpan = document.getElementById('status');
    const tokenCountSpan = document.getElementById('tokenCount');
    const costDisplaySpan = document.getElementById('costDisplay');
    const statsButton = document.getElementById('statsButton');
    const statsModal = document.getElementById('statsModal');
    const closeStatsButton = document.getElementById('closeStats');
    const statsContent = document.getElementById('statsContent');
    const skillSelector = document.getElementById('skillSelector');
    const skillDropdown = document.getElementById('skillDropdown');
    const skillList = document.getElementById('skillList');
    const themeToggle = document.getElementById('themeToggle');
    
    // State
    let isLoading = false;
    let totalTokens = 0;
    let totalCost = 0;
    let skills = [];
    let selectedSkillIndex = -1;
    let skillsLoaded = false;
    let currentSessionId = null;
    
    // ========== Theme Management ==========
    
    const THEME_KEY = 'efp-theme';
    
    /**
     * Get saved theme preference
     */
    function getTheme() {
        return localStorage.getItem(THEME_KEY) || 'light';
    }
    
    /**
     * Set theme and save preference
     */
    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
    }
    
    /**
    /**
     * Toggle between light and dark theme
     */
    function toggleTheme() {
        const currentTheme = getTheme();
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        setTheme(newTheme);
    }
    
    /**
     * Initialize theme on page load
     */
    function initTheme() {
        const theme = getTheme();
        setTheme(theme);
    }
    
    // Initialize theme
    initTheme();
    
    // Theme toggle event listener
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }
    /**
     * Toggle between light and dark theme
     */
    function toggleTheme() {
        const currentTheme = getTheme();
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        setTheme(newTheme);
    }
    
    /**
     * Initialize theme on page load
     */
    function initTheme() {
        const theme = getTheme();
        setTheme(theme);
    }
    
    // Initialize theme
    initTheme();
    
    // Theme toggle event listener
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }
    
    // ========== Sidebar Management ==========
    
    const sidebar = document.getElementById('sidebar');
    const layout = document.querySelector('.layout');
    const toggleSidebarBtn = document.getElementById('toggleSidebar');
    
    /**
     * Toggle sidebar on mobile
     */
    function toggleSidebar() {
        if (layout && window.innerWidth <= 768) {
            layout.classList.toggle('sidebar-open');
            sidebar.classList.toggle('open');
        }
    }
    
    // Sidebar toggle button
    if (toggleSidebarBtn) {
        toggleSidebarBtn.addEventListener('click', toggleSidebar);
    }
    
    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function(e) {
        if (window.innerWidth <= 768 && 
            layout && layout.classList.contains('sidebar-open') &&
            sidebar && !sidebar.contains(e.target) &&
            toggleSidebarBtn && !toggleSidebarBtn.contains(e.target)) {
            layout.classList.remove('sidebar-open');
            sidebar.classList.remove('open');
        }
    });
    
    // Stats Modal
    statsButton.addEventListener('click', showStats);
    closeStatsButton.addEventListener('click', hideStats);
    statsModal.addEventListener('click', function(e) {
        if (e.target === statsModal) hideStats();
    });
    
    /**
     * Show usage statistics modal
     */
    async function showStats() {
        statsModal.classList.add('show');
        statsContent.innerHTML = '<div class="loading">Loading...</div>';
        
        try {
            const response = await fetch('/api/usage?days=30');
            const data = await response.json();
            
            if (data.error) {
                statsContent.innerHTML = '<div class="no-data">Error loading stats</div>';
                return;
            }
            
            let html = '';
            
            // Global stats
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
            
            // By Provider
            const byProvider = data.by_provider || {};
            if (Object.keys(byProvider).length > 0) {
                html += '<div class="stats-section"><h3>By Provider</h3><div class="stats-grid">';
                for (const [provider, stats] of Object.entries(byProvider)) {
                    html += `
                        <div class="stat-item">
                            <div class="stat-label">${provider}</div>
                            <div class="stat-value cost">$${(stats.cost || 0).toFixed(4)}</div>
                            <div class="stat-model">${stats.requests || 0} requests</div>
                        </div>
                    `;
                }
                html += '</div></div>';
            }
            
            // By Model
            const byModel = data.by_model || {};
            if (Object.keys(byModel).length > 0) {
                html += '<div class="stats-section"><h3>By Model</h3>';
                for (const [model, stats] of Object.entries(byModel)) {
                    html += `
                        <div class="stat-item">
                            <div class="stat-label">${model}</div>
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
    
    /**
     * Hide usage statistics modal
     */
    function hideStats() {
        statsModal.classList.remove('show');
    }
    
    // ========== Skill Selector ==========
    
    /**
     * Load skills from API
     */
    async function loadSkills() {
        if (skillsLoaded) return;
        
        try {
            const response = await fetch('/api/skills');
            const data = await response.json();
            skills = data.skills || [];
            skillsLoaded = true;
        } catch (error) {
            console.error('Failed to load skills:', error);
            skills = [];
        }
    }
    
    /**
     * Show skill selector dropdown
     */
    function showSkillSelector() {
        if (!skills.length) {
            loadSkills().then(() => {
                if (skills.length) {
                    renderSkillList();
                    skillSelector.classList.add('active');
                }
            });
            return;
        }
        renderSkillList();
        skillSelector.classList.add('active');
    }
    
    /**
     * Hide skill selector dropdown
     */
    function hideSkillSelector() {
        skillSelector.classList.remove('active');
        selectedSkillIndex = -1;
    }
    
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
    
    // Auto-resize textarea
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
    
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
        
        // Show skill selector on /
        if (e.key === '/' && messageInput.selectionStart === 0) {
            e.preventDefault();
            showSkillSelector();
            return;
        }
        
        // Close skill selector when deleting the /
        if (e.key === 'Backspace' || e.key === 'Delete') {
            if (messageInput.value === '/' && skillSelector.classList.contains('active')) {
                hideSkillSelector();
            }
        }
        
        // Send message
        if (e.key === 'Enter' && !e.shiftKey && !skillSelector.classList.contains('active')) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    sendButton.addEventListener('click', sendMessage);
    
    /**
     * Add a message to the chat
     * @param {string} role - 'user', 'assistant', or 'error'
     * @param {string} content - Message content
     * @param {string} [timestamp] - Optional timestamp
     */
    function addMessage(role, content, timestamp) {
        // Remove welcome message if present
        const welcome = messagesContainer.querySelector('.welcome-message');
        if (welcome) {
            welcome.remove();
        }
        
        const div = document.createElement('div');
        div.className = `message ${role}`;
        
        const avatar = role === 'user' ? 'U' : role === 'assistant' ? 'AI' : '!';
        const time = timestamp || new Date().toLocaleTimeString([], { 
            hour: '2-digit', 
            minute: '2-digit' 
        });
        
        div.innerHTML = `
            <div class="avatar" aria-hidden="true">${avatar}</div>
            <div>
                <div class="message-bubble">${renderMarkdown(content)}</div>
                <div class="message-timestamp" aria-label="Message time">${time}</div>
            </div>
        `;
        
        messagesContainer.appendChild(div);
        scrollToBottom();
    }
    
    /**
     * Simple markdown-like rendering
     * @param {string} text - Text to render
     * @returns {string} HTML
     */
    function renderMarkdown(text) {
        // Escape HTML first
        let html = escapeHtml(text);
        
        // Code blocks (```...```)
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
        
        // Inline code (`...`)
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        
        // Bold (**...**)
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        
        // Headers (# ## ### ####)
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
        
        // Links ([text](url))
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        
        return html;
    }
    
    // Close skill selector when clicking outside
    document.addEventListener('click', function(e) {
        if (!skillSelector.contains(e.target)) {
            hideSkillSelector();
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
        timestamp.textContent = new Date().toLocaleTimeString([], { 
            hour: '2-digit', 
            minute: '2-digit' 
        });
        bubble.appendChild(timestamp);
    }
    
    /**
     * Send message to the server with SSE streaming
     */
    async function sendMessage() {
        if (isLoading) return;
        
        const content = messageInput.value.trim();
        if (!content) return;
        
        isLoading = true;
        sendButton.disabled = true;
        messageInput.value = '';
        messageInput.style.height = 'auto';
        
        addMessage('user', content);
        
        statusSpan.textContent = 'Streaming...';
        typingIndicator.classList.remove('show');
        
        try {
            const response = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json' 
                },
                body: JSON.stringify({ 
                    message: content,
                    session_id: 'webchat'
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            
            // Create streaming message placeholder
            const streamingDiv = createStreamingMessage();
            let accumulatedContent = '';
            
            // Read streaming response
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                const chunk = decoder.decode(value, { stream: true });
                const lines = chunk.split('\n');
                
                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        const eventType = line.slice(7).trim();
                        
                        // Find the corresponding data line
                        const dataIndex = lines.indexOf(line) + 1;
                        if (dataIndex < lines.length) {
                            const dataLine = lines[dataIndex];
                            if (dataLine.startsWith('data: ')) {
                                const data = dataLine.slice(6).trim();
                                
                                if (eventType === 'chunk') {
                                    // Decode escaped newlines
                                    const text = data.replace(/\\n/g, '\n').replace(/\\r/g, '\r');
                                    accumulatedContent += text;
                                    updateStreamingMessage(streamingDiv, accumulatedContent);
                                }
                            }
                        }
                    }
                }
            }
            
            // Streaming complete
            finishStreamingMessage(streamingDiv);
            
            // Get usage from last event (in real implementation, track session_id)
            statusSpan.textContent = 'Ready';
            
        } catch (error) {
            // Fallback to regular API on stream error
            console.warn('SSE failed, falling back to regular API:', error);
            await sendMessageFallback(content);
        } finally {
            isLoading = false;
            sendButton.disabled = false;
            messageInput.focus();
        }
    }
    
    /**
     * Fallback: Send message using regular API
     */
    async function sendMessageFallback(content) {
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json' 
                },
                body: JSON.stringify({ 
                    message: content,
                    session_id: 'webchat'
                })
            });
            
            const data = await response.json();
            
            if (data.error) {
                addMessage('error', `Error: ${data.error}`);
                statusSpan.textContent = 'Error';
            } else {
                addMessage('assistant', data.response);
                
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
            }
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
    
    /**
     * Load recent sessions from API
     */
    async function loadRecentSessions() {
        if (!recentSessionsList) return;
        
        recentSessionsList.innerHTML = '<div class="loading-sessions">Loading...</div>';
        
        try {
            const response = await fetch('/api/sessions?limit=10');
            const data = await response.json();
            
            if (data.error || !data.sessions) {
                recentSessionsList.innerHTML = '<div class="loading-sessions">No recent sessions</div>';
                return;
            }
            
            if (data.sessions.length === 0) {
                recentSessionsList.innerHTML = '<div class="loading-sessions">No recent sessions</div>';
                return;
            }
            
            recentSessionsList.innerHTML = data.sessions.map((session, index) => `
                <div class="recent-session-item ${index === 0 ? 'active' : ''}" data-session-id="${session.session_id}">
                    <svg class="recent-session-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <div class="recent-session-info">
                        <div class="recent-session-name">${escapeHtml(session.last_message || 'New Chat')}</div>
                    </div>
                </div>
            `).join('');
            
            // Add click handlers
            recentSessionsList.querySelectorAll('.recent-session-item').forEach(item => {
                item.addEventListener('click', function() {
                    const sessionId = this.dataset.sessionId;
                    loadSession(sessionId);
                    
                    // Update active state
                    recentSessionsList.querySelectorAll('.recent-session-item').forEach(i => i.classList.remove('active'));
                    this.classList.add('active');
                });
            });
            
        } catch (error) {
            console.error('Error loading sessions:', error);
            recentSessionsList.innerHTML = '<div class="loading-sessions">Error loading sessions</div>';
        }
    }
    
    /**
     * Load a specific session
     */
    async function loadSession(sessionId) {
        currentSessionId = sessionId;
        messagesContainer.innerHTML = '';
        statusSpan.textContent = `Session: ${sessionId}`;
    }
    
    // Refresh sessions button
    if (refreshSessionsBtn) {
        refreshSessionsBtn.addEventListener('click', function() {
            this.classList.add('loading');
            loadRecentSessions().finally(() => {
                this.classList.remove('loading');
            });
        });
    }
    
    // Load recent sessions on page load
    loadRecentSessions();
    
    // ========== Sidebar Actions ==========
    
    document.addEventListener('click', function(e) {
        const action = e.target.closest('[data-action]')?.dataset.action;
        if (!action) return;
        
        if (action === 'new-chat') {
            currentSessionId = null;
            messagesContainer.innerHTML = `
                <div class="welcome-message">
                    <h2>👋 New Chat</h2>
                    <p>Start a new conversation</p>
                </div>
            `;
            statusSpan.textContent = 'Ready';
            recentSessionsList.querySelectorAll('.recent-session-item').forEach(i => i.classList.remove('active'));
        } else if (action === 'files') {
            showFileExplorer();
        } else if (action === 'settings') {
            showSettings();
        }
    });
    
    // ========== File Explorer ==========
    
    const fileExplorerModal = document.getElementById('fileExplorerModal');
    const closeFileExplorer = document.getElementById('closeFileExplorer');
    const fileExplorerContent = document.getElementById('fileExplorerContent');
    
    async function showFileExplorer(path = '/root/.openclaw/workspace/engineering-flow') {
        fileExplorerModal.classList.add('show');
        fileExplorerContent.innerHTML = '<div class="loading">Loading...</div>';
        
        try {
            const response = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            
            if (data.error) {
                fileExplorerContent.innerHTML = `<div class="loading">${escapeHtml(data.error)}</div>`;
                return;
            }
            
            const pathParts = data.path.split('/').filter(p => p);
            let pathHtml = '<div class="file-explorer-path"><button data-path="/">/</button>';
            let currentPath = '';
            pathParts.forEach(part => {
                currentPath += '/' + part;
                pathHtml += ' / <button data-path="' + currentPath + '">' + escapeHtml(part) + '</button>';
            });
            pathHtml += '</div>';
            
            if (data.items.length === 0) {
                fileExplorerContent.innerHTML = pathHtml + '<div class="file-explorer-empty">Empty directory</div>';
                return;
            }
            
            fileExplorerContent.innerHTML = pathHtml + `
                <div class="file-explorer-list">
                    ${data.items.map(item => `
                        <div class="file-explorer-item" data-path="${item.path}" data-is-dir="${item.is_dir}">
                            ${item.is_dir ? 
                                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' :
                                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
                            }
                            <span class="file-name">${escapeHtml(item.name)}</span>
                        </div>
                    `).join('')}
                </div>
            `;
            
            // Add click handlers
            fileExplorerContent.querySelectorAll('.file-explorer-path button').forEach(btn => {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    showFileExplorer(this.dataset.path);
                });
            });
            
            fileExplorerContent.querySelectorAll('.file-explorer-item').forEach(item => {
                item.addEventListener('click', function() {
                    if (this.dataset.is_dir === 'true') {
                        showFileExplorer(this.dataset.path);
                    } else {
                        // Insert file path in chat
                        messageInput.value += this.dataset.path;
                        messageInput.focus();
                        fileExplorerModal.classList.remove('show');
                    }
                });
            });
            
        } catch (error) {
            console.error('Error loading files:', error);
            fileExplorerContent.innerHTML = '<div class="loading">Error loading files</div>';
        }
    }
    
    if (closeFileExplorer) {
        closeFileExplorer.addEventListener('click', function() {
            fileExplorerModal.classList.remove('show');
        });
        fileExplorerModal.addEventListener('click', function(e) {
            if (e.target === fileExplorerModal) fileExplorerModal.classList.remove('show');
        });
    }
    
    // ========== Settings ==========
    
    const settingsModal = document.getElementById('settingsModal');
    const closeSettings = document.getElementById('closeSettings');
    const settingsContent = document.getElementById('settingsContent');
    const settingsThemeToggle = document.getElementById('settingsThemeToggle');
    const settingsDefaultModel = document.getElementById('settingsDefaultModel');
    const settingsOpenAIKey = document.getElementById('settingsOpenAIKey');
    const settingsAnthropicKey = document.getElementById('settingsAnthropicKey');
    const saveSettingsBtn = document.getElementById('saveSettings');
    
    function showSettings() {
        settingsModal.classList.add('show');
        
        // Load saved settings
        const theme = getTheme();
        settingsThemeToggle.checked = theme === 'dark';
        
        // Load other settings from localStorage
        const savedModel = localStorage.getItem('efp-default-model');
        if (savedModel) settingsDefaultModel.value = savedModel;
        
        const savedOpenAIKey = localStorage.getItem('efp-openai-key');
        if (savedOpenAIKey) settingsOpenAIKey.value = savedOpenAIKey;
        
        const savedAnthropicKey = localStorage.getItem('efp-anthropic-key');
        if (savedAnthropicKey) settingsAnthropicKey.value = savedAnthropicKey;
    }
    
    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener('click', function() {
            // Save theme
            setTheme(settingsThemeToggle.checked ? 'dark' : 'light');
            
            // Save other settings
            localStorage.setItem('efp-default-model', settingsDefaultModel.value);
            localStorage.setItem('efp-openai-key', settingsOpenAIKey.value);
            localStorage.setItem('efp-anthropic-key', settingsAnthropicKey.value);
            
            settingsModal.classList.remove('show');
        });
    }
    
    if (closeSettings) {
        closeSettings.addEventListener('click', function() {
            settingsModal.classList.remove('show');
        });
        settingsModal.addEventListener('click', function(e) {
            if (e.target === settingsModal) settingsModal.classList.remove('show');
        });
    }
    
    // ========== Copy Code Button ==========
    
    function addCopyButtons() {
        document.querySelectorAll('pre code').forEach((block) => {
            if (block.parentElement.querySelector('.copy-code-button')) return;
            
            const button = document.createElement('button');
            button.className = 'copy-code-button';
            button.textContent = 'Copy';
            button.addEventListener('click', function() {
                navigator.clipboard.writeText(block.textContent).then(() => {
                    this.textContent = 'Copied!';
                    this.classList.add('copied');
                    setTimeout(() => {
                        this.textContent = 'Copy';
                        this.classList.remove('copied');
                    }, 2000);
                });
            });
            block.parentElement.style.position = 'relative';
            block.parentElement.appendChild(button);
        });
    }
    
    // Add copy buttons when new messages are added
    const originalAddMessage = window.webchatAddMessage;
    window.webchatAddMessage = function(role, content, timestamp) {
        const result = originalAddMessage(role, content, timestamp);
        addCopyButtons();
        return result;
    };
    
    // Add copy buttons to existing code blocks
    addCopyButtons();
    
})();
