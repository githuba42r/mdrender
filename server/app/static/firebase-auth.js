/* Firebase Auth for the account portal: social login + email magic link.
 *
 * The page must set window.__FIREBASE__ (apiKey, authDomain, projectId, appId)
 * before loading this module. On success we exchange the Firebase ID token for
 * an MDRender account session at POST /auth/oidc. */
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
  signInWithRedirect,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const app = initializeApp(window.__FIREBASE__);
const auth = getAuth(app);
const EMAIL_KEY = "mdrender_email_for_signin";

function show(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
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
      window.location = "/account";
      return;
    }
    const body = await resp.json().catch(() => ({}));
    show("auth-error", body.error || `sign-in failed (${resp.status})`);
  } catch (e) {
    show("auth-error", e.message || String(e));
  }
}

async function popup(provider) {
  try {
    const result = await signInWithPopup(auth, provider);
    await exchange(result.user);
  } catch (e) {
    show("auth-error", e.message || String(e));
  }
}

window.mdrenderGoogle = () => popup(new GoogleAuthProvider());
window.mdrenderGithub = () => popup(new GithubAuthProvider());

window.mdrenderPhone = async () => {
  const phone = (document.getElementById("phone-number")?.value || "").trim();
  if (!phone) {
    show("auth-error", "Enter your phone number first.");
    return;
  }
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
    show("auth-error", e.message || String(e));
  }
};

window.mdrenderMagic = async () => {
  const email = (document.getElementById("magic-email")?.value || "").trim();
  if (!email) {
    show("auth-error", "Enter your email first.");
    return;
  }
  try {
    await sendSignInLinkToEmail(auth, email, {
      url: window.location.origin + window.location.pathname,
      handleCodeInApp: true,
    });
    localStorage.setItem(EMAIL_KEY, email);
    show("auth-message", "Magic link sent — open it on this device to sign in.");
  } catch (e) {
    show("auth-error", e.message || String(e));
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
      .catch((e) => show("auth-error", e.message || String(e)));
  }
}
