/* MDRender Cloud Push Server — progressive niceties.
   Click-to-copy and an accessible confirm dialog, so templates carry no inline
   JS and destructive actions get an HTML prompt instead of window.confirm. */
(function () {
  "use strict";

  // ---- Click to copy ------------------------------------------------------

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // http origins (e.g. localhost over plain http) have no async clipboard.
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy") ? resolve() : reject(new Error("copy failed"));
      } catch (err) {
        reject(err);
      } finally {
        document.body.removeChild(ta);
      }
    });
  }

  function feedback(el) {
    if (el.tagName === "BUTTON") {
      if (!el.hasAttribute("data-label")) {
        el.setAttribute("data-label", el.textContent);
      }
      el.textContent = "Copied";
      el.classList.add("copied");
      clearTimeout(el._copyTimer);
      el._copyTimer = setTimeout(function () {
        el.textContent = el.getAttribute("data-label");
        el.classList.remove("copied");
      }, 1400);
    } else {
      el.classList.add("copied");
      clearTimeout(el._copyTimer);
      el._copyTimer = setTimeout(function () {
        el.classList.remove("copied");
      }, 1400);
    }
  }

  function textFor(trigger) {
    if (trigger.hasAttribute("data-copy")) {
      return trigger.textContent;
    }
    if (trigger.hasAttribute("data-copy-sibling")) {
      var wrap = trigger.closest(".copy-wrap");
      var src = wrap && wrap.querySelector("pre, code");
      return src ? src.textContent : "";
    }
    var ref = document.querySelector(trigger.getAttribute("data-copy-from"));
    return ref ? ref.textContent : "";
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest(
      "[data-copy], [data-copy-sibling], [data-copy-from]");
    if (!trigger) return;
    event.preventDefault();
    copyText(textFor(trigger).trim()).then(function () {
      feedback(trigger);
    }).catch(function () {});
  });

  // ---- Confirm dialog -----------------------------------------------------

  var dialog = document.getElementById("confirm-dialog");
  if (!dialog) return;

  var messageEl = document.getElementById("confirm-message");
  var okButton = document.getElementById("confirm-ok");
  var pendingForm = null;

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var message = form.getAttribute("data-confirm");
    if (!message) return;
    event.preventDefault();
    pendingForm = form;
    messageEl.textContent = message;
    okButton.textContent = form.getAttribute("data-confirm-ok") || "Confirm";
    dialog.showModal();
  });

  dialog.addEventListener("close", function () {
    if (dialog.returnValue === "confirm" && pendingForm) {
      // form.submit() sends it without re-triggering this handler.
      pendingForm.submit();
    }
    pendingForm = null;
  });

  // ---- Generic modals (e.g. add / edit admin) -----------------------------

  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-open-dialog]");
    if (opener) {
      var target = document.querySelector(opener.getAttribute("data-open-dialog"));
      if (target) target.showModal();
      return;
    }
    var closer = event.target.closest("[data-close-dialog]");
    if (closer) {
      var owner = closer.closest("dialog");
      if (owner) owner.close();
      return;
    }
    var edit = event.target.closest("[data-edit-admin]");
    if (edit) {
      var editDialog = document.getElementById("edit-admin-dialog");
      if (!editDialog) return;
      editDialog.querySelector("form").setAttribute(
        "action", "/admins/" + edit.getAttribute("data-id") + "/update");
      editDialog.querySelector('[name="name"]').value = edit.getAttribute("data-name") || "";
      editDialog.querySelector('[name="email"]').value = edit.getAttribute("data-email") || "";
      editDialog.querySelector('[name="password"]').value = "";
      editDialog.querySelector(".edit-username").textContent =
        edit.getAttribute("data-username") || "";
      editDialog.showModal();
      return;
    }
    var editAccount = event.target.closest("[data-edit-account]");
    if (editAccount) {
      var accountDialog = document.getElementById("edit-account-dialog");
      if (!accountDialog) return;
      accountDialog.querySelector("form").setAttribute(
        "action", "/accounts/" + editAccount.getAttribute("data-id") + "/update");
      accountDialog.querySelector('[name="email"]').value =
        editAccount.getAttribute("data-email") || "";
      accountDialog.querySelector('[name="name"]').value =
        editAccount.getAttribute("data-name") || "";
      accountDialog.querySelector('[name="password"]').value = "";
      accountDialog.showModal();
    }
  });
})();
