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
})();
