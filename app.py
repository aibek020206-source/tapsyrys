from flask import Flask, request, jsonify
import os
import anthropic

app = Flask(__name__)

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY")
)

@app.route("/")
def home():
    return "Tapsyrys AI server is running!"

@app.route("/presentation", methods=["POST"])
def presentation():
    data = request.json

    topic = data.get("topic", "")
    slides = data.get("slides", 8)
    language = data.get("language", "Kazakh")

    prompt = f"""
You are an educational presentation assistant.

Create a clear presentation plan and slide-by-slide educational content.

Topic: {topic}
Number of slides: {slides}
Language: {language}

For each slide provide:
1. Slide title
2. 3-5 short bullet points
3. Short speaker notes

Do not invent sources or facts. If factual information is uncertain,
say that it should be verified.

The result should help a student understand and prepare the topic.
"""

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return jsonify({
        "result": message.content[0].text
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
