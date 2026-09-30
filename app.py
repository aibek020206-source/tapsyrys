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

