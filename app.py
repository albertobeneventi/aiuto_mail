# ============================================================
# AIUTO MAIL — Crea bozze Gmail da PDF + lista destinatari
# ============================================================

import base64
import json
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

import pandas as pd
import streamlit as st

try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_OK = True
except ImportError:
    PDF2IMAGE_OK = False

try:
    from google_auth_oauthlib.flow import Flow
    import google.oauth2.credentials
    import google.auth.transport.requests
    from googleapiclient.discovery import build
    GOOGLE_OK = True
except ImportError:
    GOOGLE_OK = False

SCOPES = ["https://www.googleapis.com/auth/gmail.drafts.create"]
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
        return st.secrets.get(key, default)
    except Exception:
        return default


# ── OAuth ─────────────────────────────────────────────────────────────────────
def _client_config():
    cid = _secret("GOOGLE_CLIENT_ID")
    csecret = _secret("GOOGLE_CLIENT_SECRET")
    ruri = _secret("REDIRECT_URI", "http://localhost:8501")
    if not cid or not csecret:
        return None, ruri
    return {
        "web": {
            "client_id": cid,
            "client_secret": csecret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [ruri],
        }
    }, ruri


def _make_flow():
    cfg, ruri = _client_config()
    if cfg is None:
        return None
    return Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=ruri)


def _save_creds(creds):
    st.session_state["credentials"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }


def get_service():
    data = st.session_state.get("credentials")
    if not data:
        return None
    try:
        creds = google.oauth2.credentials.Credentials(**data)
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            _save_creds(creds)
        return build("gmail", "v1", credentials=creds)
    except Exception:
        return None


# Handle OAuth callback — must run before any widget
_qp = st.query_params
if "code" in _qp and st.session_state.get("pending_oauth"):
    try:
        flow = _make_flow()
        if flow:
            flow.fetch_token(code=_qp["code"])
            _save_creds(flow.credentials)
    except Exception as e:
        st.session_state["oauth_error"] = str(e)
    finally:
        st.session_state.pop("pending_oauth", None)
        st.query_params.clear()
        st.rerun()


# ── PDF helpers ───────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, max_entries=5)
def pdf_to_pngs(pdf_bytes: bytes, dpi: int = 150) -> list:
    images = convert_from_bytes(pdf_bytes, dpi=dpi)
    result = []
    for img in images:
        buf = BytesIO()
        img.save(buf, format="PNG")
        result.append(buf.getvalue())
    return result


def build_html(n_pages: int) -> str:
    imgs = "".join(
        f'<div style="margin-bottom:6px;text-align:center;">'
        f'<img src="cid:pg{i}" style="max-width:800px;width:100%;display:block;margin:0 auto;"></div>'
        for i in range(n_pages)
    )
    return (
        '<html><body style="margin:0;padding:16px 24px;background:#ffffff;'
        'font-family:Arial,sans-serif;">'
        + imgs
        + "</body></html>"
    )


# ── MIME builder ──────────────────────────────────────────────────────────────
def build_mime_message(
    to_list: list,
    bcc_list: list,
    subject: str,
    html_body: str,
    page_pngs: list,
    attachments: list,       # [(filename, bytes), ...]
) -> str:
    root = MIMEMultipart("mixed")
    root["To"] = ", ".join(to_list) if to_list else ""
    if bcc_list:
        root["Bcc"] = ", ".join(bcc_list)
    root["Subject"] = subject

    related = MIMEMultipart("related")
    related.attach(MIMEText(html_body, "html", "utf-8"))

    for i, png_bytes in enumerate(page_pngs):
        img = MIMEImage(png_bytes, "png")
        img.add_header("Content-ID", f"<pg{i}>")
        img.add_header("Content-Disposition", "inline")
        related.attach(img)

    root.attach(related)

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
    if st.button("Collega Gmail →", type="primary"):
        flow = _make_flow()
        if flow:
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
            st.session_state["pending_oauth"] = True
            st.markdown(
                f'<a href="{auth_url}" target="_self" style="'
                "display:inline-block;background:#c0392b;color:#fff;padding:0.55rem 1.4rem;"
                "border-radius:7px;text-decoration:none;font-weight:700;font-size:1rem;"
                '">→ Vai a Google per autorizzare</a>',
                unsafe_allow_html=True,
            )
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

page_pngs: list = []
html_body: str = ""

if main_pdf_file:
    if not PDF2IMAGE_OK:
        st.error("pdf2image non disponibile. Aggiungi `poppler-utils` in packages.txt.")
    else:
        with st.spinner("Conversione PDF → immagini…"):
            page_pngs = pdf_to_pngs(main_pdf_file.read())
            html_body = build_html(len(page_pngs))
        st.success(f"✅ PDF caricato — **{len(page_pngs)} pagina/e**  ·  `{main_pdf_file.name}`")
        with st.expander("🔍 Anteprima corpo mail", expanded=False):
            for i, png in enumerate(page_pngs):
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

    subject = st.text_input(
        "Oggetto della mail",
        placeholder="Report settimanale mercati — 23 maggio 2026",
        key="subject",
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

    ready = bool(page_pngs and html_body and subject.strip())
    if not ready:
        if not page_pngs:
            st.warning("⬆️ Carica prima il PDF principale (Step ①).")
        if not subject.strip():
            st.warning("✏️ Inserisci l'oggetto della mail.")
    else:
        st.markdown("")
        if st.button(
            f"📨  Crea {n_tot} bozze in Gmail",
            type="primary",
            use_container_width=True,
        ):
            progress = st.progress(0.0, text="Avvio…")
            status_box = st.empty()
            errors = []
            created = 0

            for i, rec in enumerate(recipient_rows):
                to_list  = rec["emails"] if rec["tipo"] == "to"  else []
                bcc_list = rec["emails"] if rec["tipo"] == "bcc" else []
                progress.progress(
                    (i + 1) / n_tot,
                    text=f"Bozza {i+1}/{n_tot} — {rec['nome_display']}",
                )
                try:
                    raw_msg = build_mime_message(
                        to_list, bcc_list, subject,
                        html_body, page_pngs, attachments_data,
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
                    f"Aprile [Gmail → Bozze](https://mail.google.com/mail/#drafts) per rivederle prima di inviare."
                )
                st.balloons()

    st.markdown("</div>", unsafe_allow_html=True)
