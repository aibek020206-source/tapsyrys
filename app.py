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

from flask import Flask, redirect, request, make_response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyrys.onrender.com/canva/callback",
)

CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

SCOPES = "design:content:read design:meta:read profile:read"

STATE_TTL = 600
DB_PATH = os.environ.get("DB_PATH", "app.db")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

PRESENTATION_PRICE_PER_SLIDE = 60


# ---------- DB ----------

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
                created_at REAL NOT NULL
            )
        """)


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


def get_customer(customer_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    return row


def ensure_customer(customer_id):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO customers (customer_id, free_used, presentation_free_used, created_at) VALUES (?, 0, 0, ?)",
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


# ---------- AI PRESENTATION GENERATOR ----------

def call_ai_for_slides(topic, subject, quantity):
    """Create slide content with an OpenAI-compatible API."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY Render Environment Variable орнатылмаған.")

    quantity = max(1, min(int(quantity), 30))
    prompt = f"""Қазақ тілінде оқу үшін презентация дайында.
Пәні: {subject or 'көрсетілмеген'}
Тақырыбы: {topic}
Слайд саны: {quantity}

Тек JSON қайтар. Формат:
{{"title":"...","slides":[{{"title":"...","bullets":["...","..."]}}]}}
Әр слайдта 3-5 қысқа, түсінікті тармақ болсын. Кіріспе, негізгі бөлім және қорытынды логикасы сақталсын. Артық түсіндірме жазба."""

    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "temperature": 0.4,
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
        # fallback for Responses API output structure
        chunks = []
        for item in result.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    chunks.append(c.get("text", ""))
        text_out = "".join(chunks).strip()

    text_out = re.sub(r"^```json\s*|\s*```$", "", text_out, flags=re.I)
    return json.loads(text_out)


def make_pptx(slides_data, order_id):
    """Build a PPTX file from generated slide content."""
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

    # Title slide
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    if slide.placeholders[1]:
        slide.placeholders[1].text = "Tapsyrys AI"

    for item in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = str(item.get("title", ""))
        tf = slide.placeholders[1].text_frame
        tf.clear()
        bullets = item.get("bullets", [])
        for i, bullet in enumerate(bullets):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = str(bullet)
            p.font.size = Pt(22)
            p.level = 0

    out_dir = Path("generated")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"presentation_{order_id}.pptx"
    prs.save(path)
    return path


# ---------- HTML ----------

def layout(title, body):
    return f"""<!doctype html>
<html lang="kk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#222}}
.box{{max-width:650px;margin:35px auto;padding:28px;background:#fff;border-radius:20px;box-shadow:0 5px 25px rgba(0,0,0,.08)}}
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
.row{{display:flex;gap:10px}}
.row>*{{flex:1}}
</style>
</head>
<body><div class="box">{body}</div></body></html>"""


def page(title, body, status=200):
    return layout(title, body), status


# ---------- Main ----------

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
    presentation_free_available = not customer["presentation_free_used"]

    if presentation_free_available:
        free_text = '<div class="note free">🎁 Сізге 1 тегін AI презентация жасау мүмкіндігі берілді!</div>'
        initial_price = "0 тг — 1 тегін AI презентация 🎁"
    else:
        free_text = '<div class="note">Тегін AI презентация мүмкіндігі қолданылды. Келесі презентация — 60 тг/слайд.</div>'
        initial_price = "600 тг"

    return layout("Tapsyrys AI", f"""
<h1>Tapsyrys AI 🚀</h1>
<p>AI арқылы презентация жасаңыз.</p>
{free_text}

<form method="post" action="/order">
<input type="hidden" name="service" value="Презентация">

<label>Пән</label>
<input name="subject" placeholder="Мысалы: Философия">

<label>Презентация тақырыбы</label>
<textarea name="topic" placeholder="Мысалы: Болмыс және таным" required></textarea>

<label>Слайд саны</label>
<input name="quantity" id="quantity" type="number" min="1" max="30" value="10" oninput="updatePrice()" required>

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

<div class="price" id="price">Бағасы: {initial_price}</div>
<button type="submit">Презентация жасау ✨</button>
</form>

<hr style="margin:25px 0">
<a class="button secondary" href="/canva/login">Canva-ға кіру</a>

<script>
const freeAvailable = {str(presentation_free_available).lower()};
function updatePrice() {{
    const quantity = Math.max(1, Math.min(30, parseInt(document.getElementById("quantity").value || "1")));
    if (freeAvailable) {{
        document.getElementById("price").innerHTML = 'Бағасы: <span class="free">0 тг — 1 тегін AI презентация 🎁</span>';
    }} else {{
        document.getElementById("price").textContent = "Бағасы: " + (quantity * 60) + " тг";
    }}
}}
updatePrice();
</script>
""")


# ---------- Orders / test payment ----------

