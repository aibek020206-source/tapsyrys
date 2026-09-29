import os
import secrets
import base64
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Flask, redirect, request, session, jsonify

app = Flask(__name__)

# ==========================================
# SECRET KEY
# ==========================================

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-secret-key-123456"
)


# ==========================================
# CANVA CONFIGURATION
# ==========================================

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


# ==========================================
# HOME
# ==========================================

@app.route("/")
def home():

    return """
    <!DOCTYPE html>
    <html lang="kk">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">

        <title>Tapsyryys AI</title>

        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                margin: 0;
                padding: 50px;
                text-align: center;
            }

            .container {
                max-width: 700px;
                margin: auto;
                background: white;
                padding: 40px;
                border-radius: 20px;
                box-shadow: 0 5px 25px rgba(0,0,0,0.08);
            }

            h1 {
                color: #222;
            }

            p {
                color: #555;
                font-size: 18px;
            }

            .button {
                display: inline-block;
                margin-top: 20px;
                padding: 14px 28px;
                background: #00c4cc;
                color: white;
                text-decoration: none;
                border-radius: 10px;
                font-size: 18px;
            }

            .button:hover {
                background: #00aeb5;
            }

            .status {
                margin-top: 25px;
                padding: 15px;
                background: #f1f1f1;
                border-radius: 10px;
            }
        </style>
    </head>

    <body>

        <div class="container">

            <h1>
                Tapsyryys AI сервері жұмыс істеп тұр! ✅
            </h1>

            <p>Canva:</p>

            <a class="button" href="/canva/login">
                Canva-ға кіру
            </a>

            <div class="status">
                <p>
                    Canva аккаунтыңызды қосу үшін
                    жоғарыдағы батырманы басыңыз.
                </p>
            </div>

        </div>

    </body>
    </html>
    """


# ==========================================
# CANVA LOGIN
# ==========================================

@app.route("/canva/login")
def canva_login():

    if not CLIENT_ID:
        return """
        <h2>Қате ❌</h2>
        <p>CANVA_CLIENT_ID Render Environment Variables ішінде жоқ.</p>
        """, 500

    # OAuth state
    state = secrets.token_urlsafe(32)

    # Session-ға сақтау
    session["canva_state"] = state

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": state
    }

    auth_url = (
        CANVA_AUTHORIZE_URL
        + "?"
        + urlencode(params)
    )

    return redirect(auth_url)


# ==========================================
# CANVA CALLBACK
# ==========================================

@app.route("/canva/callback")
def canva_callback():

    # Canva error жіберсе
    error = request.args.get("error")

    if error:

        error_description = request.args.get(
            "error_description",
            "Canva OAuth қатесі"
        )

        return f"""
        <h2>Canva OAuth қатесі ❌</h2>

        <p>
            <b>Error:</b> {error}
        </p>

        <p>
            <b>Description:</b> {error_description}
        </p>

        <br>

        <a href="/">
            Басты бетке қайту
        </a>
        """, 400


    # Authorization code
    code = request.args.get("code")

    if not code:
        return """
        <h2>Қате ❌</h2>
        <p>Canva authorization code жібермеді.</p>
        """, 400


    # State тексеру
    received_state = request.args.get("state")
    saved_state = session.get("canva_state")

    if not received_state or received_state != saved_state:

        return """
        <h2>Қауіпсіздік қатесі ❌</h2>

        <p>
            OAuth state сәйкес келмейді.
        </p>
        """, 400


    # Бір рет қолданылған state-ті өшіреміз
    session.pop("canva_state", None)


    # Client ID / Secret тексеру
    if not CLIENT_ID or not CLIENT_SECRET:

        return """
        <h2>Configuration error ❌</h2>

        <p>
            CANVA_CLIENT_ID немесе CANVA_CLIENT_SECRET
            Render Environment Variables ішінде жоқ.
        </p>
        """, 500


    # ======================================
    # ACCESS TOKEN АЛУ
    # ======================================

    credentials = (
        CLIENT_ID
        + ":"
        + CLIENT_SECRET
    )

    encoded_credentials = base64.b64encode(
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
        method="POST"
    )


    token_request.add_header(
        "Authorization",
        "Basic " + encoded_credentials
    )

    token_request.add_header(
        "Content-Type",
        "application/x-www-form-urlencoded"
    )


    try:

        with urlopen(token_request, timeout=30) as response:

            response_body = response.read().decode("utf-8")

            token_response = json.loads(response_body)


    except HTTPError as e:

        error_body = e.read().decode("utf-8", errors="ignore")

        return f"""
        <h2>Canva Token қатесі ❌</h2>

        <p>
            <b>HTTP:</b> {e.code}
        </p>

        <pre>
        {error_body}
        </pre>

        <a href="/">
            Басты бетке қайту
        </a>
        """, 400


    except URLError as e:

        return f"""
        <h2>Интернет/API қатесі ❌</h2>

        <p>
            {str(e)}
        </p>
        """, 500


    # ======================================
    # TOKEN-ді SESSION-ға сақтау
    # ======================================

    access_token = token_response.get("access_token")

    if not access_token:

        return f"""
        <h2>Access token алынбады ❌</h2>

        <pre>
        {json.dumps(
            token_response,
            indent=2,
            ensure_ascii=False
        )}
        </pre>
        """, 400


    session["canva_access_token"] = access_token


    # Refresh token болса
    if token_response.get("refresh_token"):

        session["canva_refresh_token"] = (
            token_response["refresh_token"]
        )


    return """
    <!DOCTYPE html>
    <html lang="kk">

    <head>
        <meta charset="UTF-8">

        <title>Canva қосылды</title>

        <style>

            body {
                font-family: Arial;
                background: #f5f7fb;
                text-align: center;
                padding: 60px;
            }

            .box {
                background: white;
                max-width: 600px;
                margin: auto;
                padding: 40px;
                border-radius: 20px;
                box-shadow: 0 5px 25px rgba(0,0,0,0.08);
            }

            h1 {
                color: #20a463;
            }

            a {
                display: inline-block;
                margin-top: 20px;
                padding: 12px 25px;
                background: #00c4cc;
                color: white;
                text-decoration: none;
                border-radius: 10px;
            }

        </style>

    </head>

    <body>

        <div class="box">

            <h1>
                Canva сәтті қосылды! ✅
            </h1>

            <p>
                Canva аккаунтыңыз Tapsyryys AI
                серверіне қосылды.
            </p>

            <a href="/">
                Басты бетке қайту
            </a>

        </div>

    </body>

    </html>
    """


# ==========================================
# CANVA STATUS
# ==========================================

@app.route("/canva/status")
def canva_status():

    token = session.get("canva_access_token")

    if token:

        return jsonify({
            "connected": True,
            "message": "Canva аккаунты қосылған ✅"
        })

    return jsonify({
        "connected": False,
        "message": "Canva аккаунты қосылмаған"
    })


# ==========================================
# LOGOUT
# ==========================================

@app.route("/canva/logout")
def canva_logout():

    session.pop("canva_access_token", None)
    session.pop("canva_refresh_token", None)

    return redirect("/")


# ==========================================
# HEALTH CHECK
# ==========================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "service": "Tapsyryys AI"
    })


# ==========================================
# RUN
# ==========================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
