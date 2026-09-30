import base64
import hashlib
import html
import json
import os
import secrets
import sqlite3
import time
import urllib.parse

import requests
from flask import Flask, redirect, request, make_response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# =========================
# CANVA SETTINGS
# =========================
CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")
REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyrys.onrender.com/canva/callback",
)

CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

# Canva MCP endpoint. If Canva gives you a different MCP endpoint, set
# CANVA_MCP_URL in Render Environment Variables.
CANVA_MCP_URL = os.environ.get("CANVA_MCP_URL", "https://mcp.canva.com/mcp")

# Keep the OAuth scopes that you already enabled in Canva Developer.
SCOPES = os.environ.get(
    "CANVA_SCOPES",
    "design:content:read design:meta:read profile:read",
)

STATE_TTL = 600
DB_PATH = os.environ.get("DB_PATH", "app.db")

# =========================
# SERVICES / PRICES
# =========================
SERVICES = {
    "Эссе": {"type": "fixed", "price": 250},
    "Реферат": {"type": "page", "price": 60},
    "Презентация": {"type": "page", "price": 60},
    "AI видео": {"type": "fixed", "price": 800},
    "Сайт": {"type": "fixed", "price": 1200},
    "Ойын": {"type": "question", "price": 15},
}


# =========================
# DATABASE
# =========================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(conn, table, column, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass


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
        add_column_if_missing(
            conn,
            "customers",
            "presentation_free_used",
            "INTEGER NOT NULL DEFAULT 0",
        )

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

        # New Canva result fields. Safe for an existing database.
        add_column_if_missing(conn, "orders", "canva_design_id", "TEXT")
        add_column_if_missing(conn, "orders", "canva_edit_url", "TEXT")
        add_column_if_missing(conn, "orders", "canva_view_url", "TEXT")
        add_column_if_missing(conn, "orders", "canva_page_count", "INTEGER")
        add_column_if_missing(conn, "orders", "error_message", "TEXT")


init_db()


# =========================
# CUSTOMER / PRICE HELPERS
# =========================
def create_customer():
    return secrets.token_urlsafe(24)


def customer_id_from_request():
    return request.cookies.get("tapsyrys_customer_id")


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


def get_customer(customer_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()


def calculate_price(service, quantity):
    item = SERVICES[service]
    if item["type"] == "fixed":
        return item["price"]
    return item["price"] * max(1, quantity)


# =========================
# HTML
# =========================
def layout(title, body):
    return f"""<!doctype html>
<html lang="kk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#222}}
.box{{max-width:720px;margin:35px auto;padding:28px;background:#fff;border-radius:20px;box-shadow:0 5px 25px rgba(0,0,0,.08)}}
h1,h2{{margin-top:0}}
label{{display:block;margin:14px 0 6px;font-weight:600}}
input,select,textarea{{width:100%;box-sizing:border-box;padding:12px;border:1px solid #ddd;border-radius:10px;font-size:16px}}
textarea{{min-height:100px;resize:vertical}}
button,.button{{display:inline-block;border:0;margin-top:18px;padding:13px 20px;background:#7c3aed;color:#fff;border-radius:10px;font-size:16px;text-decoration:none;cursor:pointer}}
.secondary{{background:#555}}
.green{{background:#16803c}}
.price{{font-size:24px;font-weight:bold;margin:18px 0}}
.free{{color:#138a3d;font-weight:bold}}
.note{{background:#f2efff;padding:12px;border-radius:10px;margin:15px 0}}
.error{{background:#ffecec;padding:12px;border-radius:10px;color:#a00;overflow:auto}}
.success{{background:#eaf8ee;padding:12px;border-radius:10px;color:#176b31}}
.row{{display:flex;gap:10px}}
.row>*{{flex:1}}
hr{{border:0;border-top:1px solid #eee;margin:25px 0}}
a{{color:#5b21b6}}
</style>
</head>
<body><div class="box">{body}</div></body>
</html>"""


def page(title, body, status=200):
    return layout(title, body), status


# =========================
# PKCE / OAUTH
# =========================
def make_pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


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
    expires_at = time.time() + float(expires_in) if expires_in else None

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


# =========================
# MCP HELPERS
# =========================
def parse_mcp_http_response(response):
    """Parse normal JSON or SSE response from an MCP HTTP server."""
    content_type = (response.headers.get("Content-Type") or "").lower()

    if "text/event-stream" not in content_type:
        try:
            return response.json()
        except Exception:
            text = response.text.strip()
            try:
                return json.loads(text)
            except Exception:
                raise RuntimeError(
                    f"Canva MCP JSON емес жауап қайтарды: {text[:1000]}"
                )

    # Streamable HTTP may return SSE. Take the last JSON data message.
    last_obj = None
    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            last_obj = json.loads(data)
        except Exception:
            continue

    if last_obj is None:
        raise RuntimeError(
            "Canva MCP SSE жауабынан JSON табылмады: "
            + response.text[:1000]
        )
    return last_obj


def mcp_post(method, params, access_token, session_id=None, request_id=1):
    headers = {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }

    response = requests.post(
        CANVA_MCP_URL,
        headers=headers,
        json=payload,
        timeout=90,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Canva MCP HTTP {response.status_code}: {response.text[:2000]}"
        )

    result = parse_mcp_http_response(response)
    new_session = response.headers.get("Mcp-Session-Id") or response.headers.get(
        "MCP-Session-Id"
    )
    return result, new_session


def mcp_initialize(access_token):
    result, session_id = mcp_post(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "Tapsyrys AI",
                "version": "1.0.0",
            },
        },
        access_token,
        request_id=1,
    )

    if result.get("error"):
        raise RuntimeError("Canva MCP initialize: " + json.dumps(result["error"], ensure_ascii=False))

    # MCP initialization is followed by this notification.
    headers = {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    requests.post(
        CANVA_MCP_URL,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        timeout=30,
    )

    return session_id


def mcp_call_tool(access_token, session_id, tool_name, arguments, request_id):
    result, new_session = mcp_post(
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments,
        },
        access_token,
        session_id=session_id,
        request_id=request_id,
    )

    session_id = new_session or session_id

    if result.get("error"):
        raise RuntimeError(
            f"MCP {tool_name}: "
            + json.dumps(result["error"], ensure_ascii=False)
        )

    return result, session_id


