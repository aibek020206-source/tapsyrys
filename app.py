from flask import Flask, request, redirect, jsonify
import requests
import os
import secrets
import hashlib
import base64
from urllib.parse import urlencode

app = Flask(__name__)

# =========================
# CANVA CONFIGURATION
# =========================

CLIENT_ID = os.environ.get("CANVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "CANVA_REDIRECT_URI",
    "https://tapsyrys.onrender.com/canva/callback"
)

AUTH_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"

# Temporary storage for OAuth
oauth_sessions = {}


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return """
    <h1>Tapsyrys AI сервері жұмыс істеп тұр! ✅</h1>
    <p>Canva интеграциясы дайын.</p>
    <p><a href="/canva/login">Canva-ға қосылу</a></p>
    """


# =========================
# CANVA LOGIN
# =========================

@app.route("/canva/login")
def canva_login():

    if not CLIENT_ID:
        return """
        <h2>CANVA_CLIENT_ID табылмады ❌</h2>
        <p>Render → Environment ішіне CANVA_CLIENT_ID қосыңыз.</p>
        """

    # PKCE
    code_verifier = secrets.token_urlsafe(64)

    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(
            code_verifier.encode("utf-8")
        ).digest()
    ).decode("utf-8").rstrip("=")

    state = secrets.token_urlsafe(32)

    oauth_sessions[state] = {
        "code_verifier": code_verifier
    }

    params = {
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": state
    }

    # Қажетті permission-дер
    params["scope"] = "design:content:read design:content:write"

    url = AUTH_URL + "?" + urlencode(params)

    return redirect(url)


# =========================
# CANVA CALLBACK
# =========================

@app.route("/canva/callback")
def canva_callback():

    error = request.args.get("error")

    if error:
        description = request.args.get(
            "error_description",
            "Canva авторизациядан бас тартты."
        )

        return f"""
        <h2>Canva авторизация қатесі ❌</h2>
        <p>{description}</p>
        """

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return """
        <h2>Authorization code табылмады ❌</h2>
        <p>Canva-дан код келген жоқ.</p>
        """

    if not state or state not in oauth_sessions:
        return """
        <h2>State қатесі ❌</h2>
        <p>OAuth сессиясы табылмады.</p>
        """

    code_verifier = oauth_sessions[state]["code_verifier"]

    # Authorization code → access token
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code_verifier": code_verifier
    }

    try:
        response = requests.post(
            TOKEN_URL,
            data=data,
            timeout=30
        )

        result = response.json()

    except Exception as e:
        return f"""
        <h2>Canva серверіне қосылу қатесі ❌</h2>
        <p>{str(e)}</p>
        """

    if response.status_code != 200:
        return jsonify({
            "error": "Canva token error",
            "status": response.status_code,
            "response": result
        }), response.status_code

    access_token = result.get("access_token")

    if not access_token:
        return jsonify({
            "error": "Access token алынбады",
            "response": result
        }), 400

    # Demo үшін session ішінде сақтау
    oauth_sessions[state]["access_token"] = access_token

    return """
    <h1>Canva сәтті қосылды! ✅</h1>

    <p>Tapsyrys AI Canva аккаунтымен байланыстырылды.</p>

    <p>Енді келесі кезеңге өтуге болады:</p>

    <ul>
        <li>Презентация тақырыбын алу</li>
        <li>Слайд санын алу</li>
        <li>Canva дизайнын жасау</li>
        <li>Контент қосу</li>
        <li>Презентацияны дайындау</li>
    </ul>

    <a href="/">Басты бетке қайту</a>
    """


# =========================
# TEST CANVA CONNECTION
# =========================

@app.route("/canva/test")
def canva_test():

    token = None

    for session in oauth_sessions.values():
        if session.get("access_token"):
            token = session["access_token"]
            break

    if not token:
        return """
        <h2>Canva әлі қосылмаған ❌</h2>
        <a href="/canva/login">Canva-ға қосылу</a>
        """

    return jsonify({
        "status": "success",
        "message": "Canva access token бар ✅"
    })


# =========================
# PRESENTATION REQUEST
# =========================

@app.route("/presentation", methods=["POST"])
def presentation():

    data = request.get_json(silent=True) or {}

    topic = data.get("topic", "")
    slides = data.get("slides", 8)
    language = data.get("language", "Kazakh")

    if not topic:
        return jsonify({
            "error": "Тақырып енгізілмеген"
        }), 400

    return jsonify({
        "status": "received",
        "topic": topic,
        "slides": slides,
        "language": language,
        "message": "Презентация тапсырысы қабылданды ✅"
    })


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok"
    })


# =========================
# START SERVER
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
