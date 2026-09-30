import base64
import hashlib
import html
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import re
from pathlib import Path

from flask import Flask, redirect, request, make_response, send_file

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# ============================================================
# SETTINGS
# ============================================================

CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyrys.onrender.com/canva/callback",
)

CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

# Keep the scopes that were already working in your Tapsyrys project.
SCOPES = os.environ.get(
    "CANVA_SCOPES",
    "design:content:read design:meta:read profile:read",
)

# Canva for AI assistants / MCP endpoint.
# If Canva gives you a different MCP URL in your developer project,
# put it into Render as CANVA_MCP_URL.
CANVA_MCP_URL = os.environ.get("CANVA_MCP_URL", "https://mcp.canva.com/mcp")
MCP_PROTOCOL_VERSION = os.environ.get("MCP_PROTOCOL_VERSION", "2025-06-18")

STATE_TTL = 600
DB_PATH = os.environ.get("DB_PATH", "app.db")

# Optional OpenAI fallback for generating text/content if Canva MCP is not used.
# The main presentation path below is Canva AI -> Canva design.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

SERVICES = {
    "Эссе": {"type": "fixed", "price": 250},
    "Реферат": {"type": "page", "price": 60},
    "Презентация": {"type": "page", "price": 60},
    "AI видео": {"type": "fixed", "price": 800},
    "Сайт": {"type": "fixed", "price": 1200},
    "Ойын": {"type": "question", "price": 15},
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS canva_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at REAL,
                scope TEXT,
                created_at REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                free_used INTEGER NOT NULL DEFAULT 0,
                presentation_free_used INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)

        # Migration for an older app.db that does not have the new column.
        try:
            conn.execute(
                "ALTER TABLE customers ADD COLUMN presentation_free_used INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                service TEXT NOT NULL,
                subject TEXT,
                topic TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                due_date TEXT,
                due_time TEXT,
                price INTEGER NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'pending',
                generation_status TEXT NOT NULL DEFAULT 'locked',
                canva_job_id TEXT,
                canva_candidate_id TEXT,
                canva_design_id TEXT,
                canva_edit_url TEXT,
                canva_view_url TEXT,
                canva_page_count INTEGER,
                error_message TEXT,
                created_at REAL NOT NULL
            )
        """)

        # Add Canva result columns to older orders tables.
        columns = {
            "canva_job_id": "TEXT",
            "canva_candidate_id": "TEXT",
            "canva_design_id": "TEXT",
            "canva_edit_url": "TEXT",
            "canva_view_url": "TEXT",
            "canva_page_count": "INTEGER",
            "error_message": "TEXT",
        }
        for name, definition in columns.items():
            try:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")
            except sqlite3.OperationalError:
                pass


def save_state(state, code_verifier):
    with db() as conn:
        conn.execute(
            "DELETE FROM oauth_states WHERE created_at < ?",
            (time.time() - STATE_TTL,),
        )
        conn.execute(
            "INSERT INTO oauth_states (state, code_verifier, created_at) VALUES (?, ?, ?)",
            (state, code_verifier, time.time()),
        )


def pop_state(state):
    with db() as conn:
        row = conn.execute(
            "SELECT code_verifier, created_at FROM oauth_states WHERE state = ?",
            (state,),
        ).fetchone()
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

    if not row or time.time() - row["created_at"] > STATE_TTL:
        return None
    return row["code_verifier"]


def save_tokens(token_data):
    expires_in = token_data.get("expires_in")
    expires_at = time.time() + expires_in if expires_in else None

    with db() as conn:
        conn.execute(
            """
            INSERT INTO canva_tokens
            (access_token, refresh_token, expires_at, scope, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                token_data["access_token"],
                token_data.get("refresh_token"),
                expires_at,
                token_data.get("scope"),
                time.time(),
            ),
        )


def get_latest_canva_token():
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM canva_tokens ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row