def extract_text_from_mcp(result):
    """Extract JSON/text from MCP tools/call result."""
    # Most MCP servers put tool output in result.content[].text.
    tool_result = result.get("result", result)
    contents = tool_result.get("content", []) if isinstance(tool_result, dict) else []

    texts = []
    for item in contents:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(item.get("text", ""))

    if texts:
        joined = "\n".join(texts).strip()
        # The Canva tool normally returns JSON as the text body.
        try:
            return json.loads(joined)
        except Exception:
            # Sometimes there is extra text around the JSON.
            start = joined.find("{")
            end = joined.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(joined[start:end + 1])
                except Exception:
                    pass
            return {"raw_text": joined}

    # Some servers may expose structured content directly.
    structured = tool_result.get("structuredContent") if isinstance(tool_result, dict) else None
    if structured is not None:
        return structured

    return tool_result


def find_generated_designs(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("generated_designs"), list):
            return obj["generated_designs"]
        for value in obj.values():
            found = find_generated_designs(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_generated_designs(value)
            if found is not None:
                return found
    return None


def find_job_id(obj):
    if isinstance(obj, dict):
        job = obj.get("job")
        if isinstance(job, dict) and job.get("id"):
            return job["id"]
        if obj.get("job_id"):
            return obj["job_id"]
        # Some response versions expose the top-level id as id.
        if obj.get("id") and not str(obj["id"]).startswith("dg-"):
            return obj["id"]
        for value in obj.values():
            found = find_job_id(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_job_id(value)
            if found:
                return found
    return None


def find_design_summary(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("design_summary"), dict):
            return obj["design_summary"]
        for value in obj.values():
            found = find_design_summary(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_design_summary(value)
            if found is not None:
                return found
    return None


def canva_generate_presentation(topic, subject, quantity):
    token_row = get_latest_canva_token()
    if not token_row:
        raise RuntimeError(
            "Canva аккаунты қосылмаған. Алдымен 'Canva-ға кіру' батырмасын басыңыз."
        )

    access_token = token_row["access_token"]
    session_id = mcp_initialize(access_token)

    query = f"""
Қазақ тілінде студентке арналған презентация жаса.
Пәні: {subject or 'көрсетілмеген'}
Тақырыбы: {topic}
Слайд саны: {max(1, min(int(quantity), 50))}

Презентация оқу жұмысына арналған болсын.
Құрылымы түсінікті болсын: тақырып, кіріспе, негізгі бөлім, қорытынды.
Мәтін қысқа әрі түсінікті болсын.
""".strip()

    # THIS fixes the exact error from the screenshot:
    # generate-design requires BOTH query and design_type.
    generate_arguments = {
        "query": query,
        "design_type": "presentation",
    }

    generate_result, session_id = mcp_call_tool(
        access_token,
        session_id,
        "generate-design",
        generate_arguments,
        request_id=2,
    )

    generated = extract_text_from_mcp(generate_result)
    if isinstance(generated, dict) and generated.get("isError"):
        raise RuntimeError(json.dumps(generated, ensure_ascii=False))

    candidates = find_generated_designs(generated)
    job_id = find_job_id(generated)

    if not candidates:
        raise RuntimeError(
            "Canva generate-design generated_designs қайтармады. "
            + json.dumps(generated, ensure_ascii=False)[:3000]
        )

    first = candidates[0]
    if not isinstance(first, dict) or not first.get("candidate_id"):
        raise RuntimeError(
            "Canva candidate_id қайтармады: "
            + json.dumps(first, ensure_ascii=False)
        )

    candidate_id = first["candidate_id"]

    if not job_id:
        raise RuntimeError(
            "Canva generate-design жауабынан top-level job.id табылмады. "
            "Canva жауабы: " + json.dumps(generated, ensure_ascii=False)[:3000]
        )

    # Turn the selected candidate into a real editable Canva design.
    create_arguments = {
        "candidate_id": candidate_id,
        "job_id": job_id,
    }

    create_result, _ = mcp_call_tool(
        access_token,
        session_id,
        "create-design-from-candidate",
        create_arguments,
        request_id=3,
    )

    created = extract_text_from_mcp(create_result)
    if isinstance(created, dict) and created.get("isError"):
        raise RuntimeError(json.dumps(created, ensure_ascii=False))

    summary = find_design_summary(created)
    if not summary:
        raise RuntimeError(
            "Canva create-design-from-candidate design_summary қайтармады. "
            + json.dumps(created, ensure_ascii=False)[:3000]
        )

    urls = summary.get("urls") or {}
    return {
        "id": summary.get("id"),
        "title": summary.get("title"),
        "edit_url": urls.get("edit_url"),
        "view_url": urls.get("view_url"),
        "page_count": summary.get("page_count"),
    }


# =========================
# HOME
# =========================
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
<p>Тапсырыс беріңіз.</p>
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
<input name="quantity" id="quantity" type="number" min="1" value="10" oninput="updatePrice()">

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

<hr>
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
            'Бағасы: <span class="free">0 тг — 1-ші AI презентация тегін 🎁</span>';
    }} else {{
        document.getElementById("price").textContent = "Бағасы: " + price + " тг";
    }}
}}
updatePrice();
</script>
""")


# =========================
# ORDER
# =========================
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
        quantity = max(1, int(request.form.get("quantity", "1")))
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

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders
            (customer_id, service, subject, topic, quantity, due_date, due_time,
             price, payment_status, generation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                service,
                subject,
                topic,
                quantity,
                due_date,
                due_time,
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
<h2>Тапсырыс #{order_id}</h2>
<p><b>Қызмет:</b> {html.escape(service)}</p>
<p><b>Тақырып:</b> {html.escape(topic)}</p>
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


# =========================
# GENERATION
# =========================
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

    if order["generation_status"] != "unlocked":
        return page(
            "Құлыпталған",
            "<h2>Генерация жабық 🔒</h2><p>Алдымен төлемді растаңыз.</p>",
            403,
        )

    if order["service"] != "Презентация":
        return page(
            "Тапсырыс қабылданды",
            f"""
