from flask import Flask, request, redirect, jsonify
import os
import secrets
import hashlib
import base64
import requests

app = Flask(__name__)

# =========================
# SETTINGS
# =========================

CANVA_CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CANVA_CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")
CANVA_REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyrys.onrender.com/canva/callback"
)

# Уақытша PKCE verifier сақтау
pkce_store = {}


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return """
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Tapsyrys AI</title>
    </head>
    <body>
        <h1>Tapsyrys AI сервері жұмыс істеп тұр! ✅</h1>

        <p>Canva авторизациясын тексеру:</p>

        <a href="/canva/login">
            <button style="
                padding:15px 25px;
                font-size:18px;
                cursor:pointer;
            ">
                Canva-ға қосылу
            </button>
        </a>
    </body>
    </html>
    """


# =========================
# CANVA LOGIN
# =========================

@app.route("/canva/login")
def canva_login():

    if not CANVA_CLIENT_ID:
        return "CANVA_CLIENT_ID орнатылмаған ❌", 500

    # PKCE verifier
    code_verifier = secrets.token_urlsafe(64)

    # PKCE challenge
    digest = hashlib.sha256(
        code_verifier.encode("utf-8")
    ).digest()

    code_challenge = base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")

    # Бір реттік state
    state = secrets.token_urlsafe(32)

    # verifier-ді уақытша сақтау
    pkce_store[state] = code_verifier

    # Canva OAuth URL
    auth_url = (
        "https://www.canva.com/api/oauth/authorize"
        "?response_type=code"
        f"&client_id={CANVA_CLIENT_ID}"
        f"&redirect_uri={CANVA_REDIRECT_URI}"
        f"&code_challenge={code_challenge}"
        "&code_challenge_method=S256"
        f"&state={state}"
    )

    return redirect(auth_url)


# =========================
# CANVA CALLBACK
# =========================

@app.route("/canva/callback")
def canva_callback():

    error = request.args.get("error")

    if error:
        return f"""
        <h2>Canva авторизациясы қабылданбады ❌</h2>
        <p>{error}</p>
        """, 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return """
        <h2>Authorization code табылмады ❌</h2>
        <p>Canva-дан code келген жоқ.</p>
        """, 400

    if not state:
        return """
        <h2>State табылмады ❌</h2>
        """, 400

    # PKCE verifier
    code_verifier = pkce_store.pop(state, None)

    if not code_verifier:
        return """
        <h2>PKCE verifier табылмады ❌</h2>
        <p>Авторизацияны қайта бастаңыз.</p>
        """, 400

    # Canva token endpoint
    token_url = "https://api.canva.com/rest/v1/oauth/token"

    try:

        credentials = f"{CANVA_CLIENT_ID}:{CANVA_CLIENT_SECRET}"

        basic_auth = base64.b64encode(
            credentials.encode("utf-8")
        ).decode("utf-8")

        headers = {
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CANVA_REDIRECT_URI,
            "code_verifier": code_verifier
        }

        response = requests.post(
            token_url,
            headers=headers,
            data=data,
            timeout=30
        )

        if response.status_code != 200:
            return f"""
            <h2>Canva token қатесі ❌</h2>
            <pre>{response.text}</pre>
            """, response.status_code

        token_data = response.json()

        access_token = token_data.get("access_token")

        if not access_token:
            return """
            <h2>Access token алынбады ❌</h2>
            """, 500

        # Токенді URL-ға шығармаймыз
        return """
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Tapsyrys AI</title>
        </head>
        <body>
            <h1>Canva авторизациясы сәтті өтті! ✅</h1>

            <p>
                Tapsyrys AI Canva аккаунтымен байланыстырылды.
            </p>

            <p>
                Енді Canva арқылы келесі функцияларды қосуға болады:
            </p>

            <ul>
                <li>Презентация жасау</li>
                <li>Дизайн жасау</li>
                <li>Дизайн мазмұнын оқу</li>
            </ul>

            <p>Келесі қадам: Tapsyrys Android қосымшасына қосу.</p>
        </body>
        </html>
        """

    except Exception as e:

        return f"""
        <h2>Сервер қатесі ❌</h2>
        <pre>{str(e)}</pre>
        """, 500


# =========================
# TEST CANVA CONNECTION
# =========================

@app.route("/canva/test")
def canva_test():

    return jsonify({
        "server": "online",
        "canva": "OAuth дайын",
        "status": "OK"
    })


# =========================
# SERVER
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
