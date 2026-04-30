(() => {
  'use strict';

  const messagesContainer = document.getElementById('messages');
  const messageInput = document.getElementById('messageInput');
  const sendButton = document.getElementById('sendButton');
  const fileInput = document.getElementById('fileInput');
  const uploadButton = document.getElementById('uploadButton');
  const statusEl = document.getElementById('status');
  const pendingAttachmentsEl = document.getElementById('pendingAttachments');
  const fileExplorerPanel = document.getElementById('fileExplorerPanel');
  const closeFileExplorer = document.getElementById('closeFileExplorer');
  const fileExplorerContent = document.getElementById('fileExplorerContent');
  const fileExplorerTitle = document.getElementById('fileExplorerTitle');
  const toggleServerFiles = document.getElementById('toggleServerFiles');

  const SESSION_ID_KEY = 'webchat_session_id';
  let currentSessionId = localStorage.getItem(SESSION_ID_KEY) || '';
  let pendingAttachments = [];
  let serverFilesCurrentPath = '';

  function createWebchatSessionId() {
    const now = new Date();
    const timestamp = now.getFullYear() +
      String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' +
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

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  }

  function setStatus(text, _kind) {
    if (statusEl) statusEl.textContent = text;
  }

  function addMessage(role, content) {
    if (!messagesContainer) return;
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = `<div class="message-content">${escapeHtml(content)}</div>`;
    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

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
        <span class="pending-attachment-status">${escapeHtml(a.status)}</span>
        ${a.error ? `<span class="pending-attachment-error">${escapeHtml(a.error)}</span>` : ''}
        <button type="button" class="pending-attachment-remove" data-remove-file-id="${escapeHtml(a.file_id || a.local_id)}" aria-label="Remove attachment">×</button>
      </div>
    `).join('');
  }

  pendingAttachmentsEl?.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-remove-file-id]');
    if (!btn) return;
    const id = btn.dataset.removeFileId;
    const item = pendingAttachments.find((a) => a.file_id === id || a.local_id === id);
    pendingAttachments = pendingAttachments.filter((a) => a.file_id !== id && a.local_id !== id);
    renderPendingAttachments();
    if (item?.file_id) {
      try {
        await fetch(`/api/files/${encodeURIComponent(item.file_id)}`, { method: 'DELETE' });
      } catch (_e) {}
    }
  });

  function shouldParseAttachment(file, uploaded) {
    const name = (file?.name || uploaded?.filename || '').toLowerCase();
    const type = (uploaded?.content_type || file?.type || '').toLowerCase();
    if (type.startsWith('image/')) return false;
    return type.includes('pdf') || type.includes('word') || type.includes('excel') || type.includes('spreadsheet') || type.includes('csv') || type.includes('text') ||
      name.endsWith('.pdf') || name.endsWith('.docx') || name.endsWith('.xlsx') || name.endsWith('.csv') || name.endsWith('.txt');
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
      item.status = shouldParseAttachment(file, data) ? 'parsing' : 'ready';
      renderPendingAttachments();
      if (shouldParseAttachment(file, data)) {
        const parseResp = await fetch(`/api/files/parse?session_id=${encodeURIComponent(requestSessionId)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: data.file_id, options: {} })
        });
        const parseData = await parseResp.json();
        if (!parseResp.ok || !parseData.success) throw new Error(parseData.error || 'Parse failed');
        item.status = 'ready';
      }
      setStatus('File ready: ' + item.filename, 'success');
      renderPendingAttachments();
    } catch (error) {
      item.status = 'error';
      item.error = error.message || String(error);
      setStatus('Upload failed: ' + item.error, 'error');
      renderPendingAttachments();
    }
  }

  async function sendMessageFallback(content, attachmentIds = []) {
    const requestSessionId = ensureCurrentSessionId();
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: content || (attachmentIds.length ? '[attachment]' : ''),
        session_id: requestSessionId,
        attachments: attachmentIds
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Send failed');
    if (data.session_id) {
      currentSessionId = data.session_id;
      localStorage.setItem(SESSION_ID_KEY, currentSessionId);
    }
    return data;
  }

  async function sendMessage() {
    const content = (messageInput?.value || '').trim();
    const busy = pendingAttachments.filter((a) => a.status === 'uploading' || a.status === 'parsing');
    if (busy.length) {
      setStatus(`Waiting for ${busy.length} attachment(s)...`, 'uploading');
      return;
    }
    const readyAttachments = pendingAttachments.filter((a) => a.file_id && a.status !== 'error');
    const attachmentIds = readyAttachments.map((a) => a.file_id);
    if (!content && attachmentIds.length === 0) return;
    const displayContent = content || readyAttachments.map((a) => `📎 ${a.filename}`).join('\n') || '📎 Attachment';
    addMessage('user', displayContent);
    if (messageInput) messageInput.value = '';
    pendingAttachments = [];
    renderPendingAttachments();
    try {
      const data = await sendMessageFallback(content, attachmentIds);
      addMessage('assistant', data.response || '');
      setStatus('Ready', 'success');
    } catch (error) {
      addMessage('assistant', `❌ ${error.message || 'Send failed'}。附件为一次性，请重新上传后重试。`);
      setStatus('Send failed', 'error');
    }
  }

  async function showFileExplorer(path) {
    if (!fileExplorerPanel || !fileExplorerContent) return;
    serverFilesCurrentPath = path || '';
    fileExplorerPanel.classList.add('show');
    if (fileExplorerTitle) fileExplorerTitle.textContent = 'Server Files';
    fileExplorerContent.innerHTML = '<div class="loading">Loading...</div>';
    try {
      const resp = await fetch('/api/server-files/list?path=' + encodeURIComponent(serverFilesCurrentPath));
      const data = await resp.json();
      const entries = data.entries || [];
      if (!entries.length) {
        fileExplorerContent.innerHTML = '<div class="empty">No files</div>';
        return;
      }
      fileExplorerContent.innerHTML = entries.map((e) => `<div class="file-item">${escapeHtml(e.name)}</div>`).join('');
    } catch (e) {
      fileExplorerContent.innerHTML = '<div class="error">Failed to load</div>';
    }
  }

  uploadButton?.addEventListener('click', () => fileInput?.click());
  fileInput?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    for (const file of files) await uploadFile(file);
    fileInput.value = '';
  });

  sendButton?.addEventListener('click', sendMessage);
  messageInput?.addEventListener('keydown', (e) => {
    const skillSelector = document.getElementById('skillSelector');
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229 && !(skillSelector && skillSelector.classList.contains('active'))) {
      e.preventDefault();
      sendMessage();
    }
  });
  messageInput?.addEventListener('paste', async function (e) {
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

  document.querySelectorAll('.sidebar-item[data-action="server-files"]').forEach((el) => {
    el.addEventListener('click', () => showFileExplorer(''));
  });
  closeFileExplorer?.addEventListener('click', () => fileExplorerPanel?.classList.remove('show'));
  if (toggleServerFiles) {
    toggleServerFiles.addEventListener('click', () => {
      toggleServerFiles.classList.add('active');
      if (fileExplorerTitle) fileExplorerTitle.textContent = 'Server Files';
      showFileExplorer(serverFilesCurrentPath || '');
    });
  }
})();
