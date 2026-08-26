function showAuthError(message) {
  const el = document.getElementById("authError");
  el.textContent = message;
  el.classList.add("show");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function submitAuthForm(url, body, submitBtn, defaultLabel) {
  submitBtn.disabled = true;
  submitBtn.textContent = "Please wait…";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Something went wrong.");
    }
    window.location.href = data.redirect || "/dashboard";
  } catch (err) {
    showAuthError(err.message);
    submitBtn.disabled = false;
    submitBtn.textContent = defaultLabel;
  }
}

const registerForm = document.getElementById("registerForm");
if (registerForm) {
  registerForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const submitBtn = document.getElementById("submitBtn");
    submitAuthForm(
      "/api/auth/register",
      {
        name: document.getElementById("name").value.trim(),
        email: document.getElementById("email").value.trim(),
        password: document.getElementById("password").value,
      },
      submitBtn,
      "Create Account"
    );
  });
}

const loginForm = document.getElementById("loginForm");
if (loginForm) {
  loginForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const submitBtn = document.getElementById("submitBtn");
    submitAuthForm(
      "/api/auth/login",
      {
        email: document.getElementById("email").value.trim(),
        password: document.getElementById("password").value,
      },
      submitBtn,
      "Log In"
    );
  });
}