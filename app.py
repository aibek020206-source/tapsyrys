from flask import Flask, request, redirect
import os
import urllib.parse
import urllib.request
import urllib.error
import base64
import json
import secrets

app = Flask(__name__)

# =========================================================
# CANVA CONFIGURATION
# =========================================================

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

# Уақытша OAuth state сақтау
oauth_states = set()

# Қарапайым түрде токенді жадыда сақтау
access_token = None


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
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <title>Tapsyryys AI</title>

        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                margin: 0;
                padding: 40px;
                text-align: center;
            }

            .box {
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
                background: #7c3aed;
                color: white;
                text-decoration: none;
                border-radius: 10px;
                font-size: 18px;
            }

            .button:hover {
                opacity: 0.9;
            }
        </style>
    </head>

    <body>

        <div class="box">

            <h1>
                Tapsyryys AI сервері жұмыс істеп тұр! ✅
            </h1>

            <p>
                Canva аккаунтын қосып, дизайндармен жұмыс істеуге болады.
            </p>

            <a class="button" href="/canva/login">
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
        <h2>Қате ❌</h2>
        <p>CANVA_CLIENT_ID Render Environment Variables ішінде жоқ.</p>
        """, 500

    # OAuth state
    state = secrets.token_urlsafe(32)
    oauth_states.add(state)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state
    }

    url = CANVA_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    return redirect(url)


# =========================================================
# CANVA CALLBACK
# =========================================================

@app.route("/canva/callback")
def canva_callback():

    global access_token

    # Canva error жіберсе
    error = request.args.get("error")

    if error:
        description = request.args.get(
            "error_description",
            "Белгісіз қате"
        )

        return f"""
        <!DOCTYPE html>
        <html lang="kk">
        <head>
            <meta charset="UTF-8">
            <title>Canva OAuth қатесі</title>
        </head>

        <body style="font-family:Arial;padding:40px">

            <h2>Canva OAuth қатесі ❌</h2>

            <p>
                <b>Error:</b> {error}
            </p>

            <p>
                <b>Description:</b> {description}
            </p>

            <br>

            <a href="/">
                Басты бетке қайту
            </a>

        </body>
        </html>
        """, 400

    # Authorization code
    code = request.args.get("code")

    if not code:
        return """
        <h2>Canva қатесі ❌</h2>
        <p>Authorization code табылмады.</p>
        <a href="/">Басты бетке қайту</a>
        """, 400

    # State тексеру
    state = request.args.get("state")

    if state and state not in oauth_states:
        return """
        <h2>Қауіпсіздік қатесі ❌</h2>
        <p>OAuth state сәйкес келмейді.</p>
        <a href="/">Басты бетке қайту</a>
        """, 400

    if state:
        oauth_states.discard(state)

    # Environment variables тексеру
    if not CLIENT_ID:
        return """
        <h2>Қате ❌</h2>
        <p>CANVA_CLIENT_ID орнатылмаған.</p>
        """, 500

    if not CLIENT_SECRET:
        return """
        <h2>Қате ❌</h2>
        <p>CANVA_CLIENT_SECRET орнатылмаған.</p>
        """, 500

    # =====================================================
    # TOKEN EXCHANGE
    # =====================================================

    try:

        # Canva OAuth үшін Basic Authentication
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"

        basic_auth = base64.b64encode(
            credentials.encode("utf-8")
        ).decode("utf-8")

        token_data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        }).encode("utf-8")

        req = urllib.request.Request(
            CANVA_TOKEN_URL,
            data=token_data,
            method="POST"
        )

        req.add_header(
            "Authorization",
            f"Basic {basic_auth}"
        )

        req.add_header(
            "Content-Type",
            "application/x-www-form-urlencoded"
        )

        req.add_header(
            "Accept",
            "application/json"
        )

        with urllib.request.urlopen(req, timeout=30) as response:

            response_data = response.read().decode("utf-8")

            token_response = json.loads(response_data)

        access_token = token_response.get("access_token")

        if not access_token:

            return f"""
            <h2>Token қатесі ❌</h2>

            <pre>
            {json.dumps(token_response, indent=2, ensure_ascii=False)}
            </pre>

            <a href="/">
                Басты бетке қайту
            </a>
            """, 400

        # =================================================
        # SUCCESS
        # =================================================

        return """
        <!DOCTYPE html>
        <html lang="kk">

        <head>
            <meta charset="UTF-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1.0">

            <title>Canva қосылды</title>

            <style>
                body {
                    font-family: Arial, sans-serif;
                    background: #f5f7fb;
                    text-align: center;
                    padding: 60px 20px;
                }

                .box {
                    max-width: 600px;
                    margin: auto;
                    background: white;
                    padding: 40px;
                    border-radius: 20px;
                    box-shadow: 0 5px 25px rgba(0,0,0,0.08);
                }

                .success {
                    font-size: 60px;
                }

                h1 {
                    color: #222;
                }

                p {
                    color: #555;
                    font-size: 18px;
                }

                a {
                    display: inline-block;
                    margin-top: 20px;
                    padding: 13px 25px;
                    background: #7c3aed;
                    color: white;
                    text-decoration: none;
                    border-radius: 10px;
                }
            </style>

        </head>

        <body>

            <div class="box">

                <div class="success">✅</div>

                <h1>
                    Canva сәтті қосылды!
                </h1>

                <p>
                    Canva аккаунтыңыз Tapsyryys AI жүйесіне қосылды.
                </p>

                <a href="/">
                    Басты бетке қайту
                </a>

            </div>

        </body>
        </html>
        """

    # =====================================================
    # ERRORS
    # =====================================================

    except urllib.error.HTTPError as e:

        error_body = e.read().decode(
            "utf-8",
            errors="ignore"
        )

        return f"""
        <!DOCTYPE html>
        <html lang="kk">

        <head>
            <meta charset="UTF-8">
            <title>Canva қатесі</title>
        </head>

        <body style="font-family:Arial;padding:40px">

            <h2>Canva Token қатесі ❌</h2>

            <p>
                HTTP қатесі: {e.code}
            </p>

            <pre>
            {error_body}
            </pre>

            <a href="/">
                Басты бетке қайту
            </a>

        </body>
        </html>
        """, 400

    except Exception as e:

        return f"""
        <!DOCTYPE html>
        <html lang="kk">

        <head>
            <meta charset="UTF-8">
            <title>Сервер қатесі</title>
        </head>

        <body style="font-family:Arial;padding:40px">

            <h2>Сервер қатесі ❌</h2>

            <pre>
            {str(e)}
            </pre>

            <a href="/">
                Басты бетке қайту
            </a>

        </body>
        </html>
        """, 500


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
# START SERVER
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
