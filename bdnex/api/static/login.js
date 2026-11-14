"use strict";
// The login form: no framework. A right login sets the session cookie, then the page loads.
const form = document.getElementById("form"), msg = document.getElementById("msg");
form.addEventListener("submit", async e => {
  e.preventDefault(); msg.textContent = "";
  try {
    const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "bdnex" },
      body: JSON.stringify({ username: document.getElementById("user").value, password: document.getElementById("pass").value }) });
    if (res.ok) { location.reload(); return; }
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (err) { /* not JSON */ }
    msg.textContent = detail;
  } catch (err) { msg.textContent = "The server did not answer."; }
});
