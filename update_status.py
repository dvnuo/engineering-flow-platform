with open('src/gateway/static/js/webchat.js', 'r') as f:
    content = f.read()

# Update the "Thinking..." status to use the enhanced indicator
old_code = "        statusSpan.textContent = 'Thinking...';\n        typingIndicator.classList.remove('show');"
new_code = """        // Show enhanced status indicator
        typingIndicator.classList.add('show', 'processing');
        document.getElementById('typingIcon').innerHTML = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"12\" cy=\"12\" r=\"10\"/><polyline points=\"12 6 12 12 16 14\"/></svg>';
        document.getElementById('typingText').textContent = 'Sending...';
        document.getElementById('typingSubtext').textContent = '';"""

content = content.replace(old_code, new_code)

# Update error handling - first occurrence
old_error = """                addMessage('error', `Error: ${data.error}`);
                statusSpan.textContent = 'Error';"""
new_error = """                addMessage('error', `Error: ${data.error}`);
                typingIndicator.classList.remove('processing');
                typingIndicator.classList.add('error', 'show');
                document.getElementById('typingIcon').innerHTML = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"12\" cy=\"12\" r=\"10\"/><line x1=\"15\" y1=\"9\" x2=\"9\" y2=\"15\"/><line x1=\"9\" y1=\"9\" x2=\"15\" y2=\"15\"/></svg>';
                document.getElementById('typingText').textContent = 'Error';
                document.getElementById('typingSubtext').textContent = data.error or 'Unknown error';
                statusSpan.textContent = 'Error';
                setTimeout(() => typingIndicator.classList.remove('show', 'error'), 3000);"""

content = content.replace(old_error, new_error, 1)

# Update connection error handling
old_conn_error = """            addMessage('error', `Connection error: ${error.message}`);
            statusSpan.textContent = 'Disconnected';"""
new_conn_error = """            addMessage('error', `Connection error: ${error.message}`);
            typingIndicator.classList.remove('processing');
            typingIndicator.classList.add('error', 'show');
            document.getElementById('typingIcon').innerHTML = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"12\" cy=\"12\" r=\"10\"/><line x1=\"15\" y1=\"9\" x2=\"9\" y2=\"15\"/><line x1=\"9\" y1=\"9\" x2=\"15\" y2=\"15\"/></svg>';
            document.getElementById('typingText').textContent = 'Connection error';
            document.getElementById('typingSubtext').textContent = error.message or 'Unknown error';
            statusSpan.textContent = 'Disconnected';
            setTimeout(() => typingIndicator.classList.remove('show', 'error'), 3000);"""

content = content.replace(old_conn_error, new_conn_error)

# Update the Ready status after successful response
old_ready = """                statusSpan.textContent = 'Ready';
                
                // Refresh sessions list to show the new session
                loadRecentSessions();"""
new_ready = """                typingIndicator.classList.remove('processing');
                typingIndicator.classList.add('ready', 'show');
                document.getElementById('typingIcon').innerHTML = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><path d=\"M22 11.08V12a10 10 0 1 1-5.89-5.5\"/><polyline points=\"22 4 12 14.5 9 11.5\"/></svg>';
                document.getElementById('typingText').textContent = 'Ready';
                document.getElementById('typingSubtext').textContent = '';
                statusSpan.textContent = 'Ready';
                setTimeout(() => typingIndicator.classList.remove('show', 'ready'), 2000);
                
                // Refresh sessions list to show the new session
                loadRecentSessions();"""

content = content.replace(old_ready, new_ready)

with open('src/gateway/static/js/webchat.js', 'w') as f:
    f.write(content)

print('JavaScript updated with enhanced status indicators')
