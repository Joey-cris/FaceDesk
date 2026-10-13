"""Vercel web entry point for FaceDesk.

This does not run the Tkinter desktop application.
It provides a simple web dashboard/API for FaceDesk.
"""

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

HOME_PAGE = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FaceDesk</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            margin: 0;
            padding: 40px;
            text-align: center;
        }
        .card {
            max-width: 650px;
            margin: auto;
            background: #1e293b;
            padding: 35px;
            border-radius: 16px;
            box-shadow: 0 10px 30px rgba(0,0,0,.35);
        }
        h1 { color: #38bdf8; }
        .status {
            display: inline-block;
            background: #22c55e;
            color: #052e16;
            padding: 8px 18px;
            border-radius: 999px;
            font-weight: bold;
        }
        a {
            color: #38bdf8;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="card">
        <h1>FaceDesk Web</h1>
        <p>Face recognition attendance system dashboard</p>
        <span class="status">Online</span>
        <p>
            <a href="/api/health">Check API health</a>
        </p>
    </div>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HOME_PAGE)


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "FaceDesk",
        "message": "FaceDesk web API is running"
    })


@app.route("/api/attendance")
def attendance():
    # Later, connect this to your attendance database or CSV records.
    return jsonify({
        "status": "success",
        "message": "Attendance endpoint is ready",
        "records": []
    })


# This block is only for running locally using: python app.py
if __name__ == "__main__":
    app.run(debug=True)