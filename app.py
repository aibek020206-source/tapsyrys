from flask import Flask, request, redirect, jsonify
import os
import secrets
import base64
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json

app = Flask(__name__)

# ==============================
# CANVA CONFIGURATION
# ==============================

CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyryys.onrender.com/canva/callback"
)

CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

# Temporary storage for OAuth state
oauth_states = set()


# ==============================
# HOME
# ==============================

@app.route("/")
def home():
    return """
    <h2>Tapsyrys AI сервері жұмыс істеп тұр! ✅</h2>

    <p>Canva:</p>

    <a href="/canva/login">
        <button style="
            padding:12px 25px;
            font-size:18px;
            cursor:pointer;
        ">
            Canva-ға кіру
        </button>
    </a>
    """


# ==============================
# CANVA LOGIN
# ==============================

@app.route("/canva/login")
def canva_login():

    if not CLIENT_ID:
        return """
        <h3>❌ CANVA_CLIENT_ID табылмады</h3>
        <p>Render → Environment Variables бөлімін тексеріңіз.</p>
        """, 500

    state = secrets.token_urlsafe(32)
    oauth_states.add(state)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "design:content:read design:content:write",
        "state": state
    }

    url = CANVA_AUTHORIZE_URL + "?" + urlencode(params)

    return redirect(url)


# ==============================
# CANVA CALLBACK
# ==============================

@app.route("/canva/callback")
def canva_callback():

    error = request.args.get("error")

    if error:
        description = request.args.get(
            "error_description",
            "Белгісіз қате"
        )

        return f"""
        <h2>❌ Canva авторизация қатесі</h2>
        <p><b>Error:</b> {error}</p>
        <p>{description}</p>
        """, 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return """
        <h2>Authorization code табылмады ❌</h2>
        <p>Canva-дан код келген жоқ.</p>
        """, 400

    if not state or state not in oauth_states:
        return """
        <h2>Invalid state ❌</h2>
        <p>Авторизация қауіпсіздік тексеруінен өтпеді.</p>
        """, 400

    oauth_states.discard(state)

    if not CLIENT_ID or not CLIENT_SECRET:
        return """
        <h2>❌ Canva credentials табылмады</h2>
        <p>Render Environment Variables бөлімінен:</p>
        <ul>
            <li>CANVA_CLIENT_ID</li>
            <li>CANVA_CLIENT_SECRET</li>
        </ul>
        тексеріңіз.
        """, 500

    # ==========================
    # TOKEN REQUEST
    # ==========================

    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"

    basic_auth = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    token_data = urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }).encode("utf-8")

    token_request = Request(
        CANVA_TOKEN_URL,
        data=token_data,
        method="POST",
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
    )

    try:
        with urlopen(token_request, timeout=30) as response:
            response_data = response.read().decode("utf-8")

        token_json = json.loads(response_data)

    except HTTPError as e:

        error_body = e.read().decode("utf-8", errors="replace")

        return f"""
        <h2>❌ Canva Token қатесі</h2>

        <p><b>HTTP:</b> {e.code}</p>

        <pre>{error_body}</pre>
        """, 400

    except URLError as e:

        return f"""
        <h2>❌ Canva серверіне қосылу қатесі</h2>

        <p>{e}</p>
        """, 500

    except Exception as e:

        return f"""
        <h2>❌ Қате</h2>

        <pre>{e}</pre>
        """, 500

    access_token = token_json.get("access_token")

    if not access_token:

        return f"""
        <h2>❌ Access token алынбады</h2>

        <pre>{json.dumps(token_json, indent=2, ensure_ascii=False)}</pre>
        """, 400

    return f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Canva Connected</title>
    </head>

    <body style="
        font-family: Arial;
        padding: 40px;
        text-align: center;
    ">

        <h1>Canva сәтті қосылды! ✅</h1>

        <p>Authorization code қабылданды.</p>

        <p>
            Access token сәтті алынды.
        </p>

        <p style="color:green;">
            Tapsyrys AI Canva интеграциясы жұмыс істеп тұр.
        </p>

    </body>
    </html>
    """


# ==============================
# HEALTH CHECK
# ==============================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "Tapsyrys AI"
    })


# ==============================
# START
# ==============================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