<h2>Тапсырыс қабылданды ✅</h2>
<p><b>Қызмет:</b> {html.escape(order['service'])}</p>
<p>Бұл қызметке автоматты Canva AI генерациясы әзірге қосылмаған.</p>
<a class="button secondary" href="/">Басты бет</a>
""",
        )

    return page(
        "Canva AI",
        f"""
<h2>Canva AI арқылы презентация ✨</h2>
<p><b>Тапсырыс:</b> #{order['id']}</p>
<p><b>Тақырып:</b> {html.escape(order['topic'])}</p>
<p><b>Слайд саны:</b> {order['quantity']}</p>
<div class="note">
Canva AI өзі дизайн жасап, дайын нәтижені Canva-да өңдеуге болатын сілтемемен қайтарады.
</div>
<form method="post" action="/order/{order_id}/generate">
<button type="submit">Canva AI-мен жасау ✨</button>
</form>
<a class="button secondary" href="/">Басты бет</a>
""",
    )


def result_page(order):
    edit_url = order["canva_edit_url"]
    view_url = order["canva_view_url"]
    page_count = order["canva_page_count"]

    links = ""
    if edit_url:
        links += f'<a class="button" target="_blank" href="{html.escape(edit_url, quote=True)}">Canva-да өңдеу ✏️</a>'
    if view_url:
        links += f'<a class="button secondary" target="_blank" href="{html.escape(view_url, quote=True)}">Презентацияны көру 👀</a>'

    return page(
        "Презентация дайын",
        f"""
