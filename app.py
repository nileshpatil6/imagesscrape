from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
import requests
import json
from bs4 import BeautifulSoup

app = Flask(__name__)

# Enable CORS for all /api/* endpoints
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    supports_credentials=True,
)

# Always add CORS headers to every response
@app.after_request
def add_cors_headers(response):
    response.headers.setdefault("Access-Control-Allow-Origin", "*")
    response.headers.setdefault(
        "Access-Control-Allow-Headers", "Content-Type,Authorization"
    )
    response.headers.setdefault(
        "Access-Control-Allow-Methods", "GET,POST,OPTIONS"
    )
    return response

# Fake browser headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/113.0.0.0 Safari/537.36"
    )
}

# Watermark-heavy domains
WATERMARK_DOMAINS = [
    "shutterstock.com",
    "alamy.com",
    "istockphoto.com",
    "dreamstime.com",
    "gettyimages.com",
    "123rf.com",
    "depositphotos.com",
    "bigstockphoto.com",
]

def is_watermark_source(url: str) -> bool:
    return any(domain in url for domain in WATERMARK_DOMAINS)

def fetch_images(query: str, max_images: int = 20):
    """Scrape Bing Images and return valid URLs"""
    params = {"q": query, "first": "0", "count": str(max_images)}
    try:
        resp = requests.get("https://www.bing.com/images/search", params=params, headers=HEADERS)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch from Bing: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")
    image_elements = soup.find_all("a", class_="iusc")

    urls = []
    for elem in image_elements:
        try:
            m_json = json.loads(elem.get("m", "{}"))
            url = m_json.get("murl")
            if url and not is_watermark_source(url):
                urls.append(url)
        except Exception:
            continue

        if len(urls) >= max_images:
            break
    return urls

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
        return jsonify({}), 200

    data = request.get_json(force=True, silent=True) or {}
    location = data.get("location", "").strip()
    if not location:
        return jsonify({"error": "Missing location"}), 400

    try:
        with ThreadPoolExecutor() as executor:
            future = executor.submit(fetch_images, location)
            images = future.result()
        return jsonify({"images": images}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
