# ============================================================
# AIUTO MAIL — Crea bozze Gmail da PDF + lista destinatari
# ============================================================

import base64
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

import pandas as pd
import requests as _http
import streamlit as st

try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_OK = True
except ImportError:
    PDF2IMAGE_OK = False

try:
    import google.oauth2.credentials
    import google.auth.transport.requests
    from googleapiclient.discovery import build
    GOOGLE_OK = True
except ImportError:
    GOOGLE_OK = False


SCOPE = "https://www.googleapis.com/auth/gmail.compose"
SCOPES = [SCOPE]
SKIP_SHEETS = {"Istruzioni"}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="AIUTO MAIL", page_icon="✉️", layout="wide")

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #f5f5f5; }
.block-container { padding-top: 1.5rem !important; }

.step-card {
    background: #ffffff;
    border-radius: 10px;
    padding: 1.2rem 1.5rem 1rem 1.5rem;
    margin-bottom: 1.2rem;
    border: 1px solid #e0e0e0;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
}
.step-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: #c0392b;
    margin-bottom: 0.6rem;
    letter-spacing: .02em;
}
.note-pill {
    display: inline-block;
    background: #fff3cd;
    color: #7d5a00;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 0.8rem;
    margin-top: 3px;
}
.auth-box {
    background: #fff8f8;
    border: 1.5px solid #e74c3c;
    border-radius: 10px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 1rem;
}
.auth-ok {
    background: #f0fff4;
    border: 1.5px solid #27ae60;
    border-radius: 10px;
    padding: 0.7rem 1.2rem;
    margin-bottom: 1rem;
    color: #155724;
    font-weight: 600;
}
.row-sep { border-top: 1px solid #f0f0f0; margin: 0.5rem 0; }
</style>
""", unsafe_allow_html=True)


# ── Secrets ───────────────────────────────────────────────────────────────────
def _secret(key, default=""):
    try:
        val = st.secrets.get(key, default)
        return val.strip() if isinstance(val, str) else val
    except Exception:
        return default


# ── OAuth (semplice, senza PKCE) ──────────────────────────────────────────────
def _build_auth_url():
    cid  = _secret("GOOGLE_CLIENT_ID")
    ruri = _secret("REDIRECT_URI", "http://localhost:8501")
    params = {
        "client_id":     cid,
        "redirect_uri":  ruri,
        "response_type": "code",
        "scope":         SCOPE,
        "access_type":   "offline",
        "prompt":        "consent",
    }
    return "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)


def _exchange_code(code: str) -> dict:
    cid     = _secret("GOOGLE_CLIENT_ID")
    csecret = _secret("GOOGLE_CLIENT_SECRET")
    ruri    = _secret("REDIRECT_URI", "http://localhost:8501")
    resp = _http.post("https://oauth2.googleapis.com/token", data={
        "code":          code,
        "client_id":     cid,
        "client_secret": csecret,
        "redirect_uri":  ruri,
        "grant_type":    "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    tok = resp.json()
    return {
        "token":         tok["access_token"],
        "refresh_token": tok.get("refresh_token", ""),
        "token_uri":     "https://oauth2.googleapis.com/token",
        "client_id":     cid,
        "client_secret": csecret,
        "scopes":        SCOPES,
    }


def get_service():
    data = st.session_state.get("credentials")
    if not data:
        return None
    try:
        creds = google.oauth2.credentials.Credentials(**data)
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            st.session_state["credentials"]["token"] = creds.token
        return build("gmail", "v1", credentials=creds)
    except Exception:
        return None


# Handle OAuth callback — must run before any widget
_qp = st.query_params
if "code" in _qp and "credentials" not in st.session_state:
    try:
        st.session_state["credentials"] = _exchange_code(_qp["code"])
    except Exception as e:
        st.session_state["oauth_error"] = str(e)
    st.query_params.clear()
    st.rerun()


# ── PDF helpers ───────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, max_entries=5)
def pdf_to_data(pdf_bytes: bytes, dpi: int = 150):
    """Returns (pdf_html: str, preview_pngs: list[bytes])"""
    import re
    # HTML via PyMuPDF (testo reale con colori e stili)
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_html = []
        for page in doc:
            pages_html.append(page.get_text("html"))
        doc.close()
        body = '<hr style="border:none;border-top:1px solid #e0e0e0;margin:24px 0;">'.join(pages_html)
        # Rimuovi dichiarazioni HTML/body delle singole pagine
        body = re.sub(r'</?html[^>]*>', '', body, flags=re.IGNORECASE)
        body = re.sub(r'</?body[^>]*>', '', body, flags=re.IGNORECASE)
        body = re.sub(r'</?head[^>]*>.*?</head>', '', body, flags=re.IGNORECASE | re.DOTALL)
        pdf_html = body.strip()
    except Exception:
        pdf_html = "<p><em>Impossibile estrarre testo dal PDF.</em></p>"

    # PNG per anteprima nell'app
    pngs = []
    if PDF2IMAGE_OK:
        try:
            images = convert_from_bytes(pdf_bytes, dpi=dpi)
            for img in images:
                buf = BytesIO()
                img.save(buf, format="PNG")
                pngs.append(buf.getvalue())
        except Exception:
            pass

    return pdf_html, pngs


def generate_intro_local(nome1: str, cog1: str, nome2: str, cog2: str,
                         azienda: str, note: str) -> str:
    """Genera introduzione personalizzata localmente — nessuna API esterna."""
    nl = (note or "").lower()

    # ── Tono ──────────────────────────────────────────────────────────────────
    informal = any(k in nl for k in (
        "ciao", "informale", "amico", "amica", "collega", "colleghi",
        "amichevole", " tu ", " tu,", "tono diretto",
    ))

    # ── Titolo ────────────────────────────────────────────────────────────────
    title = ""
    if any(k in nl for k in ("dottoressa", "dott.ssa")):
        title = "Dott.ssa"
    elif any(k in nl for k in ("dottor", "dottore", "dott.", "dott ")):
        title = "Dott."
    elif any(k in nl for k in ("professor", "prof.", "prof ")):
        title = "Prof."
    elif any(k in nl for k in ("ingegner", "ing.", "ing ")):
        title = "Ing."
    elif any(k in nl for k in ("avvocato", "avv.", "avv ")):
        title = "Avv."
    elif any(k in nl for k in ("direttore", "dir.")):
        title = "Direttore"

    # ── Indirizzo ─────────────────────────────────────────────────────────────
    def addr(nome, cog):
        if informal:
            return nome or cog
        if title:
            return f"{title} {cog}".strip() if cog else f"{title} {nome}".strip()
        return cog or nome

    a1, a2 = addr(nome1, cog1), addr(nome2, cog2)
    if informal:
        saluto = (f"Ciao {a1} e {a2}," if a1 and a2
                  else f"Ciao {a1}," if a1 else "Ciao,")
    else:
        saluto = (f"Buongiorno {a1} e {a2}," if a1 and a2
                  else f"Buongiorno {a1}," if a1 else "Buongiorno,")

    # ── Azione principale ─────────────────────────────────────────────────────
    lei = not informal
    appt     = any(k in nl for k in ("appuntamento", "incontro", "call", "riunione",
                                      "fissare", "organizzare", "proponi", "proporre"))
    followup = any(k in nl for k in ("seguito", "follow", "come da", "come discusso",
                                      "come concordato", "concordat"))
    thanks   = any(k in nl for k in ("grazi", "ringrazi", "ringraziamento"))
    first    = any(k in nl for k in ("primo invio", "primo contatto", "nuovo cliente",
                                      "presentazione", "benvenuto", "prima volta"))

    if appt:
        corpo = (
            "Le scrivo per proporLe un incontro nei prossimi giorni, "
            "così da commentare insieme i mercati e valutare le opportunità del momento."
            if lei else
            "Ti scrivo per proporti un incontro nei prossimi giorni, "
            "così da aggiornarci insieme sui mercati."
        )
    elif followup:
        corpo = (
            "Come concordato nel nostro recente incontro, "
            "Le faccio pervenire il consueto report settimanale."
            if lei else
            "Come ci eravamo detti, ti mando il consueto report settimanale."
        )
    elif thanks:
        corpo = (
            "La ringrazio per il nostro recente incontro "
            "e per la disponibilità che Le è propria."
            if lei else
            "Ti ringrazio per il nostro recente incontro, è sempre un piacere."
        )
    elif first:
        corpo = (
            "È con piacere che Le faccio pervenire per la prima volta "
            "il nostro report settimanale sui mercati, "
            "che speriamo possa essere di Suo interesse."
            if lei else
            "Con piacere ti mando per la prima volta "
            "il nostro report settimanale sui mercati."
        )
    else:
        corpo = ""

    chiusura = (
        "In allegato trova il report settimanale con la nostra analisi dei mercati finanziari."
        if lei else
        "In allegato trovi il report settimanale con la nostra analisi dei mercati finanziari."
    )

    parts = [f"<p>{saluto}</p>"]
    if corpo:
        parts.append(f"<p>{corpo}</p>")
    parts.append(f"<p>{chiusura}</p>")
    return "\n".join(parts)


def build_full_email(pdf_html: str, intro_html: str = "") -> str:
    """Assembla HTML email con intro personalizzata + corpo PDF."""
    header = ""
    if intro_html.strip():
        header = (
            '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.7;">'
            + intro_html +
            '</div>'
            '<hr style="border:none;border-top:2px solid #c0392b;margin:20px 0 24px 0;">'
        )
    return (
        '<html><head><meta charset="utf-8"></head>'
        '<body style="font-family:Arial,sans-serif;max-width:800px;'
        'margin:0 auto;padding:20px;background:#ffffff;">'
        + header + pdf_html +
        '</body></html>'
    )


# ── MIME builder ──────────────────────────────────────────────────────────────
def build_mime_message(
    to_list: list,
    bcc_list: list,
    subject: str,
    html_body: str,          # HTML già personalizzato per questo destinatario
    attachments: list,       # [(filename, bytes), ...]
) -> str:
    root = MIMEMultipart("mixed")
    root["To"] = ", ".join(to_list) if to_list else ""
    if bcc_list:
        root["Bcc"] = ", ".join(bcc_list)
    root["Subject"] = subject
    root.attach(MIMEText(html_body, "html", "utf-8"))

    for fname, fbytes in attachments:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(fbytes)
        encoders.encode_base64(part)
        safe = fname.encode("ascii", errors="replace").decode()
        part.add_header("Content-Disposition", f'attachment; filename="{safe}"')
        root.attach(part)

    return base64.urlsafe_b64encode(root.as_bytes()).decode()


# ── Excel loader ──────────────────────────────────────────────────────────────
COL_MAP = {
    "Nome 1": "nome1",
    "Cognome 1": "cog1",
    "Email 1": "email1",
    "Nome 2": "nome2",
    "Cognome 2": "cog2",
    "Email 2": "email2",
    "Azienda / Contesto": "azienda",
    "Note per personalizzazione": "note",
}


def _v(val) -> str:
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "") else s


def parse_emails(raw: str) -> list:
    return [e.strip() for e in raw.replace(";", ",").split(",")
            if e.strip() and "@" in e.strip()]


def load_sheets(xlsx_bytes: bytes) -> dict:
    xl = pd.ExcelFile(BytesIO(xlsx_bytes))
    out = {}
    for sname in xl.sheet_names:
        if sname in SKIP_SHEETS:
            continue
        raw = pd.read_excel(xl, sheet_name=sname, header=None, dtype=str)
        # Find row that contains 'Nome 1' — that's the header row
        hdr = None
        for i, row in raw.iterrows():
            if "Nome 1" in row.values:
                hdr = i
                break
        if hdr is None:
            continue
        raw.columns = raw.iloc[hdr]
        df = raw.iloc[hdr + 1:].reset_index(drop=True)
        df = df.rename(columns=COL_MAP)
        # Drop rows with no email at all
        df = df[df.apply(
            lambda r: bool(_v(r.get("email1", "")) or _v(r.get("email2", ""))),
            axis=1
        )].copy()
        if not df.empty:
            out[sname] = df
    return out


# ══════════════════════════════════════════════════════════════════════════════
# ── TITLE ─────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<h1 style="color:#c0392b;letter-spacing:-.02em;margin-bottom:0;">✉️ AIUTO MAIL</h1>'
    '<p style="color:#666;margin-top:2px;">Crea bozze Gmail personalizzate partendo da un PDF</p>',
    unsafe_allow_html=True,
)

# ── GMAIL AUTH ────────────────────────────────────────────────────────────────
secrets_ok = bool(_secret("GOOGLE_CLIENT_ID") and _secret("GOOGLE_CLIENT_SECRET"))
service = None

if not GOOGLE_OK:
    st.error("Librerie Google mancanti. Controlla requirements.txt.")
    st.stop()

if "oauth_error" in st.session_state:
    st.error(f"Errore OAuth: {st.session_state.pop('oauth_error')}")

if not secrets_ok:
    st.markdown('<div class="auth-box">', unsafe_allow_html=True)
    st.warning("⚙️ Per usare l'app configura i **Secrets** con le credenziali Google.")
    with st.expander("📖 Come configurare Google OAuth (clicca per espandere)"):
        st.markdown("""
**Passo 1 — Google Cloud Console**
1. Vai su [console.cloud.google.com](https://console.cloud.google.com/)
2. Crea un nuovo progetto (o usa uno esistente)
3. Menu → **API e Servizi** → **Libreria** → cerca **Gmail API** → Abilita

**Passo 2 — Crea credenziali OAuth**
1. **API e Servizi** → **Credenziali** → **Crea credenziali** → **ID client OAuth**
2. Tipo applicazione: **Applicazione web**
3. Aggiungi in *URI di reindirizzamento autorizzati*:
   - Per test locale: `http://localhost:8501`
   - Per Streamlit Cloud: `https://NOME-TUA-APP.streamlit.app`
4. Scarica il JSON e copia Client ID e Client Secret

**Passo 3 — Secrets in Streamlit**

Vai in **Settings → Secrets** dell'app e incolla:
```toml
GOOGLE_CLIENT_ID = "123456.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-abc123..."
REDIRECT_URI = "https://NOME-TUA-APP.streamlit.app"
```
        """)
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

service = get_service()
if service:
    st.markdown('<div class="auth-ok">✅ Gmail collegato — puoi creare bozze</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="auth-box">', unsafe_allow_html=True)
    st.markdown("**🔐 Collega il tuo account Gmail** per creare le bozze.")
    st.caption("Verrà richiesta solo l'autorizzazione a *creare bozze* (nessuna lettura delle email).")
    auth_url = _build_auth_url()
    st.link_button("🔗  Vai a Google per autorizzare", auth_url, type="primary")
    st.caption("Si apre in un nuovo tab → autorizza → torna su questa pagina già collegato.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — PDF principale
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-card">', unsafe_allow_html=True)
st.markdown('<div class="step-title">① PDF principale — corpo della mail</div>', unsafe_allow_html=True)
st.caption("Il contenuto del PDF verrà riprodotto fedelmente (testo, colori, tabelle, stile).")

main_pdf_file = st.file_uploader(
    "Carica il PDF principale",
    type=["pdf"],
    key="main_pdf",
    label_visibility="collapsed",
)

pdf_html: str = ""
preview_pngs: list = []

if main_pdf_file:
    with st.spinner("Estrazione testo e stili dal PDF…"):
        pdf_html, preview_pngs = pdf_to_data(main_pdf_file.read())
    n_pg = max(len(preview_pngs), 1)
    st.success(f"✅ PDF caricato — **{n_pg} pagina/e**  ·  `{main_pdf_file.name}`")
    with st.expander("🔍 Anteprima visiva", expanded=False):
        for i, png in enumerate(preview_pngs):
            st.image(png, caption=f"Pagina {i+1}", use_column_width=True)

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — PDF allegati
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-card">', unsafe_allow_html=True)
st.markdown('<div class="step-title">② Allegati PDF — opzionale, max 5 file</div>', unsafe_allow_html=True)
st.caption("Questi PDF verranno allegati a ogni bozza. Non modificano il corpo della mail.")

att_files = st.file_uploader(
    "Allegati",
    type=["pdf"],
    accept_multiple_files=True,
    key="att_pdfs",
    label_visibility="collapsed",
)
if att_files and len(att_files) > 5:
    st.warning("⚠️ Massimo 5 allegati — uso solo i primi 5.")
    att_files = att_files[:5]

attachments_data: list = []
if att_files:
    for f in att_files:
        attachments_data.append((f.name, f.read()))
    st.info(
        f"📎 **{len(attachments_data)} allegato/i:** "
        + "  ·  ".join(f"`{f.name}`" for f in att_files)
    )

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Excel destinatari
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="step-card">', unsafe_allow_html=True)
st.markdown('<div class="step-title">③ Lista destinatari — file Excel</div>', unsafe_allow_html=True)
st.caption(
    "Struttura attesa: colonne **Nome 1 · Cognome 1 · Email 1 · Nome 2 · Cognome 2 · Email 2 · "
    "Azienda/Contesto · Note per personalizzazione**. Ogni scheda = una lista separata."
)

excel_file = st.file_uploader(
    "Excel destinatari",
    type=["xlsx", "xls"],
    key="excel",
    label_visibility="collapsed",
)

sheets_data: dict = {}
selected_sheets: list = []

if excel_file:
    with st.spinner("Lettura Excel…"):
        sheets_data = load_sheets(excel_file.read())

    if not sheets_data:
        st.error("Nessuna scheda destinatari trovata (con colonna 'Nome 1').")
    else:
        snames = list(sheets_data.keys())
        counts = {s: len(sheets_data[s]) for s in snames}
        total = sum(counts.values())
        st.success(
            f"✅ **{len(snames)} liste** trovate — {total} righe totali"
        )

        st.markdown("**Seleziona le liste da includere nell'invio:**")
        ncols = min(len(snames), 4)
        cols = st.columns(ncols)
        for i, sname in enumerate(snames):
            with cols[i % ncols]:
                if st.checkbox(
                    f"**{sname}**\n_{counts[sname]} dest._",
                    value=True,
                    key=f"chk_{sname}",
                ):
                    selected_sheets.append(sname)

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Configurazione destinatari (A / CCN)
# ══════════════════════════════════════════════════════════════════════════════
recipient_rows: list = []

if sheets_data and selected_sheets:
    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="step-title">④ Destinatari — imposta A (To) o CCN (Bcc) per ogni riga</div>',
        unsafe_allow_html=True,
    )

    for sname in selected_sheets:
        df = sheets_data[sname]
        with st.expander(f"📋 **{sname}** — {len(df)} righe", expanded=True):

            # Default di scheda
            col_def, _ = st.columns([2, 5])
            with col_def:
                sheet_default = st.radio(
                    "Tipo predefinito per questa lista",
                    ["A — Campo A (To)", "CCN — Campo Nascosto (Bcc)"],
                    horizontal=True,
                    key=f"def_{sname}",
                )
            default_is_to = sheet_default.startswith("A")

            st.markdown("---")

            for idx, row in df.iterrows():
                e1 = _v(row.get("email1", ""))
                e2 = _v(row.get("email2", ""))
                n1 = _v(row.get("nome1", ""))
                c1 = _v(row.get("cog1", ""))
                n2 = _v(row.get("nome2", ""))
                c2 = _v(row.get("cog2", ""))
                note = _v(row.get("note", ""))
                azienda = _v(row.get("azienda", ""))

                all_emails = list(dict.fromkeys(
                    parse_emails(e1) + parse_emails(e2)
                ))
                if not all_emails:
                    continue

                label1 = f"{n1} {c1}".strip() or e1
                label2 = (f"{n2} {c2}".strip() or e2) if e2 else ""
                nome_display = label1 + (f" + {label2}" if label2 else "")
                if azienda:
                    nome_display += f"  ·  {azienda}"

                rc1, rc2, rc3 = st.columns([4, 2, 1])
                with rc1:
                    st.markdown(f"**{nome_display}**")
                    st.caption("  ·  ".join(all_emails))
                    if note:
                        st.markdown(
                            f'<span class="note-pill">📝 {note}</span>',
                            unsafe_allow_html=True,
                        )
                with rc2:
                    tipo = st.selectbox(
                        "Tipo",
                        ["A (To)", "CCN (Bcc)"],
                        index=0 if default_is_to else 1,
                        key=f"tipo_{sname}_{idx}",
                        label_visibility="collapsed",
                    )
                with rc3:
                    includi = st.checkbox(
                        "✓",
                        value=True,
                        key=f"inc_{sname}_{idx}",
                        help="Includi in questo invio",
                    )

                if includi:
                    recipient_rows.append(
                        {
                            "emails": all_emails,
                            "tipo": "to" if tipo.startswith("A") else "bcc",
                            "nome_display": nome_display,
                            "nome1": n1, "cog1": c1,
                            "nome2": n2, "cog2": c2,
                            "azienda": azienda,
                            "note": note,
                        }
                    )

                st.markdown('<div class="row-sep"></div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Oggetto + Crea bozze
# ══════════════════════════════════════════════════════════════════════════════
if recipient_rows:
    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="step-title">⑤ Oggetto mail e creazione bozze Gmail</div>',
        unsafe_allow_html=True,
    )

    n_tot = len(recipient_rows)
    n_to  = sum(1 for r in recipient_rows if r["tipo"] == "to")
    n_bcc = sum(1 for r in recipient_rows if r["tipo"] == "bcc")

    col_i1, col_i2, col_i3 = st.columns(3)
    col_i1.metric("Bozze totali", n_tot)
    col_i2.metric("In campo A (To)", n_to)
    col_i3.metric("In campo CCN (Bcc)", n_bcc)

    if att_files:
        st.caption(f"📎 Allegato a ogni bozza: {', '.join(f'`{f.name}`' for f in att_files)}")

    st.markdown("---")

    # ── Personalizzazione ─────────────────────────────────────────────────────
    st.markdown("**✍️ Personalizzazione introduzioni**")
    st.caption(
        "L'app legge le note dal file Excel e compone un'introduzione su misura: "
        "saluto formale/informale, titolo (Dott., Prof., Ing…), "
        "e azione specifica (appuntamento, follow-up, ringraziamento, primo invio…)."
    )

    if st.button(
        "✨ Genera anteprime personalizzate",
        key="btn_gen_intros",
    ):
        intros = {}
        for i, rec in enumerate(recipient_rows):
            intros[i] = generate_intro_local(
                rec.get("nome1", ""), rec.get("cog1", ""),
                rec.get("nome2", ""), rec.get("cog2", ""),
                rec.get("azienda", ""), rec.get("note", ""),
            )
        st.session_state["intros"] = intros
        st.rerun()

    if "intros" in st.session_state and st.session_state["intros"]:
        st.success(
            f"✅ {len(st.session_state['intros'])} introduzioni pronte — "
            "revisionale e modificale qui sotto prima di creare le bozze."
        )
        with st.expander("📝 Revisiona / modifica le introduzioni", expanded=True):
            for i, rec in enumerate(recipient_rows):
                st.markdown(f"**{rec['nome_display']}**")
                if rec.get("note"):
                    st.caption(f"Note: *{rec['note']}*")
                current = st.session_state["intros"].get(i, "")
                st.text_area(
                    "Introduzione",
                    value=current,
                    height=110,
                    key=f"intro_edit_{i}",
                    label_visibility="collapsed",
                )
                st.markdown('<div class="row-sep"></div>', unsafe_allow_html=True)

    st.markdown("---")

    # ── Oggetto + Crea bozze ──────────────────────────────────────────────────
    st.markdown("**📧 Oggetto della mail**")
    subject = st.text_input(
        "Oggetto",
        placeholder="Report settimanale mercati — 23 maggio 2026",
        key="subject",
        label_visibility="collapsed",
    )

    missing = []
    if not pdf_html:
        missing.append("carica il PDF principale (Step ①)")
    if not subject.strip():
        missing.append("inserisci l'oggetto della mail")

    btn_help = "Prima " + " e ".join(missing) if missing else ""
    if st.button(
        f"📨  Crea {n_tot} bozze in Gmail",
        type="primary",
        use_container_width=True,
        disabled=bool(missing),
        help=btn_help,
    ):
        progress = st.progress(0.0, text="Avvio…")
        status_box = st.empty()
        errors = []
        created = 0
        stored_intros = st.session_state.get("intros", {})

        for i, rec in enumerate(recipient_rows):
            to_list  = rec["emails"] if rec["tipo"] == "to"  else []
            bcc_list = rec["emails"] if rec["tipo"] == "bcc" else []
            progress.progress(
                (i + 1) / n_tot,
                text=f"Bozza {i+1}/{n_tot} — {rec['nome_display']}",
            )
            try:
                intro_html = st.session_state.get(
                    f"intro_edit_{i}", stored_intros.get(i, "")
                )
                full_html = build_full_email(pdf_html, intro_html)
                raw_msg = build_mime_message(
                    to_list, bcc_list, subject,
                    full_html, attachments_data,
                )
                service.users().drafts().create(
                    userId="me",
                    body={"message": {"raw": raw_msg}},
                ).execute()
                created += 1
                status_box.success(f"✅ {created} bozze create…")
            except Exception as e:
                errors.append(f"**{rec['nome_display']}**: {e}")

        progress.empty()
        if errors:
            with st.expander(f"⚠️ {len(errors)} errore/i durante la creazione", expanded=True):
                for err in errors:
                    st.error(err)

        if created:
            st.success(
                f"🎉 **{created} bozze create con successo** in Gmail!  "
                f"Apri [Gmail → Bozze](https://mail.google.com/mail/#drafts) per rivederle."
            )
            st.balloons()

    st.markdown("</div>", unsafe_allow_html=True)
