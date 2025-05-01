from flask import Flask, request, jsonify
from flask_cors import CORS
from duckduckgo_search import DDGS
import requests
from PIL import Image
import io
import base64

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# List of domains known for watermarking
WATERMARK_DOMAINS = [
    'shutterstock.com',
    'alamy.com',
    'istockphoto.com',
    'dreamstime.com',
    'gettyimages.com',
    '123rf.com',
    'depositphotos.com',
    'bigstockphoto.com'
]

def is_watermark_source(image_url: str) -> bool:
    return any(domain in image_url for domain in WATERMARK_DOMAINS)

def fetch_and_compress(url: str, max_size=(300,300), quality=30) -> str:
    """
    Downloads an image, downscales it (maintaining aspect ratio),
    re-encodes as low-quality JPEG, and returns a base64 data-URI.
    """
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    img = Image.open(io.BytesIO(resp.content)).convert('RGB')
    img.thumbnail(max_size, Image.ANTIALIAS)

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/jpeg;base64,{b64}"

@app.route('/api/images', methods=['POST'])
def get_images():
    data = request.get_json()
    location = data.get('location', '').strip()
    if not location:
        return jsonify({'error': 'Missing location'}), 400

    try:
        ddgs = DDGS()
        results = ddgs.images(keywords=location, max_results=30)
        
        compressed_images = []
        for item in results:
            url = item.get('image')
            if not url or is_watermark_source(url):
                continue
            try:
                data_uri = fetch_and_compress(url)
                compressed_images.append(data_uri)
            except Exception:
                # skip any images that fail to download/process
                continue
        
        return jsonify({'images': compressed_images})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
