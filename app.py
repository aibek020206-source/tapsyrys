from flask import Flask, request, jsonify
import os
import anthropic

app = Flask(__name__)

# Claude API
client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY")
)


# =========================
# Негізгі бет
# =========================

@app.route("/")
def home():
    return "Tapsyrys AI сервері жұмыс істеп тұр! ✅"


# =========================
# Презентация жасау
# =========================

@app.route("/presentation", methods=["POST"])
def presentation():
    data = request.get_json() or {}

    topic = data.get("topic", "")
    slides = data.get("slides", 8)
    language = data.get("language", "Kazakh")

    if not topic:
        return jsonify({
            "error": "Тақырып енгізілмеген"
        }), 400

    prompt = f"""
You are an educational presentation assistant.

Create a clear educational presentation plan.

Topic: {topic}
Number of slides: {slides}
Language: {language}

For each slide provide:

1. Slide title
2. 3-5 short bullet points
3. Short speaker notes

The content should help a student understand and study the topic.
Do not invent sources or facts.
If factual information is uncertain, indicate that it should be verified.
"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        result = message.content[0].text

        return jsonify({
            "success": True,
            "result": result
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =========================
# Canva OAuth Callback
# =========================

@app.route("/canva/callback")
def canva_callback():

    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"""
        <h2>Canva авторизация қатесі ❌</h2>
        <p>{error}</p>
        """, 400

    if not code:
        return """
        <h2>Authorization code табылмады ❌</h2>
        <p>Canva-дан код келген жоқ.</p>
        """, 400

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Tapsyrys AI</title>
    </head>
    <body>
        <h2>Canva авторизациясы сәтті өтті! ✅</h2>
        <p>Tapsyrys AI Canva-мен байланыс орнатуға дайын.</p>
    </body>
    </html>
    """


# =========================
# Серверді іске қосу
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
