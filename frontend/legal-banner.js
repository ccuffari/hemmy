// ============================================================
// Hemmy — banner informativo "privacy/cookie" (leggero, no-consent-blocking)
// ============================================================
// Perché non è un "cookie consent banner" classico con Accetta/Rifiuta:
// Hemmy NON imposta cookie di profilazione, marketing o analytics di terze
// parti. L'unico storage lato client è il localStorage usato da Supabase
// per mantenere la sessione di accesso (strettamente necessario al
// funzionamento del servizio: senza non potresti restare loggato).
// Per la normativa ePrivacy/GDPR lo storage "strettamente necessario"
// NON richiede consenso preventivo, ma va comunque comunicato in modo
// trasparente: è quello che fa questo banner (informativo, non bloccante).
//
// Se in futuro si aggiungono script di terze parti non essenziali
// (analytics, ads, chat widget esterni, ecc.) questo file va rivisto e
// serve un vero banner con Accetta/Rifiuta/Personalizza granulare.
// ============================================================

(function () {
  var ACK_KEY = "hemmy_legal_notice_ack_v1";

  try {
    if (localStorage.getItem(ACK_KEY) === "1") return;
  } catch (e) { /* localStorage non disponibile: mostra comunque il banner */ }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  onReady(function () {
    var style = document.createElement("style");
    style.textContent = [
      "#hemmyLegalBanner{position:fixed;left:0;right:0;bottom:0;z-index:99999;",
      "display:flex;flex-wrap:wrap;align-items:center;gap:12px;",
      "padding:12px 16px;padding-bottom:calc(12px + env(safe-area-inset-bottom));",
      "background:rgba(10,11,16,.96);backdrop-filter:blur(8px);",
      "border-top:1px solid #232635;color:#c2c7d6;",
      "font:13px/1.5 Inter,-apple-system,Segoe UI,Roboto,sans-serif;}",
      "#hemmyLegalBanner .hlb-text{flex:1;min-width:220px}",
      "#hemmyLegalBanner a{color:#eef0f6;text-decoration:none;border-bottom:1px solid rgba(194,199,214,.35);white-space:nowrap}",
      "#hemmyLegalBanner a:hover{border-color:#4f8cff}",
      "#hemmyLegalBanner .hlb-actions{display:flex;gap:8px;align-items:center;flex:0 0 auto}",
      "#hemmyLegalBanner button{font:inherit;cursor:pointer;border:0;border-radius:9px;",
      "padding:8px 16px;font-weight:600;background:linear-gradient(135deg,#6d5bf0,#4f8cff);color:#fff}",
      "#hemmyLegalBanner button:hover{filter:brightness(1.08)}",
      "@media (max-width:520px){#hemmyLegalBanner{font-size:12px}}"
    ].join("");
    document.head.appendChild(style);

    var el = document.createElement("div");
    el.id = "hemmyLegalBanner";
    el.setAttribute("role", "region");
    el.setAttribute("aria-label", "Informativa privacy e cookie");
    el.innerHTML =
      '<div class="hlb-text">' +
      'Usiamo solo dati tecnici strettamente necessari (sessione di accesso). ' +
      'Nessun cookie di profilazione o marketing. ' +
      '<a href="privacy.html">Privacy</a> · ' +
      '<a href="termini.html">Termini</a> · ' +
      '<a href="cookie.html">Cookie</a>' +
      '</div>' +
      '<div class="hlb-actions"><button type="button" id="hemmyLegalAck">Ho capito</button></div>';

    document.body.appendChild(el);

    document.getElementById("hemmyLegalAck").addEventListener("click", function () {
      try { localStorage.setItem(ACK_KEY, "1"); } catch (e) { /* ignora */ }
      el.remove();
    });
  });
})();
