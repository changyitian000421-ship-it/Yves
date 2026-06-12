const form = document.querySelector("#login-form");
const phone = document.querySelector("#phone");
const password = document.querySelector("#password");
const code = document.querySelector("#code");
const sendCode = document.querySelector("#send-code");
const status = document.querySelector("#login-status");
const error = document.querySelector("#login-error");
let countdownTimer = null;

function normalizedPhone() {
  return phone.value.replace(/\D/g, "").replace(/^86(?=1\d{10}$)/, "");
}

function startCountdown(seconds = 60) {
  let remaining = seconds;
  sendCode.disabled = true;
  sendCode.textContent = `${remaining} 秒后重发`;
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(countdownTimer);
      sendCode.disabled = false;
      sendCode.textContent = "重新发送";
      return;
    }
    sendCode.textContent = `${remaining} 秒后重发`;
  }, 1000);
}

sendCode.addEventListener("click", async () => {
  error.textContent = "";
  status.textContent = "";
  const mobile = normalizedPhone();
  if (!/^1[3-9]\d{9}$/.test(mobile)) {
    error.textContent = "请输入正确的手机号码";
    return;
  }
  if (!password.value) {
    error.textContent = "请先输入访问密码";
    return;
  }
  sendCode.disabled = true;
  sendCode.textContent = "发送中...";
  try {
    const response = await fetch("/api/send-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: mobile, password: password.value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "验证码发送失败");
    if (data.devCode) {
      code.value = data.devCode;
      status.textContent = "本地测试验证码已自动填入";
    } else {
      status.textContent = "验证码已发送，5 分钟内有效";
    }
    startCountdown(60);
    code.focus();
  } catch (sendError) {
    error.textContent = sendError.message;
    sendCode.disabled = false;
    sendCode.textContent = "获取验证码";
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  button.textContent = "登录中...";
  error.textContent = "";

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: normalizedPhone(),
        password: password.value,
        code: code.value,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "登录失败");
    window.location.href = "/?v=sms-login-1";
  } catch (loginError) {
    error.textContent = loginError.message;
  } finally {
    button.disabled = false;
    button.textContent = "登录";
  }
});
