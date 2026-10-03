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
  // Crossed-out eye = the value is currently masked.
  var EYE_OFF = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45' +
    ' 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0' +
    ' 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>' +
    '<line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function syncToggle(btn, input) {
    var hidden = input.type === "password";
    btn.innerHTML = hidden ? EYE_OFF : EYE;
    btn.setAttribute("aria-label", hidden ? "Show password" : "Hide password");
    btn.setAttribute("aria-pressed", hidden ? "false" : "true");
  }

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    var field = input.closest(".field") || input.parentElement;
    if (!field || field.querySelector(".pw-toggle")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    btn.addEventListener("click", function () {
      input.type = input.type === "password" ? "text" : "password";
      syncToggle(btn, input);
    });
    field.appendChild(btn);
    syncToggle(btn, input);
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

  // ---- Passwords in dialogs ------------------------------------------------

  // A "new password" field has to start empty and stay empty until the user
  // deliberately types in it. Password managers fill it with the *current*
  // password the instant the dialog opens; saving that looks like a successful
  // change but silently keeps the old password, so the new one never works.
  // readOnly keeps autofill out; the first click or keystroke drops it, so
  // typing behaves normally.
  function armPasswordField(input) {
    if (!input) return;
    input.value = "";
    input.readOnly = true;
    var unlock = function () { input.readOnly = false; };
    input.addEventListener("keydown", unlock, {once: true});
    input.addEventListener("pointerdown", unlock, {once: true});
  }

  // ---- Generic modals (e.g. add / edit admin) -----------------------------

  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-open-dialog]");
    if (opener) {
      var target = document.querySelector(opener.getAttribute("data-open-dialog"));
      if (target) {
        target.showModal();
        target.querySelectorAll('input[type="password"]').forEach(armPasswordField);
      }
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
      editDialog.querySelector(".edit-username").textContent =
        edit.getAttribute("data-username") || "";
      editDialog.showModal();
      armPasswordField(editDialog.querySelector('[name="password"]'));
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
      accountDialog.showModal();
      armPasswordField(accountDialog.querySelector('[name="password"]'));
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
      newPlanDialog.querySelector('[name="require_credit"]').checked = false;
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
      planDialog.querySelector('[name="require_credit"]').checked =
        editPlan.getAttribute("data-require-credit") === "1";
      planDialog._applyScope && planDialog._applyScope();
      planDialog.querySelector("form")._validate &&
        planDialog.querySelector("form")._validate();
      planDialog.showModal();
      return;
    }
    var editServerPlan = event.target.closest("[data-edit-server-plan]");
    if (editServerPlan) {
      var spDialog = document.getElementById("server-plan-dialog");
      if (!spDialog) return;
      spDialog.querySelector("form").setAttribute(
        "action",
        "/federation/" + editServerPlan.getAttribute("data-server-id") + "/plan");
      spDialog.querySelector(".sp-hostname").textContent =
        editServerPlan.getAttribute("data-hostname") || "";
      spDialog.querySelector('[name="plan_id"]').value =
        editServerPlan.getAttribute("data-plan") || "";
      spDialog.showModal();
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
      // Storage and pending-storage terms don't relate to server (host) plans.
      var isServerPlan = (pd.querySelector(".pd-type").textContent || "")
        .indexOf("server") !== -1;
      pd.querySelectorAll(".pd-hide-for-server").forEach(function (el) {
        el.hidden = isServerPlan;
      });
      pd.showModal();
    }
  });

  // ---- Small-screen nav (hamburger) ---------------------------------------

  document.querySelectorAll(".nav-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!open));
      btn.closest(".side").classList.toggle("nav-open", !open);
    });
  });

  // ---- Table labels for the small-screen card layout ----------------------
  // Below the card breakpoint each table row restacks as label/value blocks;
  // copy the column headings onto the cells so the templates stay
  // markup-only. Tables without a thead (key-value pairs) are left alone.

  document.querySelectorAll(".table-wrap table").forEach(function (table) {
    var heads = Array.prototype.map.call(
      table.querySelectorAll("thead th"),
      function (th) { return th.textContent.replace(/\s+/g, " ").trim(); }
    );
    if (!heads.length) return;
    table.querySelectorAll("tbody tr").forEach(function (row) {
      Array.prototype.forEach.call(row.cells, function (cell) {
        var head = heads[cell.cellIndex];
        if (head) cell.setAttribute("data-label", head);
      });
    });
  });

  // ---- Billing plan dialog: account-only fields depend on the type --------

  var planDialogEl = document.getElementById("plan-dialog");
  if (planDialogEl) {
    var scopeSelect = planDialogEl.querySelector('[name="scope"]');
    planDialogEl._applyScope = function () {
      var account = scopeSelect.value === "account";
      planDialogEl.querySelectorAll(".plan-account-only").forEach(function (el) {
        el.hidden = !account;
        // Hidden is not enough: the browser still validates required inputs
        // it cannot see, so Save would be blocked by the fields we hid. Take
        // them out of the form as well; absent values leave the stored plan
        // columns untouched on edit, and default to 0 on create.
        el.querySelectorAll("input").forEach(function (input) {
          input.disabled = !account;
        });
      });
      var form = planDialogEl.querySelector("form");
      form._validate && form._validate();
    };
    scopeSelect.addEventListener("change", planDialogEl._applyScope);
  }
})();
