// Auto-detect API URL based on current location
const API_URL = window.location.origin + "/api/v1";
let currentAccountId = null;
let currentPhone = null;

// Helper: Show/Hide Logic
function showView(viewId) {
    ['view-init', 'view-otp', 'view-audit', 'view-finalize', 'view-success'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
    document.getElementById(viewId).classList.remove('hidden');
}

function setStep(stepNum) {
    for (let i = 1; i <= 4; i++) {
        const el = document.getElementById(`s${i}`);
        if (i <= stepNum) el.classList.add('active');
        else el.classList.remove('active');
    }
}

function setStatus(msg, type = 'info') {
    const el = document.getElementById('global-status');
    el.classList.remove('hidden', 'success', 'error', 'info');
    el.classList.add(type);
    el.innerHTML = msg;
}

// Key Generation for Finalize Step
async function generateRSAKeyPair() {
    const keyPair = await window.crypto.subtle.generateKey(
        {
            name: "RSA-OAEP",
            modulusLength: 2048,
            publicExponent: new Uint8Array([1, 0, 1]),
            hash: "SHA-256"
        },
        true,
        ["encrypt", "decrypt"]
    );

    const exported = await window.crypto.subtle.exportKey(
        "spki",
        keyPair.publicKey
    );

    // Convert to PEM
    const exportedAsBase64 = window.btoa(String.fromCharCode(...new Uint8Array(exported)));
    const pem = `-----BEGIN PUBLIC KEY-----\n${exportedAsBase64}\n-----END PUBLIC KEY-----`;

    return pem;
}

// --- Steps ---

let authState = "OTP"; // OTP, PASSWORD, REGISTER

async function startAuth() {
    const phone = document.getElementById('phone').value;
    if (!phone) return setStatus("الرجاء إدخال رقم الهاتف", "error");

    const btn = document.getElementById('btn-init');
    btn.disabled = true;
    btn.innerHTML = '<span class="loader"></span> جاري الإرسال...';

    try {
        const res = await fetch(`${API_URL}/auth/init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone_number: phone })
        });
        const data = await res.json();

        if (res.ok) {
            currentPhone = phone;
            setStep(2);
            showView('view-otp');

            // Reset to OTP state
            authState = "OTP";
            document.getElementById('group-otp').classList.remove('hidden');
            document.getElementById('group-password').classList.add('hidden');
            document.getElementById('group-register').classList.add('hidden');
            document.getElementById('btn-auth-action').innerHTML = "تحقق من الكود";

            setStatus("");
        } else {
            setStatus("خطأ: " + JSON.stringify(data), "error");
        }
    } catch (e) {
        setStatus("خطأ في الاتصال: " + e.message, "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'ابدأ المصادقة';
    }
}

async function handleAuthAction() {
    const btn = document.getElementById('btn-auth-action');
    btn.disabled = true;
    btn.innerHTML = '<span class="loader"></span> جاري المعالجة...';

    try {
        if (authState === "OTP") {
            await submitOtp();
        } else if (authState === "PASSWORD") {
            await submitPassword();
        } else if (authState === "REGISTER") {
            await submitRegister();
        }
    } catch (e) {
        setStatus("حدث خطأ غير متوقع", "error");
        console.error(e);
    } finally {
        btn.disabled = false;
        // Restore button text based on state
        if (authState === "OTP") btn.innerHTML = "تحقق من الكود";
        else if (authState === "PASSWORD") btn.innerHTML = "أرسل كلمة المرور";
        else if (authState === "REGISTER") btn.innerHTML = "تسجيل";
    }
}

async function submitOtp() {
    const otp = document.getElementById('otp').value;
    if (!otp) return setStatus("أدخل كود التحقق", "error");

    const res = await fetch(`${API_URL}/auth/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            phone_number: currentPhone,
            otp_code: otp,
            password_2fa: null // Deprecated field in new flow, sending null
        })
    });

    await handleAuthResponse(res);
}

async function submitPassword() {
    const pwd = document.getElementById('password-2fa').value;
    if (!pwd) return setStatus("أدخل كلمة المرور", "error");

    const res = await fetch(`${API_URL}/auth/password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            phone_number: currentPhone,
            password: pwd
        })
    });

    await handleAuthResponse(res);
}

async function submitRegister() {
    const first = document.getElementById('reg-first-name').value;
    const last = document.getElementById('reg-last-name').value;
    if (!first) return setStatus("الاسم الأول مطلوب", "error");

    const res = await fetch(`${API_URL}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            phone_number: currentPhone,
            first_name: first,
            last_name: last
        })
    });

    await handleAuthResponse(res);
}

