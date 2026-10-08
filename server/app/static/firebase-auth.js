/* Firebase Auth for the generic sign-in page.
 *
 * The page must set window.__FIREBASE__ (apiKey, authDomain, projectId, appId)
 * before loading this module. On success we exchange the Firebase ID token for
 * an MDRender session at POST /auth/oidc - or, when __FIREBASE_ACTION__ is
 * "link", bind it to the signed-in account at POST /account/link.
 *
 * Primary flow: one identifier field — email sends a magic link, a phone number
 * sends an SMS code. A password option and social buttons are secondary. */
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  GithubAuthProvider,
  EmailAuthProvider,
  RecaptchaVerifier,
  sendSignInLinkToEmail,
  sendEmailVerification,
  isSignInWithEmailLink,
  signInWithEmailLink,
  signInWithPhoneNumber,
  signInWithPopup,
  signInWithEmailAndPassword,
  linkWithPopup,
  linkWithCredential,
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
  "auth/billing-not-enabled": "Phone sign-in needs the Firebase project on the Blaze (pay-as-you-go) plan.",
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

// The completion spinner shown when we arrive from an email magic link (see
// the inline script in _firebase_auth.html, which raises it before this
// module even loads).
function busy(on) {
  const el = document.getElementById("auth-busy");
  if (el) el.hidden = !on;
}

// Link mode: the visitor is already signed in locally and is binding a
// Firebase identity to that account, so the token goes to /account/link and
// success returns to the profile page instead of opening a session.
function linkMode() {
  return window.__FIREBASE_ACTION__ === "link";
}

async function exchange(user) {
  try {
    const idToken = await user.getIdToken();
    const params = new URLSearchParams(window.location.search);
    const affiliate = (
      (document.getElementById("affiliate-code")?.value || "")
      || params.get("affiliate") || params.get("affiliate_code") || "").trim();
    const payload = { id_token: idToken };
    if (affiliate) payload.affiliate_code = affiliate;
    const resp = await fetch(linkMode() ? "/account/link" : "/auth/oidc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      const body = await resp.json().catch(() => ({}));
      if (linkMode()) {
        window.location = "/account/profile?linked=1";
        return;
      }
      const next = (window.__FIREBASE_NEXT__ || "").trim();
      const dest = next.startsWith("/") && !next.startsWith("//")
        ? next
        : (body.redirect || "/account");
      window.location = dest;
      return;
    }
    const body = await resp.json().catch(() => ({}));
    busy(false);
    show("auth-error", body.error || `sign-in failed (${resp.status})`);
  } catch (e) {
    busy(false);
    show("auth-error", friendly(e));
  }
}

// Every account needs a verified email, but a phone-only Firebase user has
// none. Rather than exchange, ask them to attach one first (see below).
// Linking is different: the account already has an email of its own, so a
// phone-only identity can bind as it stands.
async function afterSignIn(user) {
  if (user && !user.email && !linkMode()) {
    busy(false);
    showEmailLinkPanel();
    return;
  }
  await exchange(user);
}

function showEmailLinkPanel() {
  const idp = document.getElementById("auth-identifier-panel");
  const pwp = document.getElementById("auth-password-panel");
  const elp = document.getElementById("auth-email-link-panel");
  if (idp) idp.hidden = true;
  if (pwp) pwp.hidden = true;
  if (elp) elp.hidden = false;
  show("auth-message", "Add an email to finish creating your account.");
}

async function popup(provider) {
  try {
    const result = await signInWithPopup(auth, provider);
    await afterSignIn(result.user);
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
      await afterSignIn(result.user);
    }
  } catch (e) {
    show("auth-error", friendly(e));
  }
}

window.mdrenderSend = async () => {
  const identifier = val("identifier");
  const providers = window.__FIREBASE_PROVIDERS__ || [];
  const phoneOn = providers.includes("phone");
  if (!identifier) {
    show("auth-error", phoneOn
      ? "Enter your email or phone number first."
      : "Enter your email first.");
    return;
  }
  show("auth-error", "");
  if (identifier.includes("@")) {
    if (!providers.includes("email_link")) {
      show("auth-error", "Email sign-in isn't enabled.");
      return;
    }
    await sendMagicLink(identifier);
  } else {
    if (!phoneOn) {
      show("auth-error", "That doesn't look like an email address.");
      return;
    }
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
    await afterSignIn(result.user);
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

async function linkProvider(provider) {
  if (!auth.currentUser) {
    show("auth-error", "Sign in again to add an email.");
    return;
  }
  try {
    const result = await linkWithPopup(auth.currentUser, provider);
    await exchange(result.user);
  } catch (e) {
    show("auth-error", friendly(e));
  }
}

window.mdrenderAttachGoogle = () => linkProvider(new GoogleAuthProvider());
window.mdrenderAttachGithub = () => linkProvider(new GithubAuthProvider());

window.mdrenderAttachEmail = async () => {
  const user = auth.currentUser;
  const email = val("attach-email");
  const password = document.getElementById("attach-password")?.value || "";
  if (!user) {
    show("auth-error", "Sign in again to add an email.");
    return;
  }
  if (!email || password.length < 6) {
    show("auth-error", "Enter an email and a password of 6+ characters.");
    return;
  }
  try {
    const result = await linkWithCredential(
      user, EmailAuthProvider.credential(email, password));
    await sendEmailVerification(result.user);
    show("auth-message", "Email attached. Check your inbox to verify it, then sign in again.");
  } catch (e) {
    show("auth-error", friendly(e));
  }
};

// Complete a magic-link sign-in if we arrived via one. The inline script in
// _firebase_auth.html has already swapped the form for the completion
// spinner; here we finish the exchange — and bring the form back (with the
// error shown on the Magic link tab) if anything goes wrong.
const arriving = isSignInWithEmailLink(auth, window.location.href);
if (!arriving) {
  busy(false); // not a sign-in code — make sure no spinner is stuck up
} else {
  let email = localStorage.getItem(EMAIL_KEY);
  if (!email) {
    busy(false); // the prompt needs the page; re-raise once we have an email
    email = window.prompt("Confirm the email you used to sign in:");
  }
  if (email) {
    busy(true);
    signInWithEmailLink(auth, email, window.location.href)
      .then((result) => {
        localStorage.removeItem(EMAIL_KEY);
        return afterSignIn(result.user);
      })
      .catch((e) => {
        busy(false);
        if (window.mdrenderTab) window.mdrenderTab("link");
        show("auth-error", friendly(e));
      });
  } else {
    show("auth-message", "Sign-in cancelled — enter your email to send a new link.");
  }
}