@app.route("/order", methods=["POST"])
def order():
    customer_id = customer_id_from_request()
    if not customer_id:
        return redirect("/")

    ensure_customer(customer_id)

    subject = request.form.get("subject", "").strip()
    topic = request.form.get("topic", "").strip()
    due_date = request.form.get("due_date", "")
    due_time = request.form.get("due_time", "")

    try:
        quantity = max(1, min(30, int(request.form.get("quantity", "10"))))
    except ValueError:
        quantity = 10

    if not topic:
        return page(
            "Қате",
            '<h2>Тақырыпты енгізіңіз ❌</h2><a class="button" href="/">Қайту</a>',
            400,
        )

    customer = get_customer(customer_id)
    presentation_free_available = not customer["presentation_free_used"]
    normal_price = PRESENTATION_PRICE_PER_SLIDE * quantity
    price = 0 if presentation_free_available else normal_price

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders
            (customer_id, service, subject, topic, quantity, due_date, due_time,
             price, payment_status, generation_status, created_at)
            VALUES (?, 'Презентация', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id, subject, topic, quantity, due_date, due_time,
                price,
                "free" if presentation_free_available else "pending",
                "unlocked" if presentation_free_available else "locked",
                time.time(),
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
<h2>Презентация #{order_id}</h2>
<p><b>Тақырып:</b> {html.escape(topic)}</p>
<p><b>Слайд саны:</b> {quantity}</p>
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

    return redirect(f"/order/{order_id}/generation")


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

    if order["generation_status"] != "unlocked":
        return page(
            "Құлыпталған",
            "<h2>Генерация жабық 🔒</h2><p>Алдымен төлемді растаңыз.</p>",
            403,
        )

    return page(
        "Генерация",
        f"""
<h2>Генерация дайын ✨</h2>
<p><b>Тапсырыс:</b> #{order["id"]}</p>
<p><b>Қызмет:</b> {html.escape(order["service"])}</p>
<p><b>Тақырып:</b> {html.escape(order["topic"])}</p>

<div class="note">
Бұл батырма әзірге генерация процесінің тесттік кезеңін көрсетеді.
Canva AI-дың нақты автоматты генерация API-ін бөлек қосу қажет.
</div>

<form method="post" action="/order/{order_id}/generate">
<button type="submit">Генерация жасау ✨</button>
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

    try:
        with db() as conn:
            conn.execute(
                "UPDATE orders SET generation_status = 'generating' WHERE id = ?",
                (order_id,),
            )

        slides_data = call_ai_for_slides(
            order["topic"], order["subject"], order["quantity"]
        )
        pptx_path = make_pptx(slides_data, order_id)

        with db() as conn:
            conn.execute(
                "UPDATE orders SET generation_status = 'completed' WHERE id = ?",
                (order_id,),
            )

        # Keep generated files under the public Flask path.
        download_url = f"/download/{order_id}"
        return page(
            "Дайын презентация",
            f"""
            <h2>Презентация дайын! 🎉</h2>
            <p><b>Тапсырыс:</b> #{order_id}</p>
            <p><b>Тақырып:</b> {html.escape(order['topic'])}</p>
            <div class="note">AI презентацияны автоматты түрде жасады.</div>
            <a class="button" href="{download_url}">PPTX жүктеу 📥</a>
            <br><a class="button secondary" href="/">Басты бет</a>
            """,
        )
    except Exception as e:
        with db() as conn:
            conn.execute(
                "UPDATE orders SET generation_status = 'unlocked' WHERE id = ?",
                (order_id,),
            )
        return page(
            "Генерация қатесі",
            f"<h2>Презентация жасау кезінде қате ❌</h2><pre>{html.escape(str(e))}</pre>"
            "<a class='button' href='/'>Басты бет</a>",
            500,
        )


@app.route("/download/<int:order_id>")
def download_presentation(order_id):
    from flask import send_file
    customer_id = customer_id_from_request()
    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ).fetchone()
    path = Path("generated") / f"presentation_{order_id}.pptx"
    if not order or order["service"] != "Презентация" or order["generation_status"] != "completed" or not path.exists():
        return page("Файл жоқ", "<h2>Файл табылмады ❌</h2>", 404)
    return send_file(path, as_attachment=True, download_name=f"Tapsyrys_presentation_{order_id}.pptx")


# ---------- Canva OAuth ----------

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

    return redirect(
        CANVA_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    )


@app.route("/canva/callback")
def canva_callback():
    error = request.args.get("error")
    if error:
        description = request.args.get(
            "error_description", "Canva OAuth қатесі"
        )
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
            CANVA_TOKEN_URL, data=data, method="POST"
        )
        req.add_header("Authorization", "Basic " + encoded_credentials)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=30) as response:
            result = response.read().decode("utf-8")

        token_data = json.loads(result)

        if not token_data.get("access_token"):
            safe = {
                k: v
                for k, v in token_data.items()
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


@app.route("/health")
def health():
    return {"status": "ok", "service": "Tapsyrys AI"}


def make_pkce():
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
