from flask import Flask, request, jsonify
from flask_cors import CORS
from duckduckgo_search import DDGS

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

@app.route('/api/images', methods=['POST'])
def get_images():
    data = request.get_json()
    location = data.get('location', '').strip()
    if not location:
        return jsonify({'error': 'Missing location'}), 400

    try:
        ddgs = DDGS()
        results = ddgs.images(keywords=location, max_results=50)
        
        # Filter out images from watermark-heavy domains
        images = [
            item['image'] for item in results
            if 'image' in item and not is_watermark_source(item['image'])
        ]
        
        return jsonify({'images': images})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
