from flask import Flask, redirect, request
import os
import secrets
import base64
import urllib.parse
import urllib.request
import urllib.error
import json

app = Flask(__name__)

# ==========================================
# CANVA SETTINGS
# ==========================================

CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyrys.onrender.com/canva/callback"
)

CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"

CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"


# OAuth state
oauth_states = set()

# Access token
access_token = None


# ==========================================
# HOME PAGE
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

        <title>Tapsyrys AI</title>

        <style>

            body {
                margin: 0;
                padding: 40px;
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                text-align: center;
            }

            .box {
                max-width: 650px;
                margin: auto;
                padding: 40px;
                background: white;
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
                padding: 14px 30px;
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
                Tapsyrys AI сервері жұмыс істеп тұр! ✅
            </h1>

            <p>
                Canva аккаунтыңызды қосыңыз.
            </p>

            <a
                class="button"
                href="/canva/login"
            >
                Canva-ға кіру
            </a>

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

        <p>
            CANVA_CLIENT_ID табылмады.
        </p>

        <p>
            Render → Environment Variables
            бөлімін тексеріңіз.
        </p>
        """, 500


    # OAuth state
    state = secrets.token_urlsafe(32)

    oauth_states.add(state)


    # Canva authorization parameters
    params = {

        "client_id": CLIENT_ID,

        "redirect_uri": REDIRECT_URI,

        "response_type": "code",

        "state": state

    }


    authorization_url = (
        CANVA_AUTHORIZE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )


    return redirect(authorization_url)


# ==========================================
# CANVA CALLBACK
# ==========================================

@app.route("/canva/callback")
def canva_callback():

    global access_token


    # --------------------------------------
    # CANVA ERROR
    # --------------------------------------

    error = request.args.get("error")

    if error:

        description = request.args.get(
            "error_description",
            "Canva OAuth қатесі"
        )

        return f"""
        <!DOCTYPE html>

        <html lang="kk">

        <head>
            <meta charset="UTF-8">
            <title>Canva Error</title>
        </head>

        <body style="
            font-family:Arial;
            padding:40px;
        ">

            <h2>
                Canva OAuth қатесі ❌
            </h2>

            <p>
                <b>Error:</b>
                {error}
            </p>

            <p>
                <b>Description:</b>
                {description}
            </p>

            <p>
                Redirect URI:
            </p>

            <pre>
{REDIRECT_URI}
            </pre>

            <a href="/">
                Басты бетке қайту
            </a>

        </body>

        </html>
        """, 400


    # --------------------------------------
    # AUTHORIZATION CODE
    # --------------------------------------

    code = request.args.get("code")

    if not code:

        return """
        <h2>Authorization code жоқ ❌</h2>

        <p>
            Canva authorization code жібермеді.
        </p>

        <a href="/">
            Басты бетке қайту
        </a>
        """, 400


    # --------------------------------------
    # STATE
    # --------------------------------------

    state = request.args.get("state")

    if state and state not in oauth_states:

        return """
        <h2>OAuth State қатесі ❌</h2>

        <p>
            State сәйкес келмейді.
        </p>

        <a href="/">
            Басты бетке қайту
        </a>
        """, 400


    if state:

        oauth_states.discard(state)


    # --------------------------------------
    # CHECK CREDENTIALS
    # --------------------------------------

    if not CLIENT_ID:

        return """
        <h2>CANVA_CLIENT_ID жоқ ❌</h2>
        """, 500


    if not CLIENT_SECRET:

        return """
        <h2>CANVA_CLIENT_SECRET жоқ ❌</h2>
        """, 500


    # ======================================
    # TOKEN REQUEST
    # ======================================

    try:

        credentials = (
            CLIENT_ID
            + ":"
            + CLIENT_SECRET
        )


        encoded_credentials = base64.b64encode(
            credentials.encode("utf-8")
        ).decode("utf-8")


        token_data = urllib.parse.urlencode({

            "grant_type":
                "authorization_code",

            "code":
                code,

            "redirect_uri":
                REDIRECT_URI

        }).encode("utf-8")


        token_request = urllib.request.Request(

            CANVA_TOKEN_URL,

            data=token_data,

            method="POST"

        )


        token_request.add_header(

            "Authorization",

            "Basic "
            + encoded_credentials

        )


        token_request.add_header(

            "Content-Type",

            "application/x-www-form-urlencoded"

        )


        token_request.add_header(

            "Accept",

            "application/json"

        )


        with urllib.request.urlopen(
            token_request,
            timeout=30
        ) as response:

            response_data = (
                response
                .read()
                .decode("utf-8")
            )


        token_json = json.loads(
            response_data
        )


        access_token = token_json.get(
            "access_token"
        )


        # ----------------------------------
        # TOKEN ERROR
        # ----------------------------------

        if not access_token:

            return f"""
            <h2>
                Access token алынбады ❌
            </h2>

            <pre>
{json.dumps(
    token_json,
    indent=2,
    ensure_ascii=False
)}
            </pre>

            <a href="/">
                Басты бетке қайту
            </a>
            """, 400


        # ----------------------------------
        # SUCCESS
        # ----------------------------------

        return """
        <!DOCTYPE html>

        <html lang="kk">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width,
                initial-scale=1.0"
            >

            <title>Canva Connected</title>

        </head>

        <body style="
            font-family:Arial;
            text-align:center;
            padding:60px 20px;
        ">

            <h1>
                Canva сәтті қосылды! ✅
            </h1>

            <p>
                Canva аккаунты
                Tapsyrys AI жүйесіне қосылды.
            </p>

            <br>

            <a href="/">
                Басты бетке қайту
            </a>

        </body>

        </html>
        """


    # ======================================
    # HTTP ERROR
    # ======================================

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

            <title>Canva Error</title>

        </head>

        <body style="
            font-family:Arial;
            padding:40px;
        ">

            <h2>
                Canva Token қатесі ❌
            </h2>

            <p>
                HTTP:
                {e.code}
            </p>

            <pre>
{error_body}
            </pre>

            <p>
                Redirect URI:
            </p>

            <pre>
{REDIRECT_URI}
            </pre>

            <a href="/">
                Басты бетке қайту
            </a>

        </body>

        </html>
        """, 400


    # ======================================
    # OTHER ERROR
    # ======================================

    except Exception as e:

        return f"""
        <h2>
            Сервер қатесі ❌
        </h2>

        <pre>
{str(e)}
        </pre>

        <a href="/">
            Басты бетке қайту
        </a>
        """, 500


# ==========================================
# HEALTH CHECK
# ==========================================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "service": "Tapsyrys AI"
    }


# ==========================================
# RUN
# ==========================================

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
