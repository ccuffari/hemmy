// ============================================================
// Hemmy — config frontend
// Richiede che <script src="...supabase-js@2"> sia caricato
// PRIMA di questo file (vedi login.html / index.html).
// ============================================================

// --- 1. Supabase: URL + anon key del tuo progetto ---
// Li trovi in Supabase → Settings → API.
// L'anon key è PUBBLICA per design: la sicurezza la fa RLS.
window.HEMMY_SUPABASE_URL      = window.HEMMY_SUPABASE_URL      || "https://ugnxqzztdumdllctsqqz.supabase.co";
window.HEMMY_SUPABASE_ANON_KEY = window.HEMMY_SUPABASE_ANON_KEY || "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVnbnhxenp0ZHVtZGxsY3RzcXF6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTAyNzA3NjYsImV4cCI6MjEwNTg0Njc2Nn0.TjXKoP8SKrP7nx0LzKMirpk-h2682gY_MGumlXtzA-0";

// Client Supabase condiviso (uno solo per tutta l'app)
window.HEMMY_SUPABASE = window.supabase.createClient(
  window.HEMMY_SUPABASE_URL,
  window.HEMMY_SUPABASE_ANON_KEY,
  {
    auth: {
      persistSession:     true,   // session resta dopo refresh pagina
      autoRefreshToken:   true,   // refresh automatico del JWT
      detectSessionInUrl: true,   // gestisce il redirect post email-confirm / OAuth
      storageKey:         "hemmy_supabase_session"
    }
  }
);

// --- 2. Backend Python/Flask ---
// In locale punta a Flask dev.
// In produzione: sostituisci con l'URL Cloud Run o con "/api" se usi Firebase rewrites.
window.HEMMY_API_BASE = window.HEMMY_API_BASE || (() => {
  const h = location.hostname;
  if (h === "localhost" || h === "127.0.0.1") return "http://127.0.0.1:8765";
  // URL assoluto sul dominio corrente, senza path: funziona
  // indipendentemente da dove è servita la pagina (/ o /index.html).
  return location.origin;
})();

// --- 3. Token cache: sincronizzato con la sessione Supabase ---
// getToken() resta sincrono per non rompere il codice esistente
// (EventSource in particolare non supporta header).
let _hemmyToken = "";

window.HEMMY_SUPABASE.auth.onAuthStateChange((_event, session) => {
  _hemmyToken = session?.access_token || "";
});

// Promise che si risolve quando la sessione è stata idratata dal localStorage.
// Usala così: await window.hemmyReady;
window.hemmyReady = window.HEMMY_SUPABASE.auth.getSession().then(({ data }) => {
  _hemmyToken = data?.session?.access_token || "";
  return _hemmyToken;
});

function getToken(){ return _hemmyToken; }

async function clearToken(){
  await window.HEMMY_SUPABASE.auth.signOut();
  _hemmyToken = "";
}

// Retrocompatibilità: il vecchio codice chiamava setToken().
// Ora non serve (Supabase persiste da sé), quindi è un no-op.
function setToken(_t){ /* no-op: gestito da Supabase */ }

// --- 4. Wrapper fetch verso il backend ---
async function api(path, opts){
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  const t = getToken();
  if (t) headers["Authorization"] = "Bearer " + t;
  opts.headers = headers;
  return fetch(window.HEMMY_API_BASE + path, opts);
}

// --- 5. Guard: se non loggato, redirect al login ---
// Chiama: const session = await requireAuth();
async function requireAuth(){
  await window.hemmyReady;
  const { data: { session } } = await window.HEMMY_SUPABASE.auth.getSession();
  if (!session) { location.href = "login.html"; return null; }
  return session;
}