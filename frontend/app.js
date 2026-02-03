// Auto-detect API URL based on current location
const API_URL = window.location.origin + "/api/v1";
let appState = {
    phone: null,
    currentStep: 1,
    authStatus: null,
    auditResult: null
};

// ==================== Helper Functions ====================

function showView(viewId) {
    ['view-phone', 'view-code', 'view-2fa', 'view-audit', 'view-telethon', 'view-complete'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
    const view = document.getElementById(viewId);
    if (view) view.classList.remove('hidden');
}

function setStep(stepNum) {
    appState.currentStep = stepNum;
    for (let i = 1; i <= 5; i++) {
        const el = document.getElementById(`step-${i}`);
        if (el) {
            el.classList.remove('active', 'completed');
            if (i < stepNum) el.classList.add('completed');
            else if (i === stepNum) el.classList.add('active');
        }
    }
}

function showStatus(message, type = 'info') {
    const el = document.getElementById('status-message');
    if (el) {
        el.className = `status-box ${type}`;
        el.innerHTML = message;
        el.classList.remove('hidden');
    }
}

function hideStatus() {
    const el = document.getElementById('status-message');
    if (el) el.classList.add('hidden');
}

function setLoading(buttonId, loading, originalText = null) {
    const btn = document.getElementById(buttonId);
    if (btn) {
        btn.disabled = loading;
        if (loading) {
            btn.innerHTML = '<span class="loader"></span> Processing...';
        } else if (originalText) {
            btn.innerHTML = originalText;
        }
    }
}

function formatPhone(phone) {
    let cleaned = phone.replace(/\s/g, '');
    if (!cleaned.startsWith('+')) {
        cleaned = '+' + cleaned;
    }
    return cleaned;
}


async function apiCall(endpoint, method = 'GET', data = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    
    if (data) {
        options.body = JSON.stringify(data);
    }
    
    try {
        const response = await fetch(`${API_URL}${endpoint}`, options);
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.detail || 'Unknown error');
        }
        
        return result;
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}


