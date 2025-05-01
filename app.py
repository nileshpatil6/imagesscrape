from flask import Flask, request, jsonify
from flask_cors import CORS
from duckduckgo_search import DDGS
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# List of known watermark-heavy domains to exclude
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

def is_watermark_source(image_url: str) -> bool:
    """Check if the image URL contains a known watermark-heavy domain."""
    return any(domain in image_url for domain in WATERMARK_DOMAINS)

def fetch_images(query):
    """Fetch images from DuckDuckGo."""
    ddgs = DDGS()
    results = ddgs.images(keywords=query, max_results=20)  # Fetch 20 results
    # Filter out images from watermark-heavy sources
    images = [
        item['image'] for item in results
        if 'image' in item and not is_watermark_source(item['image'])
    ]
    return images

@app.route('/api/images', methods=['POST'])
def get_images():
    data = request.get_json()
    location = data.get('location', '').strip()
    if not location:
        return jsonify({'error': 'Missing location'}), 400

    try:
        # Use ThreadPoolExecutor to parallelize the image fetching process
        with ThreadPoolExecutor() as executor:
            future = executor.submit(fetch_images, location)
            images = future.result()  # Block until the result is ready
        return jsonify({'images': images})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, threaded=True)
