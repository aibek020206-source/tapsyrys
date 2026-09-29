import os
import secrets
import hashlib
import base64
import urllib.parse
import urllib.request
import urllib.error
import json

from flask import Flask, redirect, request, session

app = Flask(__name__)

# =========================================================
# CONFIG
# =========================================================

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
)

CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyryys.onrender.com/canva/callback"
)

CANVA_AUTHORIZE_URL = (
    "https://www.canva.com/api/oauth/authorize"
)

CANVA_TOKEN_URL = (
    "https://api.canva.com/rest/v1/oauth/token"
)

# Canva scopes
CANVA_SCOPE = os.environ.get(
    "CANVA_SCOPE",
    "design:content:read design:content:write"
)


# =========================================================
# PKCE
# =========================================================

def create_code_verifier():
    return secrets.token_urlsafe(64)


def create_code_challenge(verifier):
    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return """
    <!DOCTYPE html>
    <html lang="kk">
    <head>
        <meta charset="UTF-8">
        <title>Tapsyryys AI</title>

        <style>
            body {
                font-family: Arial, sans-serif;
                padding: 40px;
                background: #f5f7fb;
            }

            .box {
                max-width: 700px;
                margin: auto;
                background: white;
                padding: 35px;
                border-radius: 15px;
                box-shadow: 0 5px 25px rgba(0,0,0,0.08);
            }

            h1 {
                color: #222;
            }

            .btn {
                display: inline-block;
                padding: 14px 25px;
                background: #7c3aed;
                color: white;
                text-decoration: none;
                border-radius: 8px;
                font-size: 17px;
                border: none;
                cursor: pointer;
            }

            .btn:hover {
                opacity: 0.9;
            }
        </style>
    </head>

    <body>

        <div class="box">

            <h1>
                Tapsyryys AI сервисі жұмыс істеп тұр! ✅
            </h1>

            <p>
                Canva:
            </p>

            <a href="/canva/login" class="btn">
                Canva-ға кіру
            </a>

        </div>

    </body>
    </html>
    """


# =========================================================
# CANVA LOGIN
# =========================================================

@app.route("/canva/login")
def canva_login():

    if not CLIENT_ID:
        return """
        <h2>Қате</h2>
        <p>CANVA_CLIENT_ID Render Environment Variables ішінде жоқ.</p>
        """, 500

    # State
    state = secrets.token_urlsafe(32)

    # PKCE verifier
    verifier = create_code_verifier()

    # PKCE challenge
    challenge = create_code_challenge(verifier)

    # Save in session
    session["oauth_state"] = state
    session["code_verifier"] = verifier

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": CANVA_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }

    url = (
        CANVA_AUTHORIZE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    return redirect(url)


# =========================================================
# CANVA CALLBACK
# =========================================================

@app.route("/canva/callback")
def canva_callback():

    error = request.args.get("error")

    if error:

        description = request.args.get(
            "error_description",
            "Canva OAuth қатесі"
        )

        return f"""
        <h2>Canva OAuth қатесі ❌</h2>

        <p>
            <b>Error:</b> {error}
        </p>

        <p>
            <b>Description:</b> {description}
        </p>

        <p>
            <a href="/">Басты бетке қайту</a>
        </p>
        """, 400

    code = request.args.get("code")
    state = request.args.get("state")

    saved_state = session.get("oauth_state")
    verifier = session.get("code_verifier")

    # Check state
    if not state or state != saved_state:

        return """
        <h2>State қатесі ❌</h2>
        <p>OAuth state сәйкес келмейді.</p>
        """, 400

    if not code:

        return """
        <h2>Authorization code жоқ ❌</h2>
        """, 400

    if not verifier:

        return """
        <h2>PKCE verifier жоқ ❌</h2>
        """, 400

    # =====================================================
    # TOKEN REQUEST
    # =====================================================

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier
    }

    body = urllib.parse.urlencode(
        token_data
    ).encode("utf-8")

    headers = {
        "Content-Type":
            "application/x-www-form-urlencoded",
        "Accept":
            "application/json"
    }

    # Client secret, егер берілген болса
    if CLIENT_SECRET:

        basic = base64.b64encode(
            (
                CLIENT_ID
                + ":"
                + CLIENT_SECRET
            ).encode("utf-8")
        ).decode("utf-8")

        headers["Authorization"] = (
            "Basic " + basic
        )

    req = urllib.request.Request(
        CANVA_TOKEN_URL,
        data=body,
        headers=headers,
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=30
        ) as response:

            response_data = response.read().decode(
                "utf-8"
            )

            token = json.loads(
                response_data
            )

    except urllib.error.HTTPError as e:

        error_body = e.read().decode(
            "utf-8",
            errors="replace"
        )

        return f"""
        <h2>Canva Token қатесі ❌</h2>

        <p>
            HTTP: {e.code}
        </p>

        <pre>
        {error_body}
        </pre>

        <p>
            Redirect URI:
            <br>
            {REDIRECT_URI}
        </p>
        """, 400

    except Exception as e:

        return f"""
        <h2>Сервер қатесі ❌</h2>

        <pre>{str(e)}</pre>
        """, 500

    # Save token
    session["canva_token"] = token

    # Remove temporary OAuth data
    session.pop("oauth_state", None)
    session.pop("code_verifier", None)

    return """
    <!DOCTYPE html>
    <html lang="kk">

    <head>
        <meta charset="UTF-8">
        <title>Canva Connected</title>
    </head>

    <body>

        <div style="
            font-family:Arial;
            max-width:700px;
            margin:60px auto;
            text-align:center;
        ">

            <h1>
                Canva сәтті қосылды! ✅
            </h1>

            <p>
                Tapsyryys AI Canva аккаунтымен байланыстырылды.
            </p>

            <a href="/">
                Басты бетке қайту
            </a>

        </div>

    </body>

    </html>
    """


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "service": "Tapsyryys AI"
    }


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