async function sendCode() {
    const phoneInput = document.getElementById('phone-input');
    const phone = formatPhone(phoneInput.value);
    
    if (!phone || phone.length < 10) {
        showStatus('Please enter a valid phone number', 'error');
        return;
    }
    
    setLoading('btn-send-code', true);
    hideStatus();
    
    try {
        const result = await apiCall('/auth/send-code', 'POST', { phone_number: phone });
        
        appState.phone = phone;
        
        if (result.status === 'code_sent') {
            showStatus('Verification code sent to your phone', 'success');
            setStep(2);
            showView('view-code');
        } else if (result.status === 'already_logged_in') {
            showStatus('Account already logged in, redirecting to audit...', 'success');
            setStep(3);
            showView('view-audit');
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-send-code', false, 'Send Verification Code');
    }
}


async function verifyCode() {
    const codeInput = document.getElementById('code-input');
    const code = codeInput.value.trim();
    
    if (!code || code.length < 5) {
        showStatus('Please enter verification code', 'error');
        return;
    }
    
    setLoading('btn-verify-code', true);
    hideStatus();
    
    try {
        const result = await apiCall('/auth/verify-code', 'POST', {
            phone_number: appState.phone,
            code: code
        });
        
        if (result.status === 'logged_in') {
            showStatus(`Welcome ${result.first_name || ''}! Successfully logged in`, 'success');
            setStep(3);
            showView('view-audit');
        } else if (result.status === '2fa_required') {
            showStatus('Two-factor authentication (2FA) required', 'info');
            showView('view-2fa');
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-verify-code', false, 'Verify Code');
    }
}


async function verify2FA() {
    const passwordInput = document.getElementById('2fa-input');
    const password = passwordInput.value;
    
    if (!password) {
        showStatus('الرجاء إدخال كلمة المرور', 'error');
        return;
    }
    
    setLoading('btn-verify-2fa', true);
    hideStatus();
    
    try {
        const result = await apiCall('/auth/verify-2fa', 'POST', {
            phone_number: appState.phone,
            password: password
        });
        
        if (result.status === 'logged_in') {
            showStatus('Successfully logged in!', 'success');
            setStep(3);
            showView('view-audit');
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-verify-2fa', false, 'Verify Password');
    }
}


async function runAudit() {
    setLoading('btn-run-audit', true);
    hideStatus();
    
    const auditLog = document.getElementById('audit-log');
    auditLog.innerHTML = '<div class="loading">Checking account...</div>';
    
    try {
        const result = await apiCall(`/account/audit/${encodeURIComponent(appState.phone)}`);
        
        appState.auditResult = result;
        
        // عرض النتائج
        let html = '';
        
        if (result.passed) {
            html = `
                <div class="audit-success">
                    <div class="icon">✓</div>
                    <h3>Account ready for transfer!</h3>
                    <p>All security checks passed</p>
                </div>
            `;
            showStatus('You can proceed to create Telethon session', 'success');
            document.getElementById('btn-proceed').classList.remove('hidden');
        } else {
            html = `
                <div class="audit-failed">
                    <div class="icon">✗</div>
                    <h3>${result.issues_count} issue(s) must be resolved</h3>
                </div>
                <ul class="issues-list">
            `;
            
            result.issues.forEach(issue => {
                html += `
                    <li class="issue-item severity-${issue.severity}">
                        <div class="issue-title">${issue.title}</div>
                        <div class="issue-desc">${issue.description}</div>
                        <div class="issue-action">💡 ${issue.action}</div>
                        ${issue.sessions ? `<div class="issue-sessions">${issue.sessions.join('<br>')}</div>` : ''}
                    </li>
                `;
            });
            
            html += '</ul>';
            html += `
                <div class="audit-actions">
                    <button onclick="terminateSessions()" class="btn-secondary">Terminate Other Sessions Automatically</button>
                </div>
            `;
            
            showStatus('Please resolve issues above then re-run audit', 'warning');
        }
        
        auditLog.innerHTML = html;
        
    } catch (error) {
        auditLog.innerHTML = `<div class="error">Audit error: ${error.message}</div>`;
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-run-audit', false, 'Start Security Audit');
    }
}

async function terminateSessions() {
    if (!confirm('Are you sure you want to terminate all other sessions?')) {
        return;
    }
    
    try {
        showStatus('Terminating sessions...', 'info');
        
        const result = await apiCall(`/account/terminate-sessions/${encodeURIComponent(appState.phone)}`, 'POST');
        
        showStatus(`Terminated ${result.terminated_count} session(s). Please re-run audit.`, 'success');
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    }
}

function proceedToTelethon() {
    setStep(4);
    showView('view-telethon');
}


async function createTelethonSession() {
    setLoading('btn-create-telethon', true);
    hideStatus();
    
    const telethonLog = document.getElementById('telethon-log');
    telethonLog.innerHTML = '<div class="loading">Creating Telethon session...</div>';
    
    try {
        const result = await apiCall('/account/create-telethon-session', 'POST', {
            phone_number: appState.phone
        });
        
        if (result.status === 'success') {
            telethonLog.innerHTML = `
                <div class="success">
                    <div class="icon">✓</div>
                    <h3>Telethon session created successfully!</h3>
                </div>
            `;
            showStatus('You can now finalize the process', 'success');
            document.getElementById('btn-finalize').classList.remove('hidden');
            
        } else if (result.status === 'already_logged_in') {
            telethonLog.innerHTML = `
                <div class="success">
                    <h3>Telethon session already exists</h3>
                </div>
            `;
            document.getElementById('btn-finalize').classList.remove('hidden');
            
        } else if (result.manual_code_required) {
            telethonLog.innerHTML = `
                <div class="manual-code">
                    <p>New code sent. Please enter code manually:</p>
                    <input type="text" id="telethon-code-input" placeholder="Enter code" maxlength="5">
                    <button onclick="verifyTelethonCode()" class="btn-primary">Verify</button>
                </div>
            `;
            
        } else if (result.status === '2fa_required') {
            telethonLog.innerHTML = `
                <div class="manual-code">
                    <p>2FA password required for Telethon session:</p>
                    <input type="password" id="telethon-2fa-input" placeholder="Password">
                    <button onclick="verifyTelethon2FA()" class="btn-primary">Verify</button>
                </div>
            `;
        }
        
    } catch (error) {
        telethonLog.innerHTML = `<div class="error">Error: ${error.message}</div>`;
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-create-telethon', false, 'Create Telethon Session');
    }
}

async function verifyTelethonCode() {
    const code = document.getElementById('telethon-code-input').value;
    
    if (!code) {
        showStatus('Please enter code', 'error');
        return;
    }
    
    try {
        const result = await apiCall('/account/verify-telethon-code', 'POST', {
            phone_number: appState.phone,
            code: code
        });
        
        if (result.status === 'success') {
            document.getElementById('telethon-log').innerHTML = `
                <div class="success">
                    <div class="icon">✓</div>
                    <h3>تم إنشاء جلسة Telethon بنجاح!</h3>
                </div>
            `;
            document.getElementById('btn-finalize').classList.remove('hidden');
            
        } else if (result.status === '2fa_required') {
            document.getElementById('telethon-log').innerHTML = `
                <div class="manual-code">
                    <p>2FA password required:</p>
                    <input type="password" id="telethon-2fa-input" placeholder="Password">
                    <button onclick="verifyTelethon2FA()" class="btn-primary">Verify</button>
                </div>
            `;
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    }
}

async function verifyTelethon2FA() {
    const password = document.getElementById('telethon-2fa-input').value;
    
    if (!password) {
        showStatus('الرجاء إدخال كلمة المرور', 'error');
        return;
    }
    
    try {
        const result = await apiCall('/account/verify-telethon-2fa', 'POST', {
            phone_number: appState.phone,
            password: password
        });
        
        if (result.status === 'success') {
            document.getElementById('telethon-log').innerHTML = `
                <div class="success">
                    <div class="icon">✓</div>
                    <h3>تم إنشاء جلسة Telethon بنجاح!</h3>
                </div>
            `;
            document.getElementById('btn-finalize').classList.remove('hidden');
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    }
}


async function finalizeAccount() {
    setLoading('btn-finalize', true);
    hideStatus();
    
    try {
        const result = await apiCall('/account/finalize', 'POST', {
            phone_number: appState.phone
        });
        
        if (result.status === 'completed') {
            setStep(5);
            showView('view-complete');
            
            document.getElementById('complete-message').innerHTML = `
                <div class="success-big">
                    <div class="icon"></div>
                    <h2>Process completed successfully!</h2>
                    <p>Pyrogram and Telethon sessions created for account</p>
                    <div class="session-info">
                        <p> Phone: ${appState.phone}</p>
                        <p> Pyrogram Session: ${result.pyrogram_session ? 'Available' : 'Unavailable'}</p>
                        <p> Telethon Session: ${result.telethon_session ? 'Available' : 'Unavailable'}</p>
                    </div>
                </div>
            `;
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-finalize', false, 'Finalize Process');
    }
}


document.addEventListener('DOMContentLoaded', () => {
    showView('view-phone');
    setStep(1);
    hideStatus();
});
