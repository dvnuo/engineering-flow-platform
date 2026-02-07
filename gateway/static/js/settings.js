// Settings Page JavaScript

let currentProvider = 'openai';

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    loadSettings();
    updateProviderUI();
    loadStatus();
    
    // Temperature slider
    document.getElementById('temperature').addEventListener('input', (e) => {
        document.getElementById('temperature-value').textContent = e.target.value;
    });
});

function updateProviderUI() {
    const provider = document.getElementById('provider').value;
    currentProvider = provider;
    
    const modelSelect = document.getElementById('model');
    const ollamaSection = document.getElementById('ollama-models');
    
    // Clear existing options
    modelSelect.innerHTML = '';
    
    const models = {
        openai: [
            { value: 'gpt-4o', label: 'GPT-4o (Latest)' },
            { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
            { value: 'gpt-4-turbo', label: 'GPT-4 Turbo' },
            { value: 'gpt-4', label: 'GPT-4' },
            { value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo' }
        ],
        github_copilot: [
            { value: 'gpt-4', label: 'GPT-4' },
            { value: 'gpt-4-turbo', label: 'GPT-4 Turbo' }
        ],
        claude: [
            { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4' },
            { value: 'claude-opus-4-20250514', label: 'Claude Opus 4' },
            { value: 'claude-haiku-4-20250514', label: 'Claude Haiku 4' },
            { value: 'claude-3-5-sonnet', label: 'Claude 3.5 Sonnet' },
            { value: 'claude-3-opus', label: 'Claude 3 Opus' }
        ],
        ollama: [
            { value: 'llama3', label: 'Llama 3' },
            { value: 'llama3.2', label: 'Llama 3.2' },
            { value: 'mistral', label: 'Mistral' },
            { value: 'codellama', label: 'CodeLlama' },
            { value: 'qwen2.5', label: 'Qwen 2.5' }
        ]
    };
    
    const providerModels = models[provider] || models.openai;
    providerModels.forEach(m => {
        const option = document.createElement('option');
        option.value = m.value;
        option.textContent = m.label;
        modelSelect.appendChild(option);
    });
    
    // Show/hide Ollama section
    ollamaSection.classList.toggle('hidden', provider !== 'ollama');
    
    if (provider === 'ollama') {
        loadOllamaModels();
    }
}

async function loadOllamaModels() {
    const modelList = document.getElementById('model-list');
    modelList.innerHTML = '<p>Loading models...</p>';
    
    try {
        const response = await fetch('/api/settings/ollama/models');
        const data = await response.json();
        
        if (data.status === 'healthy') {
            modelList.innerHTML = data.models.map(m => 
                `<span class="model-tag" onclick="selectModel('${m}')">${m}</span>`
            ).join('');
        } else {
            modelList.innerHTML = '<p class="status-unhealthy">Ollama not running</p>';
        }
    } catch (e) {
        modelList.innerHTML = '<p class="status-unhealthy">Failed to connect</p>';
    }
}

function selectModel(model) {
    const modelSelect = document.getElementById('model');
    modelSelect.value = model;
}

async function loadSettings() {
    try {
        const response = await fetch('/api/settings');
        const config = await response.json();
        
        if (config.llm) {
            document.getElementById('provider').value = config.llm.provider || 'openai';
            document.getElementById('model').value = config.llm.model || 'gpt-3.5-turbo';
            document.getElementById('temperature').value = config.llm.temperature || 0.7;
            document.getElementById('temperature-value').textContent = config.llm.temperature || 0.7;
            document.getElementById('max_tokens').value = config.llm.max_tokens || 1000;
        }
    } catch (e) {
        console.error('Failed to load settings:', e);
    }
}

async function loadStatus() {
    const statusBox = document.getElementById('status-info');
    
    try {
        const response = await fetch('/health');
        const data = await response.json();
        
        // Get provider info
        const providerResponse = await fetch('/api/settings/providers');
        const providers = await providerResponse.json();
        
        let html = `<p class="status-healthy">✅ Service: Healthy</p>`;
        html += `<p>📅 ${new Date().toLocaleString()}</p>`;
        
        html += `<p><strong>Providers:</strong></p>`;
        for (const [name, info] of Object.entries(providers)) {
            const status = info.models?.length > 0 ? '✅' : '⚠️';
            html += `<p>${status} ${name}: ${info.default_model || 'N/A'}</p>`;
        }
        
        statusBox.innerHTML = html;
    } catch (e) {
        statusBox.innerHTML = `<p class="status-unhealthy">❌ Failed to load status</p>`;
    }
}

async function saveSettings() {
    const settings = {
        llm: {
            provider: document.getElementById('provider').value,
            model: document.getElementById('model').value,
            temperature: parseFloat(document.getElementById('temperature').value),
            max_tokens: parseInt(document.getElementById('max_tokens').value)
        }
    };
    
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        if (response.ok) {
            alert('Settings saved successfully! Restart required to apply.');
        } else {
            alert('Failed to save settings');
        }
    } catch (e) {
        alert('Error saving settings: ' + e.message);
    }
}

async function restartService() {
    if (!confirm('Restart the service? This will disconnect all users.')) {
        return;
    }
    
    try {
        const response = await fetch('/api/restart', { method: 'POST' });
        if (response.ok) {
            alert('Service restarting...');
            setTimeout(loadStatus, 5000);
        } else {
            alert('Failed to restart');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function exportConfig() {
    const config = {
        llm: {
            provider: document.getElementById('provider').value,
            model: document.getElementById('model').value,
            temperature: parseFloat(document.getElementById('temperature').value),
            max_tokens: parseInt(document.getElementById('max_tokens').value)
        }
    };
    
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'efp-config.json';
    a.click();
    URL.revokeObjectURL(url);
}

async function pullModel() {
    const model = prompt('Enter model name to pull (e.g., llama3, mistral):');
    if (!model) return;
    
    try {
        const response = await fetch('/api/settings/ollama/pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        
        const data = await response.json();
        if (data.status === 'success') {
            alert(`Model ${model} pulled successfully!`);
            loadOllamaModels();
        } else {
            alert('Failed to pull model: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}
