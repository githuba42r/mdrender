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

  // ---- Password visibility toggles ---------------------------------------

  var EYE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/>' +
    '<circle cx="12" cy="12" r="3"/></svg>';

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    var field = input.closest(".field") || input.parentElement;
    if (!field || field.querySelector(".pw-toggle")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    btn.setAttribute("aria-label", "Show password");
    btn.innerHTML = EYE;
    btn.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
    field.appendChild(btn);
  });

  // ---- Disable submit until required fields are complete ------------------

  document.querySelectorAll("form[data-validate]").forEach(function (form) {
    var submit = form.querySelector('button[type="submit"]');
    function check() {
      var ok = true;
      form.querySelectorAll("input[required]").forEach(function (input) {
        // Required fields hidden for this variant (e.g. account-only plan
        // fields when the type is a server plan) are not demanded.
        if (input.closest("[hidden]")) return;
        if (!input.value.trim()) ok = false;
      });
      var pw = form.querySelector('input[name="password"]');
      var confirmPw = form.querySelector('input[name="confirm_password"]');
      if (pw && confirmPw && pw.value !== confirmPw.value) ok = false;
      if (submit) submit.disabled = !ok;
    }
    form.addEventListener("input", check);
    form.addEventListener("change", check);
    form._validate = check;
    check();
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
      return;
    }
    var newPlan = event.target.closest("[data-new-plan]");
    if (newPlan) {
      var newPlanDialog = document.getElementById("plan-dialog");
      if (!newPlanDialog) return;
      newPlanDialog.querySelector("form").setAttribute("action", "/billing/plans");
      newPlanDialog.querySelector(".plan-title").textContent = "New plan";
      newPlanDialog.querySelector('[name="name"]').value = "";
      newPlanDialog.querySelector('[name="scope"]').value = "account";
      ["price", "included_messages", "message_cost", "storage_cost",
       "storage_grace_days", "max_messages_per_month", "pending_mb",
       "pending_expiry_hours"].forEach(function (f) {
        newPlanDialog.querySelector('[name="' + f + '"]').value = "";
      });
      newPlanDialog._applyScope && newPlanDialog._applyScope();
      newPlanDialog.querySelector("form")._validate &&
        newPlanDialog.querySelector("form")._validate();
      newPlanDialog.showModal();
      return;
    }
    var editPlan = event.target.closest("[data-edit-plan]");
    if (editPlan) {
      var planDialog = document.getElementById("plan-dialog");
      if (!planDialog) return;
      planDialog.querySelector("form").setAttribute(
        "action", "/billing/plans/" + editPlan.getAttribute("data-id"));
      planDialog.querySelector(".plan-title").textContent = "Edit plan";
      planDialog.querySelector('[name="name"]').value = editPlan.getAttribute("data-name") || "";
      planDialog.querySelector('[name="scope"]').value = editPlan.getAttribute("data-scope") || "account";
      planDialog.querySelector('[name="price"]').value = editPlan.getAttribute("data-price") || "0";
      planDialog.querySelector('[name="included_messages"]').value = editPlan.getAttribute("data-included-messages") || "0";
      planDialog.querySelector('[name="message_cost"]').value = editPlan.getAttribute("data-message-cost") || "0";
      planDialog.querySelector('[name="storage_cost"]').value = editPlan.getAttribute("data-storage-cost") || "0";
      planDialog.querySelector('[name="storage_grace_days"]').value = editPlan.getAttribute("data-storage-grace") || "0";
      planDialog.querySelector('[name="max_messages_per_month"]').value = editPlan.getAttribute("data-max-messages") || "0";
      planDialog.querySelector('[name="pending_mb"]').value = editPlan.getAttribute("data-pending-mb") || "0";
      planDialog.querySelector('[name="pending_expiry_hours"]').value = editPlan.getAttribute("data-pending-expiry") || "0";
      planDialog._applyScope && planDialog._applyScope();
      planDialog.querySelector("form")._validate &&
        planDialog.querySelector("form")._validate();
      planDialog.showModal();
      return;
    }
    var addCredit = event.target.closest("[data-add-credit]");
    if (addCredit) {
      var creditDialog = document.getElementById("credit-dialog");
      if (!creditDialog) return;
      creditDialog.querySelector("form").setAttribute("action", "/billing/credit");
      creditDialog.querySelector('[name="account_id"]').value = addCredit.getAttribute("data-id") || "";
      creditDialog.querySelector(".credit-account").textContent = addCredit.getAttribute("data-email") || "";
      creditDialog.querySelector('[name="amount_cents"]').value = "";
      creditDialog.querySelector('[name="reason"]').value = "manual";
      creditDialog.showModal();
      return;
    }
    var newGroup = event.target.closest("[data-new-group]");
    if (newGroup) {
      var newGroupDialog = document.getElementById("group-dialog");
      if (!newGroupDialog) return;
      newGroupDialog.querySelector("form").setAttribute("action", "/billing/groups");
      newGroupDialog.querySelector(".group-title").textContent = "New group";
      newGroupDialog.querySelector('[name="name"]').value = "";
      newGroupDialog.querySelector('[name="plan_id"]').value = "";
      newGroupDialog.querySelector('[name="trial_days"]').value = "0";
      newGroupDialog.querySelector('[name="next_group_id"]').value = "";
      newGroupDialog.querySelector('[name="affiliate_code"]').value = "";
      newGroupDialog.querySelector('[name="affiliate_enabled"]').checked = false;
      ["name", "trial_days", "next_group_id", "affiliate_code", "affiliate_enabled",
       "plan_id"].forEach(function (f) {
        newGroupDialog.querySelector('[name="' + f + '"]').disabled = false;
      });
      newGroupDialog.showModal();
      return;
    }
    var editGroup = event.target.closest("[data-edit-group]");
    if (editGroup) {
      var groupDialog = document.getElementById("group-dialog");
      if (!groupDialog) return;
      var gForm = groupDialog.querySelector("form");
      gForm.setAttribute("action",
        "/billing/groups/" + editGroup.getAttribute("data-id") + "/update");
      var isDefault = editGroup.getAttribute("data-is-default") === "1";
      groupDialog.querySelector(".group-title").textContent =
        isDefault ? "System group (plan only)" : "Edit group";
      gForm.querySelector('[name="name"]').value = editGroup.getAttribute("data-name") || "";
      gForm.querySelector('[name="plan_id"]').value = editGroup.getAttribute("data-plan-id") || "";
      gForm.querySelector('[name="trial_days"]').value = editGroup.getAttribute("data-trial-days") || "0";
      gForm.querySelector('[name="next_group_id"]').value = editGroup.getAttribute("data-next-group-id") || "";
      gForm.querySelector('[name="affiliate_code"]').value = editGroup.getAttribute("data-affiliate-code") || "";
      gForm.querySelector('[name="affiliate_enabled"]').checked =
        editGroup.getAttribute("data-affiliate-enabled") === "1";
      // The system group is fixed except for the attached plan.
      ["name", "trial_days", "next_group_id", "affiliate_code", "affiliate_enabled"]
        .forEach(function (f) { gForm.querySelector('[name="' + f + '"]').disabled = isDefault; });
      gForm.querySelector('[name="plan_id"]').disabled = false;
      groupDialog.showModal();
      return;
    }
    var planDetails = event.target.closest("[data-plan-details]");
    if (planDetails) {
      var pd = document.getElementById("plan-details-dialog");
      if (!pd) return;
      var fields = {
        ".pd-name": "data-plan-name", ".pd-type": "data-plan-type",
        ".pd-fee": "data-plan-fee", ".pd-included": "data-plan-included",
        ".pd-overage": "data-plan-overage", ".pd-storage": "data-plan-storage",
        ".pd-grace": "data-plan-grace", ".pd-max": "data-plan-max",
        ".pd-pending": "data-plan-pending", ".pd-expiry": "data-plan-expiry"
      };
      Object.keys(fields).forEach(function (sel) {
        var value = planDetails.getAttribute(fields[sel]);
        pd.querySelector(sel).textContent =
          (value === null || value === "" ? "—" : value);
      });
      pd.showModal();
    }
  });

  // ---- Billing plan dialog: account-only fields depend on the type --------

  var planDialogEl = document.getElementById("plan-dialog");
  if (planDialogEl) {
    var scopeSelect = planDialogEl.querySelector('[name="scope"]');
    planDialogEl._applyScope = function () {
      var account = scopeSelect.value === "account";
      planDialogEl.querySelectorAll(".plan-account-only").forEach(function (el) {
        el.hidden = !account;
      });
      var form = planDialogEl.querySelector("form");
      form._validate && form._validate();
    };
    scopeSelect.addEventListener("change", planDialogEl._applyScope);
  }
})();
