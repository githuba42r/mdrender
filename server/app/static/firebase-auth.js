/* Firebase Auth for the generic sign-in page.
 *
 * The page must set window.__FIREBASE__ (apiKey, authDomain, projectId, appId)
 * before loading this module. On success we exchange the Firebase ID token for
 * an MDRender session at POST /auth/oidc.
 *
 * Primary flow: one identifier field — email sends a magic link, a phone number
 * sends an SMS code. A password option and social buttons are secondary. */
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  GithubAuthProvider,
  RecaptchaVerifier,
  sendSignInLinkToEmail,
  isSignInWithEmailLink,
  signInWithEmailLink,
  signInWithPhoneNumber,
  signInWithPopup,
  signInWithEmailAndPassword,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const app = initializeApp(window.__FIREBASE__);
const auth = getAuth(app);
const EMAIL_KEY = "mdrender_email_for_signin";

function show(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function val(id) {
  return (document.getElementById(id)?.value || "").trim();
}

const ERRORS = {
  "auth/operation-not-allowed": "This sign-in method isn't enabled — ask the operator to enable it in Firebase.",
  "auth/unauthorized-domain": "This domain is not authorised for sign-in.",
  "auth/invalid-phone-number": "That phone number looks invalid.",
  "auth/invalid-email": "That email address looks invalid.",
  "auth/too-many-requests": "Too many attempts — please try again later.",
  "auth/popup-closed-by-user": "The sign-in popup was closed before finishing.",
  "auth/invalid-verification-code": "That code is not correct.",
  "auth/code-expired": "That code has expired — request a new one.",
  "auth/network-request-failed": "Network error — check your connection.",
};

function friendly(e) {
  const code = (e && e.code) || "";
  return ERRORS[code] || (e && e.message) || String(e);
}

async function exchange(user) {
  try {
    const idToken = await user.getIdToken();
    const resp = await fetch("/auth/oidc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: idToken }),
    });
    if (resp.ok) {
      const body = await resp.json().catch(() => ({}));
      const next = (window.__FIREBASE_NEXT__ || "").trim();
      const dest = next.startsWith("/") && !next.startsWith("//")
        ? next
        : (body.redirect || "/account");
      window.location = dest;
      return;
    }
    const body = await resp.json().catch(() => ({}));
    show("auth-error", body.error || `sign-in failed (${resp.status})`);
  } catch (e) {
    show("auth-error", friendly(e));
  }
}

async function popup(provider) {
  try {
    const result = await signInWithPopup(auth, provider);
    await exchange(result.user);
  } catch (e) {
    show("auth-error", friendly(e));
  }
}

window.mdrenderGoogle = () => popup(new GoogleAuthProvider());
window.mdrenderGithub = () => popup(new GithubAuthProvider());

async function sendMagicLink(email) {
  try {
    await sendSignInLinkToEmail(auth, email, {
      url: window.location.origin + window.location.pathname,
      handleCodeInApp: true,
    });
    localStorage.setItem(EMAIL_KEY, email);
    const what = window.__FIREBASE_ACTION__ === "signup" ? "Create-account link" : "Sign-in link";
    show("auth-message", `${what} sent — open it on this device to finish.`);
  } catch (e) {
    show("auth-error", friendly(e));
  }
}

async function sendPhoneCode(phone) {
  try {
    const container = document.getElementById("recaptcha-container");
    const verifier = new RecaptchaVerifier(auth, container, { size: "invisible" });
    const confirmation = await signInWithPhoneNumber(auth, phone, verifier);
    const code = window.prompt("Enter the SMS code we just sent:");
    if (code) {
      const result = await confirmation.confirm(code);
      await exchange(result.user);
    }
  } catch (e) {
    show("auth-error", friendly(e));
  }
}

window.mdrenderSend = async () => {
  const identifier = val("identifier");
  if (!identifier) {
    show("auth-error", "Enter your email or phone number first.");
    return;
  }
  show("auth-error", "");
  if (identifier.includes("@")) {
    await sendMagicLink(identifier);
  } else {
    await sendPhoneCode(identifier);
  }
};

window.mdrenderPassword = async () => {
  const email = val("pw-email") || val("identifier");
  const password = document.getElementById("pw-password")?.value || "";
  if (!email || !password) {
    show("auth-error", "Enter your email and password.");
    return;
  }
  try {
    const result = await signInWithEmailAndPassword(auth, email, password);
    await exchange(result.user);
  } catch (e) {
    show("auth-error", friendly(e));
  }
};

window.mdrenderTogglePassword = () => {
  const idp = document.getElementById("auth-identifier-panel");
  const pwp = document.getElementById("auth-password-panel");
  if (!idp || !pwp) return;
  const showPassword = pwp.hidden;
  pwp.hidden = !showPassword;
  idp.hidden = showPassword;
  const email = val("identifier");
  if (showPassword && email.includes("@")) {
    const field = document.getElementById("pw-email");
    if (field && !field.value) field.value = email;
  }
};

// Complete a magic-link sign-in if we arrived via one.
if (isSignInWithEmailLink(auth, window.location.href)) {
  let email = localStorage.getItem(EMAIL_KEY);
  if (!email) email = window.prompt("Confirm the email you used to sign in:");
  if (email) {
    signInWithEmailLink(auth, email, window.location.href)
      .then((result) => {
        localStorage.removeItem(EMAIL_KEY);
        return exchange(result.user);
      })
      .catch((e) => show("auth-error", friendly(e)));
  }
}
