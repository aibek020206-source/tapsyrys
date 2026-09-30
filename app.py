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

from flask import Flask, redirect, request

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

# Canva Developer Portal-да осы scope-тар қосулы болуы керек
SCOPES = "design:content:read design:meta:read profile:read"

STATE_TTL = 600  # секунд (10 минут)

# Render-де тұрақты диск болса, DB_PATH-ты соған көрсетіңіз (мысалы /data/app.db)
DB_PATH = os.environ.get("DB_PATH", "app.db")


# ---------- Дерекқор (SQLite) ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canva_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at REAL,
                scope TEXT,
                created_at REAL NOT NULL
            )
            """
        )


def save_state(state, code_verifier):
    with db() as conn:
        # ескі state-терді тазалау
        conn.execute(
            "DELETE FROM oauth_states WHERE created_at < ?",
            (time.time() - STATE_TTL,),
        )
        conn.execute(
            "INSERT INTO oauth_states (state, code_verifier, created_at) VALUES (?, ?, ?)",
            (state, code_verifier, time.time()),
        )


def pop_state(state):
    """State-ті бір рет қана қолдануға болады. Жарамсыз болса None қайтарады."""
    with db() as conn:
        row = conn.execute(
            "SELECT code_verifier, created_at FROM oauth_states WHERE state = ?",
            (state,),
        ).fetchone()
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    if not row:
        return None
    if time.time() - row["created_at"] > STATE_TTL:
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


init_db()


# ---------- Көмекші функциялар ----------

def page(title, body, status=200):
    return (
        f"""<!DOCTYPE html>
<html lang="kk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        body {{ font-family: Arial, sans-serif; text-align: center;
               padding: 60px 20px; background: #f5f7fb; }}
        pre {{ text-align: left; background: #fff; padding: 16px;
              border-radius: 10px; overflow-x: auto; }}
    </style>
</head>
<body>
{body}
</body>
</html>""",
        status,
    )


def make_pkce():
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ---------- Route-тар ----------

@app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html lang="kk">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Tapsyrys AI</title>
        <style>
            body {
                margin: 0;
                padding: 40px 20px;
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                text-align: center;
            }
            .box {
                max-width: 600px;
                margin: auto;
                padding: 40px;
                background: white;
                border-radius: 20px;
                box-shadow: 0 5px 25px rgba(0,0,0,0.08);
            }
            h1 { color: #222; }
            p { color: #555; font-size: 18px; }
            .button {
                display: inline-block;
                margin-top: 20px;
                padding: 14px 30px;
                background: #7c3aed;
                color: white;
                text-decoration: none;
                border-radius: 10px;
                font-size: 18px;
            }
        </style>
    </head>
    <body>
        <div class="box">
            <h1>Tapsyrys AI 🚀</h1>
            <p>Canva аккаунтыңызды қосыңыз.</p>
            <a class="button" href="/canva/login">Canva-ға кіру</a>
        </div>
    </body>
    </html>
    """


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
            <p><b>Redirect URI:</b></p>
            <pre>{html.escape(REDIRECT_URI)}</pre>
            <a href="/">Басты бетке қайту</a>
            """,
            400,
        )

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return page(
            "Код жоқ",
            """
            <h2>Authorization code жоқ ❌</h2>
            <p>Canva код жібермеді.</p>
            <a href="/">Басты бетке қайту</a>
            """,
            400,
        )

    code_verifier = pop_state(state) if state else None
    if not code_verifier:
        return page(
            "State қатесі",
            """
            <h2>OAuth State қатесі ❌</h2>
            <p>Сессия ескірген немесе жарамсыз. Қайтадан кіріп көріңіз.</p>
            <a href="/">Басты бетке қайту</a>
            """,
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

        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": code_verifier,
            }
        ).encode("utf-8")

        req = urllib.request.Request(CANVA_TOKEN_URL, data=data, method="POST")
        req.add_header("Authorization", "Basic " + encoded_credentials)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=30) as response:
            result = response.read().decode("utf-8")

        token_data = json.loads(result)

        if not token_data.get("access_token"):
            # Токендерді бетке шығармаймыз, тек қате кодын көрсетеміз
            safe = {k: v for k, v in token_data.items() if k in ("error", "error_description")}
            return page(
                "Токен алынбады",
                f"""
                <h2>Access token алынбады ❌</h2>
                <pre>{html.escape(json.dumps(safe, indent=2, ensure_ascii=False))}</pre>
                <a href="/">Басты бетке қайту</a>
                """,
                400,
            )

        save_tokens(token_data)

        return page(
            "Canva Connected",
            """
            <h1>Canva сәтті қосылды! ✅</h1>
            <p>Canva аккаунты Tapsyrys AI жүйесіне қосылды.</p>
            <a href="/">Басты бетке қайту</a>
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
            <p>Redirect URI:</p>
            <pre>{html.escape(REDIRECT_URI)}</pre>
            <a href="/">Басты бетке қайту</a>
            """,
            400,
        )

    except Exception as e:
        return page(
            "Сервер қатесі",
            f"""
            <h2>Сервер қатесі ❌</h2>
            <pre>{html.escape(str(e))}</pre>
            <a href="/">Басты бетке қайту</a>
            """,
            500,
        )


@app.route("/health")
def health():
    return {"status": "ok", "service": "Tapsyrys AI"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