<h2>Презентация дайын! 🎉</h2>
<p><b>Тапсырыс:</b> #{order['id']}</p>
<p><b>Тақырып:</b> {html.escape(order['topic'])}</p>
{f'<p><b>Слайд саны:</b> {page_count}</p>' if page_count else ''}
<div class="success">Canva AI презентацияны жасап, editable Canva design құрды.</div>
{links}
<br>
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
            "<p>Бұл қызметке автоматты Canva AI генерациясы әзірге қосылмаған.</p>"
            "<a class='button' href='/'>Басты бет</a>",
        )

    try:
        with db() as conn:
            conn.execute(
                "UPDATE orders SET generation_status = 'generating', error_message = NULL WHERE id = ?",
                (order_id,),
            )

        result = canva_generate_presentation(
            order["topic"],
            order["subject"],
            order["quantity"],
        )

        if not result.get("edit_url") and not result.get("view_url"):
            raise RuntimeError(
                "Canva дизайн URL қайтармады: "
                + json.dumps(result, ensure_ascii=False)
            )

        with db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET generation_status = 'completed',
                    canva_design_id = ?,
                    canva_edit_url = ?,
                    canva_view_url = ?,
                    canva_page_count = ?,
                    error_message = NULL
                WHERE id = ?
                """,
                (
                    result.get("id"),
                    result.get("edit_url"),
                    result.get("view_url"),
                    result.get("page_count"),
                    order_id,
                ),
            )

        with db() as conn:
            updated = conn.execute(
                "SELECT * FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()

        return result_page(updated)

    except Exception as e:
        error_text = str(e)
        with db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET generation_status = 'unlocked', error_message = ?
                WHERE id = ?
                """,
                (error_text[:8000], order_id),
            )

        return page(
            "Canva AI қатесі",
            f"""
<h2>Canva AI арқылы жасау кезінде қате ❌</h2>
<div class="error"><pre>{html.escape(error_text)}</pre></div>
<div class="note">
Егер қате <b>query Required</b> немесе <b>design_type Required</b> болса, бұл код оларды generate-design-ге жібереді.
Сондықтан ондай қате шықса, Canva Developer-дегі tool/permission немесе MCP endpoint параметрін тексеру керек.
</div>
<a class="button" href="/order/{order_id}/generation">Қайта көру</a>
<a class="button secondary" href="/">Басты бет</a>
""",
            500,
        )


# =========================
# CANVA OAUTH
# =========================
@app.route("/canva/login")
def canva_login():
    if not CLIENT_ID:
        return page(
            "Қате",
            "<h2>CANVA_CLIENT_ID табылмады ❌</h2>",
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
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

        response = requests.post(
            CANVA_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": code_verifier,
            },
            headers={
                "Authorization": "Basic " + encoded_credentials,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=30,
        )

        if response.status_code >= 400:
            return page(
                "Token қатесі",
                f"<h2>Canva Token қатесі ❌</h2><pre>{html.escape(response.text[:4000])}</pre>",
                400,
            )

        token_data = response.json()
        if not token_data.get("access_token"):
            return page(
                "Токен алынбады",
                f"<pre>{html.escape(json.dumps(token_data, indent=2, ensure_ascii=False))}</pre>",
                400,
            )

        save_tokens(token_data)

        return page(
            "Canva Connected",
            """
<h1>Canva сәтті қосылды! ✅</h1>
<p>Canva аккаунты Tapsyrys AI жүйесіне қосылды.</p>
<p>Енді презентация тапсырысына кіріп, <b>Canva AI-мен жасау</b> батырмасын басыңыз.</p>
<a class="button" href="/">Басты бетке қайту</a>
""",
        )

    except requests.RequestException as e:
        return page(
            "Сервер қатесі",
            f"<h2>Canva OAuth сұранысы сәтсіз ❌</h2><pre>{html.escape(str(e))}</pre>",
            500,
        )
    except Exception as e:
        return page(
            "Сервер қатесі",
            f"<h2>Сервер қатесі ❌</h2><pre>{html.escape(str(e))}</pre>",
            500,
        )


@app.route("/health")
def health():
    return {
        "status": "ok",
        "service": "Tapsyrys AI",
        "canva_mcp_url": CANVA_MCP_URL,
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
