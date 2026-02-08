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
    
    // State
    let isLoading = false;
    let totalTokens = 0;
    
    // Auto-resize textarea
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
    
    // Send on Enter (Shift+Enter for new line)
    messageInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
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
            <div class="avatar">${avatar}</div>
            <div>
                <div class="message-content">${escapeHtml(content)}</div>
                <div class="message-timestamp">${time}</div>
            </div>
        `;
        
        messagesContainer.appendChild(div);
        scrollToBottom();
    }
    
    /**
     * Escape HTML special characters
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        const html = div.innerHTML;
        return html.replace(/\n/g, '<br>');
    }
    
    /**
     * Scroll messages container to bottom
     */
    function scrollToBottom() {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    /**
     * Send message to the server
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
        
        statusSpan.textContent = 'Thinking...';
        typingIndicator.classList.add('show');
        
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
            
            typingIndicator.classList.remove('show');
            
            if (data.error) {
                addMessage('error', `Error: ${data.error}`);
                statusSpan.textContent = 'Error';
            } else {
                addMessage('assistant', data.response);
                
                if (data.usage && data.usage.total_tokens) {
                    totalTokens += data.usage.total_tokens;
                    tokenCountSpan.textContent = `Tokens: ${totalTokens}`;
                }
                
                statusSpan.textContent = 'Ready';
            }
        } catch (error) {
            typingIndicator.classList.remove('show');
            addMessage('error', `Connection error: ${error.message}`);
            statusSpan.textContent = 'Disconnected';
        } finally {
            isLoading = false;
            sendButton.disabled = false;
            messageInput.focus();
        }
    }
    
    // Expose sendMessage globally for testing
    window.webchatSendMessage = sendMessage;
    window.webchatAddMessage = addMessage;
    
})();
