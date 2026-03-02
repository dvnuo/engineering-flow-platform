// WebChat JavaScript

(function() {
    'use strict';
    
    // DOM Elements
    const messagesContainer = document.getElementById('messages');
    const messageInput = document.getElementById('messageInput');
    const sendButton = document.getElementById('sendButton');
    const fileInput = document.getElementById('fileInput');
    const uploadButton = document.getElementById('uploadButton');
    
    // File upload handling
    if (uploadButton && fileInput) {
        uploadButton.addEventListener('click', () => {
            fileInput.click();
        });
        
        fileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            
            // Show uploading status
            setStatus('Uploading file...', 'uploading');
            
            try {
                const formData = new FormData();
                formData.append('file', file);
                
                const sessionId = ''; // Not required for My Uploads
                const headers = sessionId ? { 'X-Session-ID': sessionId } : {};
                
                const response = await fetch('/api/files/upload', {
                    method: 'POST',
                    body: formData,
                    headers
                });
                
                const data = await response.json();
                
                if (data.success) {
                    setStatus('File uploaded: ' + data.filename, 'success');
                    
                    // Add to file list
                    refreshFileList();
                    
                    // Show file info in chat
                    addMessage('assistant', `📎 File uploaded: **${data.filename}**\n\nYou can now ask me to analyze or discuss this file.`);
                } else {
                    setStatus('Upload failed: ' + data.error, 'error');
                    addMessage('assistant', `❌ File upload failed: ${data.error}`);
                }
            } catch (error) {
                console.error('Upload error:', error);
                setStatus('Upload failed: ' + error.message, 'error');
            }
            
            // Reset file input
            fileInput.value = '';
        });
    }
    
    // Refresh file list
    async function refreshFileList() {
        const fileExplorerContent = document.getElementById('fileExplorerContent');
        if (!fileExplorerContent) return;
        
        try {
            // My Uploads - show all uploaded files from metadata
            const headers = {};
            
            // Try new context API first, fall back to files API
            let files = [];
            try {
                const contextResp = await fetch('/api/context/files?session_id=' + sessionId, { headers });
                if (contextResp.ok) {
                    const contextData = await contextResp.json();
                    files = contextData.files || [];
                }
            } catch (e) {
                // Fall back to files API
            }
            
            // If no files from context API, try files API
            if (files.length === 0) {
                const response = await fetch('/api/files/list', { headers });
                if (response.ok) {
                    const data = await response.json();
                    files = data.files || [];
                }
            }
            
            if (files && files.length > 0) {
                let html = '<ul class="file-list">';
                for (const file of files) {
                    
                    html += `
                        <li class="file-item" data-file-id="${file.file_id}">
                            <span class="file-icon">${getFileIcon(file.content_type)}</span>
                            <div class="file-info">
                                <span class="file-name" title="${escapeHtml(file.filename)}">${escapeHtml(file.filename)}</span>
                            </div>
                            <div class="file-actions">
                                
                                <button class="file-action cite-btn" data-file-id="${file.file_id}" title="Ask about this file">@file_${file.file_id.slice(0,8)}</button>
                                <button class="file-action delete-btn" data-file-id="${file.file_id}" title="Delete file">🗑️</button>
                            </div>
                        </li>
                    `;
                }
                html += '</ul>';
                fileExplorerContent.innerHTML = html;
                
                // Add cite button handlers
                fileExplorerContent.querySelectorAll('.cite-btn').forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const fileRef = e.target.dataset.fileId;
                        // Insert reference into input
                        const input = document.getElementById('messageInput');
                        if (input) {
                            input.value += '@file_' + fileRef.slice(0, 8) + ' ';
                            input.focus();
                        }
                    });
                });
                
                // Add delete button handlers
                fileExplorerContent.querySelectorAll('.delete-btn').forEach(btn => {
                    btn.addEventListener('click', async (e) => {
                        const fileId = e.target.dataset.fileId;
                        if (!confirm('Delete this file?')) return;
                        
                        try {
                            const response = await fetch('/api/files/' + fileId, { method: 'DELETE' });
                            const data = await response.json();
                            if (data.success || response.ok) {
                                refreshFileList(); // Reload list
                            } else {
                                alert('Delete failed: ' + (data.error || 'Unknown error'));
                            }
                        } catch (err) {
                            console.error('Delete error:', err);
                            alert('Delete failed');
                        }
                    });
                });
            } else {
                fileExplorerContent.innerHTML = '<div class="empty">No files uploaded yet</div>';
            }
        } catch (error) {
            console.error('Error loading files:', error);
            fileExplorerContent.innerHTML = '<div class="error">Failed to load files</div>';
        }
    }
    
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
    const fileSelector = document.getElementById('fileSelector');
    const fileDropdown = document.getElementById('fileDropdown');
    const fileList = document.getElementById('fileList');
    const themeToggle = document.getElementById('themeToggle');
    const newChatBtn = document.querySelector('[data-action="new-chat"]');
    
    // State
    let isLoading = false;
    let totalTokens = 0;
    let totalCost = 0;
    let skills = [];
    let selectedSkillIndex = -1;
    let skillsLoaded = false;
    let uploadedFiles = [];
    let selectedFileIndex = -1;
    let filesLoaded = false;
    let currentSessionId = localStorage.getItem('efp-session-id') || null;
    let fileViewMode = 'server'; // 'server' or 'uploads'
    console.log('[WebChat] Initial sessionId from localStorage:', currentSessionId);
    
    // ========== Helper Functions ==========
    
    /**
     * Format timestamp with smart date display
     * - Same day: "14:30"
     * - Yesterday: "Yesterday 14:30"
     * - Within a week: "Thursday 14:30"
     * - Within a year: "Jan 15 14:30"
     * - Over a year ago: "2024-01-15 14:30"
     */
    function formatSmartDate(date) {
        const now = new Date();
        const messageDate = new Date(date);
        const timeStr = messageDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        
        // Same day
        if (messageDate.toDateString() === now.toDateString()) {
            return timeStr;
        }
        
        // Yesterday
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (messageDate.toDateString() === yesterday.toDateString()) {
            return `Yesterday ${timeStr}`;
        }
        
        // Within the last 7 days
        const oneWeekAgo = new Date(now);
        oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
        if (messageDate > oneWeekAgo) {
            const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
            return `${days[messageDate.getDay()]} ${timeStr}`;
        }
        
        // Within the same year
        if (messageDate.getFullYear() === now.getFullYear()) {
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const month = months[messageDate.getMonth()];
            const day = messageDate.getDate();
            return `${month} ${day} ${timeStr}`;
        }
        
        // Over a year ago
        const year = messageDate.getFullYear();
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const month = months[messageDate.getMonth()];
        const day = messageDate.getDate();
        return `${year}-${month}-${day} ${timeStr}`;
    }
    
    // ========== Theme Management ==========
    
    const THEME_KEY = 'efp-theme';
    const SESSION_ID_KEY = 'efp-session-id';
    
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
    
    /**
     * Show usage statistics modal
     */
    async function showStats() {
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
        statsPanel.classList.remove('show');
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
     * Show file selector dropdown
     */
    function showFileSelector() {
        // Always reload files to get latest
        filesLoaded = false;
        uploadedFiles = [];
        
        if (!uploadedFiles.length) {
            loadFilesForSelector().then(() => {
                if (uploadedFiles.length) {
                    renderFileList();
                    fileSelector.classList.add('active');
                }
            });
            return;
        }
        renderFileList();
        fileSelector.classList.add('active');
    }
    
    /**
     * Hide file selector dropdown
     */
    function hideFileSelector() {
        fileSelector.classList.remove('active');
        selectedFileIndex = -1;
    }
    
    /**
     * Load files for selector
     */
    async function loadFilesForSelector() {
        if (filesLoaded) return;
        
        try {
            const response = await fetch('/api/files/list');
            const data = await response.json();
            uploadedFiles = data.files || [];
            filesLoaded = true;
        } catch (error) {
            console.error('Error loading files:', error);
            uploadedFiles = [];
        }
    }
    
    /**
     * Render file list in dropdown
     */
    function renderFileList() {
        if (!uploadedFiles.length) {
            fileList.innerHTML = '<div class="skill-item"><span class="skill-desc">No files uploaded</span></div>';
            return;
        }
        
        // Get query after @
        const inputVal = messageInput.value;
        const atIndex = inputVal.lastIndexOf('@');
        const query = atIndex >= 0 ? inputVal.slice(atIndex + 1).toLowerCase() : '';
        
        let filteredFiles = uploadedFiles;
        
        if (query) {
            filteredFiles = uploadedFiles.filter(f => 
                f.filename.toLowerCase().includes(query) || 
                f.file_id.toLowerCase().includes(query)
            );
        }
        
        fileList.innerHTML = filteredFiles.map((file, index) => `
            <div class="skill-item" 
                 role="option" 
                 data-file-id="${file.file_id}"
                 data-index="${index}">
                <span class="skill-name">${getFileIcon(file.content_type)} ${escapeHtml(file.filename)}</span>
                <span class="skill-desc">@file_${file.file_id.slice(0, 8)}</span>
            </div>
        `).join('');
        
        // Add click handlers
        fileList.querySelectorAll('.skill-item').forEach(item => {
            item.addEventListener('click', () => {
                const fileId = item.dataset.fileId;
                const atIndex = messageInput.value.lastIndexOf('@');
                messageInput.value = messageInput.value.slice(0, atIndex) + '@file_' + fileId.slice(0, 8) + ' ';
                messageInput.focus();
                hideFileSelector();
            });
        });
        
        selectedFileIndex = -1;
    }
    
    /**
     * Navigate file list
     */
    function navigateFileList(direction) {
        const items = fileList.querySelectorAll('.skill-item');
        if (!items.length) return;
        
        if (selectedFileIndex >= 0) {
            items[selectedFileIndex].classList.remove('selected');
        }
        
        selectedFileIndex += direction;
        if (selectedFileIndex < 0) selectedFileIndex = items.length - 1;
        if (selectedFileIndex >= items.length) selectedFileIndex = 0;
        
        items[selectedFileIndex].classList.add('selected');
        items[selectedFileIndex].scrollIntoView({ block: 'nearest' });
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
    
    // Upload file function
    async function uploadFile(file) {
        setStatus('Uploading file...', 'uploading');
        try {
            const formData = new FormData();
            formData.append('file', file);
            const sessionId = ''; // Not required for My Uploads
            const headers = sessionId ? { 'X-Session-ID': sessionId } : {};
            const response = await fetch('/api/files/upload', { method: 'POST', body: formData, headers });
            const data = await response.json();
            if (data.success) {
                setStatus('File uploaded: ' + data.filename, 'success');
                const shortId = data.file_id.substring(0, 8);
                messageInput.value = '@file_' + shortId + ' ';
                messageInput.focus();
                refreshFileList();
                addMessage('assistant', '📎 File uploaded: ' + data.filename);
            } else {
                setStatus('Upload failed: ' + data.error, 'error');
            }
        } catch (error) {
            console.error('Upload error:', error);
            setStatus('Upload failed', 'error');
        }
    }
    
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
    
    // Add listeners to both input area and chat container
    chatInputArea.addEventListener('dragover', handleDragOver);
    chatInputArea.addEventListener('dragleave', handleDragLeave);
    chatInputArea.addEventListener('drop', handleDrop);
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
        
        // Show skill selector on /
        if (e.key === '/' && messageInput.selectionStart === 0) {
            e.preventDefault();
            showSkillSelector();
            return;
        }
        
        // Show file selector on @
        if (e.key === '@') {
            const cursorPos = messageInput.selectionStart;
            const textBefore = messageInput.value.slice(0, cursorPos);
            // Only show if @ is at start or after space
            if (cursorPos === 0 || textBefore.endsWith(' ') || textBefore.endsWith('\n')) {
                e.preventDefault();
                showFileSelector();
                return;
            }
        }
        
        // File selector navigation
        if (fileSelector.classList.contains('active')) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                navigateFileList(1);
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                navigateFileList(-1);
                return;
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                const selected = fileList.querySelector('.skill-item.selected');
                if (selected) {
                    const fileId = selected.dataset.fileId;
                    const atIndex = messageInput.value.lastIndexOf('@');
                    messageInput.value = messageInput.value.slice(0, atIndex) + '@file_' + fileId.slice(0, 8) + ' ';
                    messageInput.focus();
                    hideFileSelector();
                }
                return;
            }
            if (e.key === 'Escape') {
                hideFileSelector();
                return;
            }
        }
        
        // Close skill selector when deleting the /
        if (e.key === 'Backspace' || e.key === 'Delete') {
            if (messageInput.value === '/' && skillSelector.classList.contains('active')) {
                hideSkillSelector();
            }
            // Close file selector when deleting @
            const atIndex = messageInput.value.lastIndexOf('@');
            if (atIndex === -1 && fileSelector.classList.contains('active')) {
                hideFileSelector();
            }
        }
        
        // Send message
        if (e.key === 'Enter' && !e.shiftKey && !skillSelector.classList.contains('active') && !fileSelector.classList.contains('active')) {
            e.preventDefault();
            sendMessage();
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
    function addMessage(role, content, timestamp, toolCalls = null) {
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
            badge = '<span class="tool-badge">🔧 Tool Result</span>';
        } else if (role === 'assistant' && toolCalls && toolCalls.length > 0) {
            // Assistant message with tool calls but no content (pending tool execution)
            badge = '<span class="tool-calls-badge">⚙️ Calling Tools</span>';
            const toolNames = toolCalls.map(tc => tc.function?.name || tc.name).join(', ');
            messageContent = `_Calling: ${toolNames}_`;
        }
        
        div.innerHTML = `
            <div class="avatar" aria-hidden="true">${avatar}</div>
            <div>
                ${badge}
                <div class="message-bubble">${renderMarkdown(messageContent)}</div>
                <div class="message-timestamp" aria-label="Message time">${time}</div>
            </div>
        `;
        
        messagesContainer.appendChild(div);
        scrollToBottom();
        
        // Mark assistant messages as pending thinking process
        if (role === 'assistant') {
            markPendingAssistant(div);
        }
        
        // Apply syntax highlighting to code blocks
        if (typeof hljs !== 'undefined') {
            div.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
        }
    }
    
    /**
     * Render markdown text to HTML
     * @param {string} text - Markdown text to render
     * @returns {string} HTML
     */
    function renderMarkdown(text) {
        if (!text || typeof text !== 'string') {
            return '';
        }
        
        // Input length validation to prevent ReDoS and memory issues
        const MAX_INPUT_LENGTH = 100000; // 100KB limit
        if (text.length > MAX_INPUT_LENGTH) {
            text = text.substring(0, MAX_INPUT_LENGTH);
        }
        
        // Escape HTML first to prevent XSS
        let html = escapeHtml(text);
        
        // Code blocks with language class (```lang ... ```)
        html = html.replace(/```(\w*)\s*([\s\S]*?)```/g, function(match, lang, code) {
            const langClass = lang ? `language-${lang}` : '';
            const escapedCode = escapeHtml(code.trim());
            return `<pre><code class="${langClass}">${escapedCode}</code></pre>`;
        });
        
        // Inline code (`...`)
        html = html.replace(/`([^`]+)`/g, function(match, code) {
            return '<code>' + escapeHtml(code) + '</code>';
        });
        
        // Bold (**...** or __...__)
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
        
        // Italic (*...* or _..._)
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
        
        // Strikethrough (~~...~~)
        html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
        
        // Spoiler (||...||) - collapsible content
        html = html.replace(/\|\|([^|]+)\|\|/g, '<span class="spoiler">$1</span>');
        
        // Headers (# ## ### ####)
        html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
        
        // Blockquotes (> ...)
        html = html.replace(/^&gt;\s*(.+)$/gm, '<blockquote>$1</blockquote>');
        html = html.replace(/^>\s*(.+)$/gm, '<blockquote>$1</blockquote>');
        
        // Unordered lists (- or * or +)
        html = html.replace(/^[\-\*\+]\s+(.+)$/gm, '<li>$1</li>');
        
        // Ordered lists (1. 2. etc.)
        html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
        
        // Wrap consecutive list items in <ul> or <ol>
        html = html.replace(/(<li>.*<\/li>)+/g, function(match) {
            // Check if any item starts with a digit pattern (ordered list)
            const hasOrdered = /<li>\s*\d+\./.test(match);
            if (hasOrdered) {
                return '<ol>' + match + '</ol>';
            }
            return '<ul>' + match + '</ul>';
        });
        
        // Links ([text](url))
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="link">$1</a>');
        
        // Horizontal rules (--- or ***)
        html = html.replace(/^[\-\*]{3,}$/gm, '<hr class="divider">');
        
        return html;
    }
    
    // Close skill and file selector when clicking outside
    document.addEventListener('click', function(e) {
        if (!skillSelector.contains(e.target)) {
            hideSkillSelector();
        }
        if (!fileSelector.contains(e.target)) {
            hideFileSelector();
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
        
        // Apply syntax highlighting to finished message
        if (typeof hljs !== 'undefined') {
            div.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
        }
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
        
        // Clear input first (before addMessage to avoid any race conditions)
        messageInput.value = '';
        messageInput.style.height = 'auto';
        // Force browser to update (fix for autocomplete/ cached values)
        messageInput.blur();
        messageInput.focus();
        
        addMessage('user', content);
        
        statusSpan.textContent = 'Thinking...';
        typingIndicator.classList.remove('show');
        
        // Reset events for new request
        resetEvents();
        
        // Use regular API directly (SSE streaming not yet implemented)
        await sendMessageFallback(content);
        
        isLoading = false;
        sendButton.disabled = false;
        messageInput.focus();
    }
    
    /**
     * Fallback: Send message using regular API
     */
    async function sendMessageFallback(content) {
        try {
            // Generate new session_id if currentSessionId is null/undefined
            const now = new Date();
            const timestamp = now.getFullYear() +
                String(now.getMonth() + 1).padStart(2, '0') +
                String(now.getDate()).padStart(2, '0') +
                '_' +
                String(now.getHours()).padStart(2, '0') +
                String(now.getMinutes()).padStart(2, '0') +
                String(now.getSeconds()).padStart(2, '0');
            const requestSessionId = currentSessionId || 'webchat_' + timestamp;
            
            console.log('[WebChat] Generated session_id:', requestSessionId);
            
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json' 
                },
                body: JSON.stringify({ 
                    message: content,
                    session_id: requestSessionId
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
            console.log('[WebChat] data.session_id:', data.session_id);
            if (data.session_id) {
                currentSessionId = data.session_id;
                console.log('[WebChat] Received session_id from server:', currentSessionId);
                localStorage.setItem(SESSION_ID_KEY, currentSessionId);
                console.log('[WebChat] Saved sessionId to localStorage:', currentSessionId);
                // Verify it was saved
                const saved = localStorage.getItem(SESSION_ID_KEY);
                console.log('[WebChat] Verified localStorage.getItem:', saved);
            }
            
            // Fetch full session to get complete message history including tool calls/results
            try {
                const sessionResponse = await fetch('/api/sessions/' + encodeURIComponent(currentSessionId));
                const sessionData = await sessionResponse.json();
                
                if (sessionData.messages && sessionData.messages.length > 0) {
                    // Clear loading message and render full history
                    messagesContainer.innerHTML = '';
                    
                    // Render all messages from session history
                    sessionData.messages.forEach(msg => {
                        const role = msg.role || 'user';
                        const content = msg.content || '';
                        const timestamp = msg.timestamp || msg.created_at;
                        addMessage(role, content, timestamp, msg.tool_calls);
                    });
                    
                    // Scroll to bottom after rendering all messages
                    scrollToBottom();
                    
                    // Show thinking events from session metadata
                    const metadata = sessionData.metadata || {};
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
                                            </div>`;
                                    });
                                    timelineHtml += '</div>';
                                    
                                    processContent.innerHTML = timelineHtml;
                                    
                                    // Add toggle functionality
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
                    addMessage('assistant', data.response);
                }
            } catch (sessionError) {
                console.error('[WebChat] Error loading session:', sessionError);
                // Fallback to simple response display
                addMessage('assistant', data.response);
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
                
                const item = document.createElement('div');
                item.className = `recent-session-item ${isActive ? 'active' : ''}`;
                item.setAttribute('data-session-id', sessionId);
                item.innerHTML = `
                    <svg class="recent-session-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <div class="recent-session-info">
                        <div class="recent-session-name">${escapeHtml(session.name || session.session_id || 'Chat ' + (sessionsOffset + index + 1))}</div>
                        <div class="recent-session-preview">${escapeHtml(session.last_message || '')}</div>
                    </div>
                `;
                
                // Add click handler
                item.addEventListener('click', function(e) {
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
                    addMessage(role, msg.content || '', msg.timestamp || msg.created_at, msg.tool_calls);
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
            fileViewMode = 'server';
            showFileExplorer();
        } else if (action === 'my-uploads') {
            fileViewMode = 'uploads';
            showMyUploads();
        } else if (action === 'settings') {
            showSettings();
        }
    });
    
    // ========== File Explorer ==========
    
    const fileExplorerPanel = document.getElementById('fileExplorerPanel');
    const closeFileExplorer = document.getElementById('closeFileExplorer');
    const fileExplorerContent = document.getElementById('fileExplorerContent');
    
    // Show My Uploads (user's uploaded files)
    async function showMyUploads() {
        fileExplorerPanel.classList.add('show');
        fileExplorerContent.innerHTML = '<div class="loading">Loading...</div>';
        
        // Update title
        const titleEl = document.getElementById('fileExplorerTitle');
        if (titleEl) titleEl.textContent = 'My Uploads';
        
        // Hide toggle buttons in panel
        const toggleDiv = document.querySelector('.file-toggle');
        if (toggleDiv) toggleDiv.style.display = 'none';
        
        await refreshFileList();
    }
    
    async function showFileExplorer(path = '/root') {
        fileExplorerPanel.classList.add('show');
        fileExplorerContent.innerHTML = '<div class="loading">Loading...</div>';
        const feTitle = document.getElementById('fileExplorerTitle');
        if (feTitle) feTitle.textContent = 'Server Files';
        
        try {
            const response = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            
            if (data.error) {
                fileExplorerContent.innerHTML = `<div class="loading">${escapeHtml(data.error)}</div>`;
                return;
            }
            
            const pathParts = data.path.split('/').filter(p => p);
            let pathHtml = '<div class="file-explorer-path">';
            pathHtml += '<button data-path="/">🏠</button>';
            let currentPath = '';
            let isFirst = true;
            pathParts.forEach(part => {
                currentPath += '/' + part;
                if (!isFirst) {
                    pathHtml += '<span class="separator">/</span>';
                }
                isFirst = false;
                pathHtml += '<button data-path="' + currentPath + '">' + escapeHtml(part) + '</button>';
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
                item.addEventListener('click', function(e) {
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
    
    async function showFileViewer(path) {
        fileViewerPanel.classList.add('show');
        fileViewerContent.innerHTML = '<div class="loading">Loading...</div>';
        
        try {
            const response = await fetch(`/api/files/read?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            
            if (data.error) {
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
    const sshEnabled = document.getElementById('sshEnabled');
    const sshKeyPath = document.getElementById('sshKeyPath');
    const debugEnabled = document.getElementById('debugEnabled');
    
    // Provider to Model mapping
    const providerModels = {
        github_copilot: [
            { value: 'gpt-4o', label: 'GPT-4o' }, 
            { value: 'gpt-5-mini', label: 'GPT-5 mini' }, 
            { value: 'gpt-5', label: 'GPT-5' }, 
            { value: 'gpt-5.1-codex', label: 'GPT-5.1-Codex' }, 
            { value: 'gpt-5.1-codex-max', label: 'GPT-5.1-Codex-Max' }, 
            { value: 'gpt-5.2', label: 'GPT-5.2' }, 
            { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
        ],
        openai: [
            { value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo' },
            { value: 'gpt-4', label: 'GPT-4' },
            { value: 'gpt-4o', label: 'GPT-4o' },
            { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
        ],
        anthropic: [
            { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4' },
            { value: 'claude-haiku-4-20250514', label: 'Claude Haiku 4' },
            { value: 'claude-opus-4-20250514', label: 'Claude Opus 4' },
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
                    const provider = config.llm.provider || 'github_copilot';
                    const model = config.llm.model || 'gpt-5-mini';
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
                    updateModelDropdown('github_copilot', 'gpt-5-mini');
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
                
                // SSH settings
                if (config.ssh) {
                    sshEnabled.checked = config.ssh.enabled || false;
                    sshKeyPath.value = config.ssh.private_key_path || '';
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
            // Determine api_base based on selected provider
            const provider = llmProvider.value;
            let apiBase = '';
            if (provider === 'github_copilot') {
                apiBase = 'https://api.githubcopilot.com';
            } else if (provider === 'anthropic') {
                apiBase = 'https://api.anthropic.com/v1';
            } else if (provider === 'ollama') {
                apiBase = 'http://127.0.0.1:11434';
            } else {
                apiBase = 'https://api.openai.com/v1';
            }
            
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
                ssh: {
                    enabled: sshEnabled.checked,
                    private_key_path: sshKeyPath.value,
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
                showAgentEvent('tool-call', `🔧 Calling: ${eventData.tool || 'Unknown tool'}`);
                break;
                
            case 'tool_result':
                // Show detailed result or error - ALWAYS show the result field
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
                // Show tool name and arguments in debug mode
                const argsStr = data.args ? JSON.stringify(data.args, null, 2) : '';
                message = `🔧 ${data.tool || 'Unknown tool'}\n📝 Args: ${argsStr || 'none'}`;
                break;
            case 'tool_result':
                // Show tool result or error
                const statusIcon = data.success ? '✅' : '❌';
                message = `${statusIcon} ${data.tool || 'Tool'} Result:\n${data.result || '(no result)'}`;
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
    const toggleMyUploads = document.getElementById('toggleMyUploads');
    const fileExplorerTitle = document.getElementById('fileExplorerTitle');
    
    if (toggleServerFiles && toggleMyUploads) {
        toggleServerFiles.addEventListener('click', function() {
            fileViewMode = 'server';
            toggleServerFiles.classList.add('active');
            toggleMyUploads.classList.remove('active');
            if (fileExplorerTitle) fileExplorerTitle.textContent = 'Server Files';
            refreshFileList();
        });
        
        toggleMyUploads.addEventListener('click', function() {
            fileViewMode = 'uploads';
            toggleMyUploads.classList.add('active');
            toggleServerFiles.classList.remove('active');
            if (fileExplorerTitle) fileExplorerTitle.textContent = 'My Uploads';
            refreshFileList();
        });
    }
    
})();
