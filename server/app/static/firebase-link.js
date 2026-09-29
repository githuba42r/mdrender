/* Link a Firebase identity (Google / GitHub / phone) to an admin.
 *
 * The page must set window.__FIREBASE__ before loading this module. The browser
 * signs in with Firebase, then posts the ID token to /admins/link, which binds
 * the verified uid to the chosen admin. */
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  GithubAuthProvider,
  RecaptchaVerifier,
  signInWithPopup,
  signInWithPhoneNumber,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const app = initializeApp(window.__FIREBASE__);
const auth = getAuth(app);

const ERRORS = {
  "auth/operation-not-allowed": "This sign-in method isn't enabled — ask the operator to enable it in Firebase.",
  "auth/unauthorized-domain": "This domain is not authorised for sign-in.",
  "auth/popup-closed-by-user": "The sign-in popup was closed before finishing.",
  "auth/network-request-failed": "Network error — check your connection.",
};

function friendly(e) {
  const code = (e && e.code) || "";
  return ERRORS[code] || (e && e.message) || String(e);
}

async function postLink(idToken, adminId) {
  const resp = await fetch("/admins/link", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: idToken, admin_id: adminId }),
  });
  const body = await resp.json().catch(() => ({}));
  if (resp.ok) {
    window.location = "/admins?linked=1";
    return;
  }
  window.alert(body.error || `Link failed (${resp.status})`);
}

window.mdrenderLinkAdmin = async (adminId) => {
  try {
    const result = await signInWithPopup(auth, new GoogleAuthProvider());
    await postLink(await result.user.getIdToken(), adminId);
  } catch (e) {
    window.alert(friendly(e));
  }
};

window.mdrenderLinkAdminGithub = async (adminId) => {
  try {
    const result = await signInWithPopup(auth, new GithubAuthProvider());
    await postLink(await result.user.getIdToken(), adminId);
  } catch (e) {
    window.alert(friendly(e));
  }
};

window.mdrenderLinkAdminPhone = async (adminId) => {
  const phone = window.prompt("Phone number (e.g. +61400000000):");
  if (!phone) return;
  try {
    const verifier = new RecaptchaVerifier(auth, "recaptcha-container", { size: "invisible" });
    const confirmation = await signInWithPhoneNumber(auth, phone, verifier);
    const code = window.prompt("Enter the SMS code we just sent:");
    if (!code) return;
    const result = await confirmation.confirm(code);
    await postLink(await result.user.getIdToken(), adminId);
  } catch (e) {
    window.alert(friendly(e));
  }
};
