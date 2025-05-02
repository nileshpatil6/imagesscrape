from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from duckduckgo_search import DDGS
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# 1) Configure CORS for all /api/* endpoints
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},  # allow any origin; lock this down in production!
    supports_credentials=True,
)

# 2) Always add CORS headers, even on errors
@app.after_request
def add_cors_headers(response):
    response.headers.setdefault("Access-Control-Allow-Origin", "*")
    response.headers.setdefault(
        "Access-Control-Allow-Headers",
        "Content-Type,Authorization"
    )
    response.headers.setdefault(
        "Access-Control-Allow-Methods",
        "GET,POST,OPTIONS"
    )
    return response

# Domains to exclude (watermark-heavy)
WATERMARK_DOMAINS = [
    "shutterstock.com",
    "alamy.com",
    "istockphoto.com",
    "dreamstime.com",
    "gettyimages.com",
    "123rf.com",
    "depositphotos.com",
    "bigstockphoto.com"
]

def is_watermark_source(url: str) -> bool:
    return any(domain in url for domain in WATERMARK_DOMAINS)

def fetch_images(query: str):
    ddgs = DDGS()
    results = ddgs.images(keywords=query, max_results=2)
    return [
        item["image"]
        for item in results
        if "image" in item and not is_watermark_source(item["image"])
    ]

@app.route("/", methods=["GET", "POST"])
def index():
    query = ""
    images = []

    if request.method == "POST":
        query = request.form.get("location", "").strip()
        if query:
            images = fetch_images(query)

    return render_template("index.html", query=query, images=images)

@app.route("/api/images", methods=["POST", "OPTIONS"])
def get_images():
    if request.method == "OPTIONS":
        # Preflight request — return immediately
        return jsonify({}), 200

    data = request.get_json(force=True, silent=True) or {}
    location = data.get("location", "").strip()
    if not location:
        return jsonify({"error": "Missing location"}), 400

    try:
        # parallelize the fetch so Flask stays responsive
        with ThreadPoolExecutor() as executor:
            future = executor.submit(fetch_images, location)
            images = future.result()
        return jsonify({"images": images}), 200

    except Exception as e:
        # even on errors, our after_request will attach CORS headers
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Enable threading so multiple requests are handled concurrently
    app.run(debug=True, threaded=True)