def get_customer(customer_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()


def ensure_customer(customer_id):
    with db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO customers
            (customer_id, free_used, presentation_free_used, created_at)
            VALUES (?, 0, 0, ?)
            """,
            (customer_id, time.time()),
        )


def create_customer():
    return secrets.token_urlsafe(24)


def calculate_price(service, quantity):
    item = SERVICES[service]
    if item["type"] == "fixed":
        return item["price"]
    return item["price"] * max(1, quantity)


def customer_id_from_request():
    return request.cookies.get("tapsyrys_customer_id")


init_db()


# ============================================================
# GENERIC HELPERS
# ============================================================

def json_response_body(raw):
    """Read normal JSON or an MCP SSE response and return a Python object."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = raw.strip()

    if not raw:
        return {}

    # Normal JSON response.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Streamable HTTP / SSE response.
    data_lines = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    if data_lines:
        for item in reversed(data_lines):
            try:
                return json.loads(item)
            except json.JSONDecodeError:
                continue

    raise RuntimeError("Canva MCP жауабы JSON ретінде оқылмады: " + raw[:1000])


def extract_tool_result(mcp_result):
    """Extract JSON from an MCP tools/call result."""
    if not isinstance(mcp_result, dict):
        return mcp_result

    if mcp_result.get("isError"):
        raise RuntimeError(json.dumps(mcp_result, ensure_ascii=False))

    # JSON-RPC result -> {content:[{type:'text',text:'...'}]}
    result = mcp_result.get("result", mcp_result)
    if isinstance(result, dict) and result.get("isError"):
        raise RuntimeError(json.dumps(result, ensure_ascii=False))

    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        text = "\n".join(texts).strip()
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Sometimes the tool returns plain text containing JSON.
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        pass
                return {"text": text}

    return result


def mcp_post(payload, access_token, session_id=None):
    headers = {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        CANVA_MCP_URL,
        data=data,
        method="POST",
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read()
            new_session = response.headers.get("Mcp-Session-Id")
            return json_response_body(body), new_session
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Canva MCP HTTP {e.code}: {body[:2000]}")


def canva_mcp_tool(tool_name, arguments):
    """
    Call a Canva AI assistant MCP tool.

    Flow:
      initialize -> initialized notification -> tools/call
    """
    token = get_latest_canva_token()
    if not token:
        raise RuntimeError("Canva аккаунты қосылмаған. Алдымен 'Canva-ға кіру' батырмасын басыңыз.")

    access_token = token["access_token"]

    initialize_payload = {
        "jsonrpc": "2.0",
        "id": secrets.randbelow(900000) + 100000,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": "Tapsyrys AI",
                "version": "1.0.0",
            },
        },
    }

    init_result, session_id = mcp_post(initialize_payload, access_token)

    # MCP notifications/initialized does not require a response.
    try:
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        mcp_post(notification, access_token, session_id)
    except Exception:
        # Some MCP servers accept initialize and tools/call without the notification.
        pass

    call_payload = {
        "jsonrpc": "2.0",
        "id": secrets.randbelow(900000) + 100000,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    result, _ = mcp_post(call_payload, access_token, session_id)

    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(json.dumps(result["error"], ensure_ascii=False))

    return extract_tool_result(result)


# ============================================================
# CANVA AI GENERATION
# ============================================================

def generate_canva_design(topic, subject, quantity):
    """Generate Canva AI candidates, then turn the first candidate into a real design."""
    quantity = max(1, min(int(quantity), 30))

    prompt = f"""Create an educational presentation in Kazakh language.
Subject: {subject or 'Not specified'}
Topic: {topic}
Number of pages/slides: {quantity}

Make it suitable for a university student. Use a clean modern academic presentation style.
Include a logical introduction, main part and conclusion.
Use concise readable Kazakh text and relevant visual structure.
Do not invent citations or sources.
"""

    generated = canva_mcp_tool(
        "generate-design",
        {"prompt": prompt},
    )

    # The documentation example shows:
    # job.id + job.result.generated_designs[].candidate_id
    job = generated.get("job") if isinstance(generated, dict) else None
    if not job:
        # Some wrappers may return the job object directly.
        job = generated if isinstance(generated, dict) else {}

    job_id = job.get("id")
    status = job.get("status")
    result = job.get("result") or {}
    candidates = result.get("generated_designs") or []

    if status not in (None, "success") and not candidates:
        raise RuntimeError(
            "Canva generate-design жұмысы әлі аяқталмаған немесе қате берді. "
            + json.dumps(job, ensure_ascii=False)[:2000]
        )

    if not job_id:
        raise RuntimeError("Canva generate-design жауабында job.id табылмады.")

    if not candidates:
        raise RuntimeError(
            "Canva generate-design жауабында generated_designs табылмады. "
            "Canva MCP құралының рұқсаттары мен scope-тарын тексеріңіз."
        )

    candidate = candidates[0]
    candidate_id = candidate.get("candidate_id")
    if not candidate_id:
        raise RuntimeError("Canva candidate_id табылмады.")

    created = canva_mcp_tool(
        "create-design-from-candidate",
        {
            "job_id": job_id,
            "candidate_id": candidate_id,
        },
    )

    summary = created.get("design_summary") if isinstance(created, dict) else None
    if not summary:
        raise RuntimeError(
            "create-design-from-candidate жауабында design_summary табылмады: "
            + json.dumps(created, ensure_ascii=False)[:2000]
        )

    urls = summary.get("urls") or {}

    return {
        "job_id": job_id,
        "candidate_id": candidate_id,
        "design_id": summary.get("id"),
        "title": summary.get("title") or topic,
        "edit_url": urls.get("edit_url"),
        "view_url": urls.get("view_url"),
        "page_count": summary.get("page_count"),
    }


# ============================================================
# OPTIONAL OPENAI CONTENT FALLBACK / PPTX
# ============================================================

def call_ai_for_slides(topic, subject, quantity):
    """Optional PPTX fallback. Canva AI remains the primary presentation generator."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY Render Environment Variable орнатылмаған.")

    quantity = max(1, min(int(quantity), 30))
    prompt = f"""Қазақ тілінде оқу үшін презентация дайында.
Пәні: {subject or 'көрсетілмеген'}
Тақырыбы: {topic}
Слайд саны: {quantity}

Тек JSON қайтар:
{{"title":"...","slides":[{{"title":"...","bullets":["...","..."]}}]}}
Әр слайдта 3-5 қысқа, түсінікті тармақ болсын.
"""

    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer " + OPENAI_API_KEY,
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))

    text_out = result.get("output_text", "").strip()
    if not text_out:
        chunks = []
        for item in result.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    chunks.append(c.get("text", ""))
        text_out = "".join(chunks).strip()

    text_out = re.sub(r"^```json\s*|\s*```$", "", text_out, flags=re.I)
    return json.loads(text_out)


def make_pptx(slides_data, order_id):
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError:
        raise RuntimeError("python-pptx орнатылмаған. requirements.txt ішіне python-pptx қосыңыз.")

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    title = slides_data.get("title", "Презентация")
    slides = slides_data.get("slides", [])

    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    if len(slide.placeholders) > 1:
        slide.placeholders[1].text = "Tapsyrys AI"

    for item in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = str(item.get("title", ""))
        tf = slide.placeholders[1].text_frame
        tf.clear()
        for i, bullet in enumerate(item.get("bullets", [])):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = str(bullet)
            p.font.size = Pt(22)

    out_dir = Path("generated")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"presentation_{order_id}.pptx"
    prs.save(path)
    return path


# ============================================================
# HTML
# ============================================================

def layout(title, body):
    return f"""<!doctype html>
<html lang="kk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#222}}
.box{{max-width:700px;margin:35px auto;padding:28px;background:#fff;border-radius:20px;box-shadow:0 5px 25px rgba(0,0,0,.08)}}
h1,h2{{margin-top:0}}
label{{display:block;margin:14px 0 6px;font-weight:600}}
input,select,textarea{{width:100%;box-sizing:border-box;padding:12px;border:1px solid #ddd;border-radius:10px;font-size:16px}}
textarea{{min-height:100px;resize:vertical}}
button,.button{{display:inline-block;border:0;margin-top:18px;padding:13px 20px;background:#7c3aed;color:#fff;border-radius:10px;font-size:16px;text-decoration:none;cursor:pointer}}
.secondary{{background:#555}}
.price{{font-size:24px;font-weight:bold;margin:18px 0}}
.free{{color:#138a3d;font-weight:bold}}
.note{{background:#f2efff;padding:12px;border-radius:10px;margin:15px 0}}
.error{{background:#ffecec;padding:12px;border-radius:10px;color:#a00}}
.success{{background:#eafaf0;padding:12px;border-radius:10px;color:#116b35}}
.row{{display:flex;gap:10px}}
.row>*{{flex:1}}
.url{{word-break:break-all}}
</style>
</head>
<body><div class="box">{body}</div></body></html>"""


def page(title, body, status=200):
    return layout(title, body), status


# ============================================================
# HOME / ORDERS
# ============================================================

@app.route("/")
def home():
    customer_id = customer_id_from_request()

    if not customer_id:
        customer_id = create_customer()
        ensure_customer(customer_id)
        resp = make_response(_home_html(customer_id))
        resp.set_cookie(
            "tapsyrys_customer_id",
            customer_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="Lax",
            secure=True,
        )
        return resp

    ensure_customer(customer_id)
    return _home_html(customer_id)


def _home_html(customer_id):
    customer = get_customer(customer_id)
    presentation_free_available = not bool(customer["presentation_free_used"])

    options = "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in SERVICES
    )

    free_text = (
        '<div class="note free">🎁 AI презентация жасауға 1 тегін мүмкіндік бар!</div>'
        if presentation_free_available
        else '<div class="note">🎁 Тегін AI презентация мүмкіндігі қолданылды. Келесі презентациялар — 60 тг/слайд.</div>'
    )

    return layout("Tapsyrys AI", f"""
<h1>Tapsyrys AI 🚀</h1>
<p>Тапсырысты таңдаңыз. AI автоматты генерациясы әзірге тек <b>Презентацияға</b> қосылған.</p>
{free_text}

<form method="post" action="/order">
<label>Қызмет</label>
<select name="service" id="service" onchange="updatePrice()" required>
{options}
</select>

<label>Пән</label>
<input name="subject" placeholder="Мысалы: Философия">

<label>Тақырып</label>
<textarea name="topic" placeholder="Мысалы: Болмыс және таным" required></textarea>

<label>Бет / слайд / сұрақ саны</label>
<input name="quantity" id="quantity" type="number" min="1" max="30" value="10" oninput="updatePrice()">

<div class="row">
<div>
<label>Күні</label>
<input name="due_date" type="date">
</div>
<div>
<label>Уақыты</label>
<input name="due_time" type="time">
</div>
</div>

<div class="price" id="price">Бағасы: 0 тг</div>
<button type="submit">Тапсырыс беру</button>
</form>

<hr style="margin:25px 0">
<a class="button secondary" href="/canva/login">Canva-ға кіру</a>

<script>
const services = {json.dumps(SERVICES, ensure_ascii=False)};
const presentationFreeAvailable = {str(presentation_free_available).lower()};

function updatePrice() {{
    const service = document.getElementById("service").value;
    const quantity = Math.max(1, parseInt(document.getElementById("quantity").value || "1"));
    let price = services[service].price;
    if (services[service].type !== "fixed") price *= quantity;

    if (service === "Презентация" && presentationFreeAvailable) {{
        document.getElementById("price").innerHTML =
            'Бағасы: <span class="free">0 тг — 1 тегін AI презентация 🎁</span>';
    }} else {{
        document.getElementById("price").textContent = "Бағасы: " + price + " тг";
    }}
}}
updatePrice();
</script>
""")


@app.route("/order", methods=["POST"])
def order():
    customer_id = customer_id_from_request()
    if not customer_id:
        return redirect("/")

    ensure_customer(customer_id)

    service = request.form.get("service", "")
    subject = request.form.get("subject", "").strip()
    topic = request.form.get("topic", "").strip()
    due_date = request.form.get("due_date", "")
    due_time = request.form.get("due_time", "")

    try:
        quantity = max(1, min(30, int(request.form.get("quantity", "1"))))
    except ValueError:
        quantity = 1

    if service not in SERVICES or not topic:
        return page(
            "Қате",
            '<h2>Мәлімет толық емес ❌</h2><a class="button" href="/">Қайту</a>',
            400,
        )

    customer = get_customer(customer_id)
    presentation_free_available = (
        service == "Презентация" and not bool(customer["presentation_free_used"])
    )

    normal_price = calculate_price(service, quantity)
    price = 0 if presentation_free_available else normal_price

    # Only presentation has the free attempt.
    payment_status = "free" if presentation_free_available else "pending"
    generation_status = "unlocked" if presentation_free_available else "locked"

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders
            (customer_id, service, subject, topic, quantity, due_date, due_time,
             price, payment_status, generation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id, service, subject, topic, quantity,
                due_date, due_time, price, payment_status,
                generation_status, time.time(),
            ),
        )
        order_id = cur.lastrowid

        if presentation_free_available:
            conn.execute(
                "UPDATE customers SET presentation_free_used = 1 WHERE customer_id = ?",
                (customer_id,),
            )

    if presentation_free_available:
        return redirect(f"/order/{order_id}/generation")

    return page(
        "Төлем",
        f"""
<h2>Тапсырыс #{order_id}</h2>
<p><b>Қызмет:</b> {html.escape(service)}</p>
<p><b>Тақырып:</b> {html.escape(topic)}</p>
<p><b>Саны:</b> {quantity}</p>
<div class="price">Төлем: {normal_price} тг</div>
<div class="note">Бұл әзірге <b>тесттік төлем</b>. Нақты ақша алынбайды.</div>
<form method="post" action="/order/{order_id}/test-payment">
<button type="submit">Тест төлемін растау</button>
</form>
<a class="button secondary" href="/">Басты бет</a>
""",
    )


@app.route("/order/<int:order_id>/test-payment", methods=["POST"])
def test_payment(order_id):
    customer_id = customer_id_from_request()

    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ).fetchone()

        if not order:
            return page("Қате", "<h2>Тапсырыс табылмады ❌</h2>", 404)

        conn.execute(
            """
            UPDATE orders
            SET payment_status = 'paid', generation_status = 'unlocked'
            WHERE id = ?
            """,
            (order_id,),
        )

    if order["service"] == "Презентация":
        return redirect(f"/order/{order_id}/generation")

    return page(
        "Тапсырыс қабылданды",
        f"""
<h2>Тапсырыс қабылданды ✅</h2>
<p><b>№:</b> {order_id}</p>
<p><b>Қызмет:</b> {html.escape(order['service'])}</p>
<p>Бұл қызметке автоматты AI генерациясы әзірге қосылмаған. Тапсырыс қабылданды.</p>
<a class="button" href="/">Басты бет</a>
""",
    )


@app.route("/order/<int:order_id>/generation")
def generation(order_id):
    customer_id = customer_id_from_request()

    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ).fetchone()

    if not order:
        return page("Қате", "<h2>Тапсырыс табылмады ❌</h2>", 404)

    if order["generation_status"] == "completed":
        return result_page(order)

    if order["service"] != "Презентация":
        return page(
            "Тапсырыс",
            f"""
<h2>Тапсырыс #{order_id}</h2>
<p>Қызмет: <b>{html.escape(order['service'])}</b></p>
<p>Бұл қызметке автоматты AI генерациясы әзірге қосылмаған.</p>
<a class="button" href="/">Басты бет</a>
""",
        )

    if order["generation_status"] != "unlocked":
        return page(
            "Құлыпталған",
            "<h2>Генерация жабық 🔒</h2><p>Алдымен төлемді растаңыз.</p>",
            403,
        )

    return page(
        "Canva AI генерациясы",
        f"""
<h2>Canva AI презентациясы ✨</h2>
<p><b>Тапсырыс:</b> #{order['id']}</p>
<p><b>Пән:</b> {html.escape(order['subject'] or 'Көрсетілмеген')}</p>
<p><b>Тақырып:</b> {html.escape(order['topic'])}</p>
<p><b>Слайд саны:</b> {order['quantity']}</p>

<div class="note">
Canva AI <b>generate-design</b> арқылы дизайн нұсқаларын жасайды,
содан кейін <b>create-design-from-candidate</b> арқылы нақты өңделетін Canva дизайны жасалады.
</div>

<form method="post" action="/order/{order_id}/generate">
<button type="submit">Canva AI арқылы жасау 🚀</button>
</form>
<a class="button secondary" href="/">Басты бет</a>
""",
    )


@app.route("/order/<int:order_id>/generate", methods=["POST"])
def generate(order_id):
    customer_id = customer_id_from_request()

    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ).fetchone()

    if not order or order["generation_status"] != "unlocked":
        return page("Қате", "<h2>Генерацияға рұқсат жоқ ❌</h2>", 403)

    if order["service"] != "Презентация":
        return page(
            "Тапсырыс",
            f"<h2>{html.escape(order['service'])}</h2>"
            "<p>Бұл қызметке әзірге автоматты AI генерация қосылмаған.</p>"
            "<a class='button' href='/'>Басты бет</a>",
        )

    try:
        with db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET generation_status = 'generating', error_message = NULL
                WHERE id = ?
                """,
                (order_id,),
            )

        # PRIMARY: Canva AI itself generates the design.
        result = generate_canva_design(
            order["topic"],
            order["subject"],
            order["quantity"],
        )

        with db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET generation_status = 'completed',
                    canva_job_id = ?,
                    canva_candidate_id = ?,
                    canva_design_id = ?,
                    canva_edit_url = ?,
                    canva_view_url = ?,
                    canva_page_count = ?
                WHERE id = ?
                """,
                (
                    result.get("job_id"),
                    result.get("candidate_id"),
                    result.get("design_id"),
                    result.get("edit_url"),
                    result.get("view_url"),
                    result.get("page_count"),
                    order_id,
                ),
            )

        with db() as conn:
            final_order = conn.execute(
                "SELECT * FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()

        return result_page(final_order)

    except Exception as e:
        message = str(e)
        with db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET generation_status = 'unlocked', error_message = ?
                WHERE id = ?
                """,
                (message[:4000], order_id),
            )

        return page(
            "Canva AI қатесі",
            f"""
<h2>Canva AI арқылы жасау кезінде қате ❌</h2>
<div class="error"><pre style="white-space:pre-wrap">{html.escape(message)}</pre></div>

<div class="note">
Егер қате <b>scope</b>, <b>permission</b>, <b>tools/call</b> немесе <b>MCP</b> туралы болса,
Canva Developer жобасындағы рұқсаттарды тексеру керек.
</div>
<a class="button" href="/order/{order_id}/generation">Қайта көру</a>
<a class="button secondary" href="/">Басты бет</a>
""",
            500,
        )


def result_page(order):
    edit_url = order["canva_edit_url"]
    view_url = order["canva_view_url"]
    page_count = order["canva_page_count"]

    links = ""
    if edit_url:
        links += f'<p><a class="button" href="{html.escape(edit_url, quote=True)}" target="_blank">Canva-да өңдеу ✏️</a></p>'
    if view_url:
        links += f'<p><a class="button secondary" href="{html.escape(view_url, quote=True)}" target="_blank">Көру 👀</a></p>'

    return page(
        "Дайын презентация",
        f"""
<h2>Презентация дайын! 🎉</h2>
<div class="success">
<b>Canva AI презентацияны жасады.</b>
</div>
<p><b>Тапсырыс:</b> #{order['id']}</p>
<p><b>Тақырып:</b> {html.escape(order['topic'])}</p>
<p><b>Canva design ID:</b> {html.escape(order['canva_design_id'] or '-')}</p>
<p><b>Слайд саны:</b> {html.escape(str(page_count or order['quantity']))}</p>
{links}
<hr>
<a class="button secondary" href="/">Басты бет</a>
""",
    )


@app.route("/download/<int:order_id>")
def download_presentation(order_id):
    """Kept for compatibility with the old PPTX route."""
    customer_id = customer_id_from_request()
    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ).fetchone()

    path = Path("generated") / f"presentation_{order_id}.pptx"
    if not order or order["service"] != "Презентация" or not path.exists():
        return page("Файл жоқ", "<h2>Файл табылмады ❌</h2>", 404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"Tapsyrys_presentation_{order_id}.pptx",
    )


# ============================================================
# CANVA OAUTH
# ============================================================

@app.route("/canva/login")
def canva_login():
    if not CLIENT_ID:
        return page(
            "Қате",
            "<h2>Қате ❌</h2><p>CANVA_CLIENT_ID табылмады.</p>",
            500,
        )

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = make_pkce()
    save_state(state, code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    return redirect(CANVA_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))


@app.route("/canva/callback")
def canva_callback():
    error = request.args.get("error")
    if error:
        description = request.args.get("error_description", "Canva OAuth қатесі")
        return page(
            "OAuth қатесі",
            f"""
<h2>Canva OAuth қатесі ❌</h2>
<p><b>Error:</b> {html.escape(error)}</p>
<p><b>Description:</b> {html.escape(description)}</p>
<a href="/">Басты бетке қайту</a>
""",
            400,
        )

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return page(
            "Код жоқ",
            "<h2>Authorization code жоқ ❌</h2><a href='/'>Басты бетке қайту</a>",
            400,
        )

    code_verifier = pop_state(state) if state else None
    if not code_verifier:
        return page(
            "State қатесі",
            "<h2>OAuth State қатесі ❌</h2><a href='/'>Басты бетке қайту</a>",
            400,
        )

    if not CLIENT_ID or not CLIENT_SECRET:
        return page(
            "Қате",
            "<h2>CANVA_CLIENT_ID немесе CANVA_CLIENT_SECRET жоқ ❌</h2>",
            500,
        )

    try:
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(
            credentials.encode("utf-8")
        ).decode("utf-8")

        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": code_verifier,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            CANVA_TOKEN_URL,
            data=data,
            method="POST",
        )
        req.add_header("Authorization", "Basic " + encoded_credentials)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=30) as response:
            result = response.read().decode("utf-8")

        token_data = json.loads(result)

        if not token_data.get("access_token"):
            safe = {
                k: v for k, v in token_data.items()
                if k in ("error", "error_description")
            }
            return page(
                "Токен алынбады",
                f"<pre>{html.escape(json.dumps(safe, indent=2, ensure_ascii=False))}</pre>",
                400,
            )

        save_tokens(token_data)

        return page(
            "Canva Connected",
            """
<h1>Canva сәтті қосылды! ✅</h1>
<p>Canva аккаунты Tapsyrys AI жүйесіне қосылды.</p>
<p>Енді презентация тапсырынында Canva AI генерациясын іске қосуға болады.</p>
<a class="button" href="/">Басты бетке қайту</a>
""",
        )

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        return page(
            "Token қатесі",
            f"""
<h2>Canva Token қатесі ❌</h2>
<p>HTTP: {e.code}</p>
<pre>{html.escape(error_body)}</pre>
<a href="/">Басты бетке қайту</a>
""",
            400,
        )

    except Exception as e:
        return page(
            "Сервер қатесі",
            f"<h2>Сервер қатесі ❌</h2><pre>{html.escape(str(e))}</pre>",
            500,
        )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    return {
        "status": "ok",
        "service": "Tapsyrys AI",
        "canva_mcp_url": CANVA_MCP_URL,
    }


def make_pkce():
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
