# demo/sample_repo/app.py
from flask import Flask, request, jsonify
from auth import authenticate

app = Flask(__name__)

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data or "email" not in data or "password" not in data:
        return jsonify({"error": "email and password required"}), 400
    user = authenticate(data["email"], data["password"])
    if user is None:
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"user_id": user.id, "email": user.email}), 200

@app.route("/health")
def health():
    return jsonify({"status": "ok"})
