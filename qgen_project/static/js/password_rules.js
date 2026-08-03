/**
 * Live password rule checks (Django validators, client-side subset).
 * Expects #pw-rules / #pw-match-rules and #id_password1 / #id_password2
 * inside the same form (optional #id_username / #id_email for similarity).
 */
(function () {
  function initPasswordRules(form) {
    if (!form) return;

    const pw1 = form.querySelector("#id_password1");
    const pw2 = form.querySelector("#id_password2");
    const username = form.querySelector("#id_username");
    const email = form.querySelector("#id_email");
    const rules = form.querySelector("#pw-rules");
    const matchRules = form.querySelector("#pw-match-rules");
    if (!pw1 || !rules) return;

    const COMMON = new Set([
      "password", "password1", "password123", "12345678", "123456789", "1234567890",
      "qwerty123", "qwertyui", "iloveyou", "admin123", "welcome1", "abc12345",
      "letmein1", "monkey12", "dragon12", "master12", "login123", "passw0rd",
      "football", "baseball", "starwars", "shadow12", "sunshine", "princess",
      "654321", "11111111", "00000000", "abcdefg1", "changeme", "trustno1",
    ]);

    function normalize(s) {
      return (s || "").trim().toLowerCase();
    }

    function personalBits() {
      const bits = [];
      const u = normalize(username && username.value);
      const e = normalize(email && email.value);
      if (u) bits.push(u);
      if (e) {
        bits.push(e);
        const local = e.split("@")[0];
        if (local) bits.push(local);
      }
      return bits.filter((b) => b.length >= 3);
    }

    function tooSimilar(password) {
      const p = normalize(password);
      if (!p) return false;
      return personalBits().some((bit) => p.includes(bit) || bit.includes(p));
    }

    function setRule(listEl, name, ok, active) {
      if (!listEl) return;
      const li = listEl.querySelector('[data-rule="' + name + '"]');
      if (!li) return;
      li.classList.remove("is-ok", "is-bad", "is-idle");
      if (!active) {
        li.classList.add("is-idle");
        return;
      }
      li.classList.add(ok ? "is-ok" : "is-bad");
    }

    function evaluate() {
      const password = pw1.value || "";
      const active = password.length > 0;

      setRule(rules, "length", password.length >= 8, active);
      setRule(rules, "numeric", password.length > 0 && !/^\d+$/.test(password), active);
      setRule(rules, "common", password.length > 0 && !COMMON.has(normalize(password)), active);
      setRule(rules, "similar", password.length > 0 && !tooSimilar(password), active);

      const confirm = pw2 ? pw2.value : "";
      const matchOk = confirm.length > 0 && confirm === password;
      setRule(matchRules, "match", matchOk, confirm.length > 0);
    }

    ["input", "change", "keyup"].forEach((evt) => {
      pw1.addEventListener(evt, evaluate);
      if (pw2) pw2.addEventListener(evt, evaluate);
      if (username) username.addEventListener(evt, evaluate);
      if (email) email.addEventListener(evt, evaluate);
    });

    rules.querySelectorAll(".pw-rule").forEach((li) => li.classList.add("is-idle"));
    if (matchRules) {
      matchRules.querySelectorAll(".pw-rule").forEach((li) => li.classList.add("is-idle"));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-pw-rules]").forEach(initPasswordRules);
  });
})();
