from flask import Flask, request, jsonify
from flask_cors import CORS
from duckduckgo_search import DDGS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

@app.route('/api/images', methods=['POST'])
def get_images():
    data = request.get_json()
    location = data.get('location', '').strip()
    if not location:
        return jsonify({'error': 'Missing location'}), 400

    try:
        ddgs = DDGS()
        results = ddgs.images(keywords=location, max_results=20)
        images = [item['image'] for item in results]
        return jsonify({'images': images})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