async function handleAuthResponse(res) {
    const data = await res.json();

    // Check for our new status signals from the backend (which would come from polling in a real app)
    // For this simplified demo/mock, we assume the backend returns the immediate next step or success.

    // IMPORTANT: The current Verification endpoint /auth/verify returns {status: "authenticated"} on success 
    // OR waits. Since we don't have long-polling here implemented fully, we will simulate the check loop
    // or rely on the backend response.

    // In our manual test, we saw that the *Worker* puts status in the queue. 
    // The *API endpoint* `verify_auth` currently just returns success mocked or waits.
    // To make this work properly with the new flow, we need to update the frontend to poll for status 
    // or the backend verify endpoint needs to return the specific status.

    // Let's assume for now that if correct, we proceed. 
    // BUT, since we implemented the worker logic, we need to know if it's asking for Password/Register.

    // For this prototype, if the verify endpoint returns 200 OK, we assume success logged in.
    // If it fails, we show error.
    // To support 2FA properly without Polling endpoint, we need to guess or try.

    if (res.ok) {
        // Mocking the next step detection since we didn't add a poller endpoint
        // In a real Tdlib app, you call GetAuthorizationState.

        // For now, if we get "authenticated", we go to next view.
        if (data.status === "authenticated" || data.status === "LOGGED_IN") {
            currentAccountId = data.account_id || 12345; // Mock ID if missing
            setStep(3);
            showView('view-audit');
            setStatus("تم تسجيل الدخول بنجاح!");
        } else if (data.status === "WAITING_PASSWORD") {
            authState = "PASSWORD";
            document.getElementById('group-otp').classList.add('hidden');
            document.getElementById('group-password').classList.remove('hidden');
            setStatus("مطلوب التحقق بخطوتين (2FA)", "info");
        } else if (data.status === "WAITING_REGISTRATION") {
            authState = "REGISTER";
            document.getElementById('group-otp').classList.add('hidden');
            document.getElementById('group-register').classList.remove('hidden');
            setStatus("حساب جديد، يرجى التسجيل", "info");
        } else {
            // Fallback for demo
            setStatus("Status: " + data.status);
        }
    } else {
        setStatus("فشل: " + (data.detail || "Unknown error"), "error");
    }
}

async function runAudit() {
    if (!currentAccountId) return;

    const btn = document.getElementById('btn-audit');
    btn.disabled = true;
    btn.innerHTML = '<span class="loader"></span> Auditing...';

    const logBox = document.getElementById('audit-log');
    logBox.innerHTML = "Scanning active sessions...<br>Checking passkeys...<br>Verifying recovery emails...";

    try {
        const res = await fetch(`${API_URL}/account/audit/${currentAccountId}`);
        const data = await res.json();

        // Check mocked simulation logic or real logic
        if (res.ok && data.passed === true) {
            setStep(4);
            showView('view-finalize');
            setStatus("");
        } else {
            // Failed
            logBox.className = "status-box error";
            const details = data.details || [];
            const listItems = details.map(d => `<li style="margin-bottom: 5px;">${d}</li>`).join('');
            logBox.innerHTML = `
                <div style="text-align: center; margin-bottom: 10px;"><strong>Audit Failed (${details.length} issues found)</strong></div>
                <ul style="text-align: left; padding-left: 20px; margin: 0;">${listItems}</ul>
                <div style="margin-top: 10px; font-size: 0.9em;">Please fix these issues in your Telegram App and try again.</div>
            `;
            document.getElementById('btn-audit').innerHTML = "Retry Audit";
        }
    } catch (e) {
        setStatus("Error running audit", "error");
    } finally {
        btn.disabled = false;
        if (document.getElementById('view-finalize').classList.contains('hidden')) {
            btn.innerHTML = 'Run Deep Security Audit';
        }
    }
}

async function finalizeAccount() {
    const btn = document.getElementById('btn-finalize');
    btn.disabled = true;
    btn.innerHTML = '<span class="loader"></span> Locking Down...';

    try {
        // 1. Generate Key
        const pubKey = await generateRSAKeyPair();

        // 2. Send
        const res = await fetch(`${API_URL}/account/finalize/${currentAccountId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ public_key_pem: pubKey })
        });

        const data = await res.json();
        if (res.ok) {
            showView('view-success');
        } else {
            setStatus("Finalize Failed: " + data.detail, "error");
        }

    } catch (e) {
        setStatus("Crypto Error: " + e.message, "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Lockdown & Finalize';
    }
}
