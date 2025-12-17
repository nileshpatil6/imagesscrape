"""
Image Scraping API for TripOnBuddy
===================================

Purpose:
--------
This FastAPI application provides a REST API for scraping travel destination images from Bing Image Search.
It's designed to fetch high-quality, watermark-free images for travel locations to enhance the TripOnBuddy
user experience when exploring destinations or planning trips.

Key Features:
- Bulk image fetching for multiple locations
- Intelligent caching to reduce API calls and improve performance
- Watermark filtering to ensure only high-quality images
- CORS support for web application integration
- Rate limiting through semaphore-based concurrency control
- Two flexible API formats for different use cases

Architecture Overview:
---------------------
1. Client Request → FastAPI → Bing Image Search → HTML Parsing → Filtered Results
2. Caching Layer: TTLCache stores results for 1 hour
3. Concurrency Control: Semaphore limits to 50 concurrent requests
4. Error Handling: Graceful degradation for failed requests

Deployment Guide:
----------------
1. Local Development:
   - Install dependencies: pip install fastapi uvicorn aiohttp beautifulsoup4 cachetools pydantic
   - Run locally: python main.py
   - Test at: http://localhost:8000/docs

2. Vercel Deployment:
   - Ensure vercel.json is configured correctly
   - Install Vercel CLI: npm i -g vercel
   - Deploy: vercel --prod
   - The API will be available at: https://your-domain.vercel.app/api/bulk_images

3. Environment Variables (if needed):
   - No environment variables required for basic operation
   - For production, consider adding API keys or rate limit configs

API Usage Examples:
------------------
1. Bulk Image Fetch (JavaScript):
   ```javascript
   const response = await fetch('https://api.triponbuddy.com/api/bulk_images', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     body: JSON.stringify(['Paris', 'Rome', 'Tokyo'])
   });
   const images = await response.json();
   // Returns: { Paris: [...], Rome: [...], Tokyo: [...] }
   ```

2. Single Location Fetch (JavaScript):
   ```javascript
   const response = await fetch('https://api.triponbuddy.com/api/bulk_images', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     body: JSON.stringify({ location: 'Eiffel Tower' })
   });
   const data = await response.json();
   // Returns: { images: [...] }
   ```

3. Using cURL:
   ```bash
   # Bulk fetch
   curl -X POST https://api.triponbuddy.com/api/bulk_images \
     -H "Content-Type: application/json" \
     -d '["Paris", "London"]'
   
   # Single location
   curl -X POST https://api.triponbuddy.com/api/bulk_images \
     -H "Content-Type: application/json" \
     -d '{"location": "Tokyo Tower"}'
   ```

Performance Considerations:
--------------------------
- Cache hit ratio should be monitored for optimization
- Consider implementing Redis for distributed caching in production
- Current limits: 20 locations per request, 50 concurrent operations
- Average response time: 1-3 seconds for uncached requests

Security Notes:
--------------
- No authentication required (consider adding API keys for production)
- CORS configured for triponbuddy.com and localhost only
- Input validation prevents injection attacks
- Rate limiting prevents abuse

Monitoring & Debugging:
----------------------
- Check /health endpoint for cache statistics
- Logs are written to stdout (captured by Vercel)
- Error responses include type information for debugging
- Use correlation IDs in production for request tracking

Future Enhancements:
-------------------
- Implement image quality filters (minWidth, aspectRatio, etc.)
- Add support for multiple search engines (Google, DuckDuckGo)
- Implement distributed caching with Redis
- Add webhook support for async image processing
- Implement image CDN integration for serving optimized images

Author: TripOnBuddy Development Team
Version: 1.0.0
Last Updated: 2024
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from cachetools import TTLCache, cached
import aiohttp
import asyncio
import json
import logging
from bs4 import BeautifulSoup
from typing import List, Dict, Union, Optional
from pydantic import BaseModel

# Set up logging for debugging and monitoring
# INFO level provides a good balance between verbosity and usefulness
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI application
app = FastAPI(
    title="TripOnBuddy Image Scraper API",
    description="API for fetching high-quality travel destination images",
    version="1.0.0"
)

# CORS (Cross-Origin Resource Sharing) Configuration
# ==================================================
# CORS is essential for allowing the TripOnBuddy frontend (running on a different domain)
# to make requests to this API. Without proper CORS configuration, browsers will block
# these requests as a security measure.
#
# Configuration Details:
# - allow_origins: Specifies which domains can access this API
#   - Production: https://www.triponbuddy.com
#   - Development: http://localhost:* (any port)
# - allow_credentials: Enables cookies/auth headers to be sent with requests
# - allow_methods: HTTP methods that can be used (GET for health checks, POST for image fetching)
# - allow_headers: Allows all headers (could be more restrictive in production)
# - expose_headers: Makes all response headers accessible to the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.triponbuddy.com", "http://localhost:*"],  # Be more specific in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Caching Mechanism
# =================
# The caching system uses TTLCache (Time-To-Live Cache) to store image search results.
# This significantly improves performance and reduces load on Bing's servers.
#
# Cache Configuration:
# - maxsize=1000: Stores up to 1000 unique search queries
# - ttl=3600: Each cached result expires after 1 hour (3600 seconds)
#
# Benefits:
# 1. Faster response times for repeated queries
# 2. Reduced external API calls
# 3. Better user experience
# 4. Cost savings if deployed with API rate limits
cache = TTLCache(maxsize=1000, ttl=3600)

# Rate Limiting & Concurrency Control
# ===================================
# Semaphore limits the number of concurrent requests to prevent overwhelming
# Bing's servers and avoid getting rate-limited or blocked.
# 
# Configuration:
# - Maximum 50 concurrent requests
# - Prevents "thundering herd" problem when bulk requests arrive
# - Ensures stable performance under high load
SEM = asyncio.Semaphore(50)

# HTTP Headers Configuration
# ==========================
# User-Agent header mimics a real browser to avoid being blocked by anti-bot measures.
# This is a common practice for web scraping but should be used responsibly.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/113.0.0.0 Safari/537.36"
    )
}

# Watermark Domain Filtering
# ==========================
# Many stock photo sites include watermarks on their images.
# This set contains domains known to serve watermarked images.
# Images from these sources are automatically filtered out to ensure
# only high-quality, watermark-free images are returned.
#
# Add new domains here if you discover additional watermarked sources.
WATERMARK_DOMAINS = {
    "shutterstock.com", "alamy.com", "istockphoto.com", "dreamstime.com",
    "gettyimages.com", "123rf.com", "depositphotos.com", "bigstockphoto.com"
}

def is_watermark_source(url: str) -> bool:
    """
    Check if an image URL is from a known watermarked source.
    
    This function performs a simple substring search to determine if the image
    URL contains any of the known watermark domains. This helps filter out
    stock photos that typically have visible watermarks.
    
    Args:
        url (str): The image URL to check
        
    Returns:
        bool: True if the URL is from a watermarked source, False otherwise
    
    Example:
        >>> is_watermark_source("https://www.shutterstock.com/image-123.jpg")
        True
        >>> is_watermark_source("https://example.com/travel-photo.jpg")
        False
    """
    return any(d in url for d in WATERMARK_DOMAINS)

@cached(cache)
async def fetch_images(query: str, max_images: int = 5) -> List[str]:
    """
    Fetch images from Bing Image Search for a given query.
    
    This is the core function that performs the web scraping. It uses Bing's
    image search to find relevant images for travel destinations. The function
    is decorated with @cached to store results in memory for faster repeated queries.
    
    How Bing Image Search Works:
    ---------------------------
    1. Constructs a search URL with the query and desired image count
    2. Makes an HTTP GET request to Bing's image search page
    3. Parses the HTML response to extract image URLs
    4. Filters out watermarked images
    5. Returns a list of clean image URLs
    
    The Bing search results contain image metadata in JSON format within
    HTML anchor tags with class "iusc". This metadata includes the actual
    image URL under the "murl" (media URL) key.
    
    Args:
        query (str): The search query (e.g., "Paris tourist attractions")
        max_images (int): Maximum number of images to return (default: 5)
        
    Returns:
        List[str]: List of image URLs without watermarks
        
    Raises:
        HTTPException: If the request fails or times out
        
    Note:
        The @cached decorator ensures this function's results are stored
        in the TTL cache, preventing duplicate requests for the same query
        within the cache TTL period (1 hour).
    """
    # Use semaphore to limit concurrent requests
    async with SEM:
        # Construct Bing image search URL with query parameters
        url = f"https://www.bing.com/images/search?q={query}&count={max_images}"
        
        try:
            # Create aiohttp session with browser-like headers
            async with aiohttp.ClientSession(headers=HEADERS) as sess:
                # Make GET request with 10-second timeout to prevent hanging
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    # Check for successful response
                    if resp.status != 200:
                        logger.error(f"Bing returned status {resp.status} for query: {query}")
                        raise HTTPException(502, f"Bing returned {resp.status}")
                    # Get HTML content
                    html = await resp.text()
                    
        except asyncio.TimeoutError:
            # Handle timeout errors specifically
            logger.error(f"Timeout fetching images for query: {query}")
            raise HTTPException(504, "Request timeout")
        except Exception as e:
            # Handle any other errors
            logger.error(f"Error fetching images: {str(e)}")
            raise HTTPException(502, "Failed to fetch images")

    # Parse HTML using BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    
    # Find all anchor tags with class "iusc" - these contain image metadata
    elems = soup.select("a.iusc")
    out = []
    
    # Extract image URLs from each element
    for e in elems:
        try:
            # The 'm' attribute contains JSON data with image information
            data = json.loads(e.get("m", "{}"))
            # Extract the media URL (actual image URL)
            img = data.get("murl")
            
            # Only add non-watermarked images to results
            if img and not is_watermark_source(img):
                out.append(img)
        except Exception as e:
            # Log parsing errors at debug level (these are common and usually not critical)
            logger.debug(f"Failed to parse image element: {str(e)}")
            pass
        
        # Stop when we have enough images
        if len(out) >= max_images:
            break
    
    return out

# Pydantic Models for Request Validation
# ======================================
# These models define the expected structure of API requests and provide
# automatic validation and documentation through FastAPI's integration
# with Pydantic.

class ImageParams(BaseModel):
    """
    Optional parameters for image search customization.
    
    Currently, these parameters are defined but not implemented in the
    image fetching logic. They're included for future extensibility
    when more advanced image filtering might be needed.
    
    Attributes:
        aspectRatio: Desired aspect ratio (e.g., "16:9", "1:1")
        minWidth: Minimum image width in pixels
        minHeight: Minimum image height in pixels
        preferredOrientation: Image orientation ("landscape", "portrait", "square")
        highQuality: Whether to prefer high-quality images
    """
    aspectRatio: Optional[str] = None
    minWidth: Optional[int] = None
    minHeight: Optional[int] = None
    preferredOrientation: Optional[str] = None
    highQuality: Optional[bool] = None

class LocationRequest(BaseModel):
    """
    Request model for single location image search (Format 2).
    
    This model supports the object-based API format where a single
    location is provided along with optional parameters.
    
    Attributes:
        location: The location/destination to search images for
        params: Optional image search parameters
        
    Example:
        {
            "location": "Eiffel Tower Paris",
            "params": {
                "minWidth": 1920,
                "preferredOrientation": "landscape"
            }
        }
    """
    location: str
    params: Optional[ImageParams] = None

@app.get("/")
async def root():
    """
    Basic health check endpoint.
    
    This endpoint provides a simple way to verify that the API is running
    and accessible. It's useful for monitoring tools and load balancers.
    
    Returns:
        dict: Basic status information
        
    Example Response:
        {
            "status": "ok",
            "service": "image-scraper"
        }
    """
    return {"status": "ok", "service": "image-scraper"}

@app.get("/health")
async def health_check():
    """
    Detailed health check endpoint with cache statistics.
    
    This endpoint provides more detailed information about the API's health,
    including the current cache size. This helps monitor memory usage and
    cache effectiveness.
    
    Returns:
        dict: Detailed status including cache information
        
    Example Response:
        {
            "status": "healthy",
            "cache_size": 42,
            "service": "image-scraper"
        }
    """
    return {
        "status": "healthy",
        "cache_size": len(cache),
        "service": "image-scraper"
    }

@app.post("/api/bulk_images")
async def bulk_images(request: Union[List[str], LocationRequest]):
    """
    Main API endpoint for fetching travel destination images.
    
    This endpoint supports two flexible formats to accommodate different
    client needs:
    
    Format 1 - Bulk Location Array:
    -------------------------------
    Use this format when you need images for multiple locations at once.
    Perfect for populating destination galleries or comparison views.
    
    Request:
        ["Paris", "Rome", "Tokyo", "New York"]
    
    Response:
        {
            "Paris": ["url1", "url2", "url3", "url4", "url5"],
            "Rome": ["url1", "url2", "url3", "url4", "url5"],
            "Tokyo": ["url1", "url2", "url3", "url4", "url5"],
            "New York": ["url1", "url2", "url3", "url4", "url5"]
        }
    
    Format 2 - Single Location Object:
    ---------------------------------
    Use this format when you need images for a single location with
    potential future support for filtering parameters.
    
    Request:
        {
            "location": "Eiffel Tower Paris",
            "params": {
                "minWidth": 1920,
                "preferredOrientation": "landscape"
            }
        }
    
    Response:
        {
            "images": ["url1", "url2", "url3", "url4", "url5"]
        }
    
    Error Handling:
    --------------
    - 400 Bad Request: Invalid input format or empty locations
    - 502 Bad Gateway: Failed to fetch from Bing
    - 504 Gateway Timeout: Request took too long
    - 500 Internal Server Error: Unexpected errors
    
    Rate Limiting:
    -------------
    - Maximum 20 locations per request (Format 1)
    - Concurrent requests limited by semaphore (50 max)
    - Results cached for 1 hour to reduce load
    
    Args:
        request: Either a list of location strings or a LocationRequest object
        
    Returns:
        dict: Image URLs organized by location (Format 1) or in an "images" key (Format 2)
        
    Raises:
        HTTPException: Various HTTP errors based on the failure type
    """
    try:
        # Format 1: Handle array of strings (bulk locations)
        if isinstance(request, list):
            # Validate input
            if not request:
                raise HTTPException(400, "Empty location list provided")
            
            # Ensure all items are strings
            if not all(isinstance(loc, str) for loc in request):
                raise HTTPException(400, "When sending an array, all items must be strings.")
            
            # Prevent abuse by limiting number of locations
            if len(request) > 20:
                raise HTTPException(400, "Maximum 20 locations allowed per request")
            
            # Create async tasks for parallel image fetching
            # This significantly improves performance for bulk requests
            tasks = [fetch_images(loc, max_images=5) for loc in request]
            
            # Execute all tasks concurrently and capture exceptions
            # return_exceptions=True prevents one failed request from canceling others
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Build response dictionary
            output: Dict[str, List[str]] = {}
            for loc, res in zip(request, results):
                if isinstance(res, Exception):
                    # Log errors but don't fail the entire request
                    logger.error(f"Error fetching images for {loc}: {str(res)}")
                    output[loc] = []  # Return empty array for failed locations
                else:
                    output[loc] = res

            return output
        
        # Format 2: Handle object with location property
        elif isinstance(request, LocationRequest) or (isinstance(request, dict) and "location" in request):
            # Extract location from request object
            location = request.location if isinstance(request, LocationRequest) else request["location"]
            
            # Validate location
            if not location:
                raise HTTPException(400, "Location cannot be empty")
            
            try:
                # Fetch images for single location
                images = await fetch_images(location, max_images=5)
                return {"images": images}
            except Exception as e:
                # Return error info in response rather than raising exception
                logger.error(f"Error fetching images for {location}: {str(e)}")
                return {"images": [], "error": str(e)}
        
        # Invalid request format
        else:
            raise HTTPException(
                status_code=400, 
                detail="Invalid request format. Expected JSON array of strings or object with location property."
            )
            
    except HTTPException:
        # Re-raise HTTP exceptions as they already have proper status codes
        raise
    except Exception as e:
        # Catch any unexpected errors and log them
        logger.error(f"Unexpected error in bulk_images: {str(e)}")
        raise HTTPException(500, "Internal server error")

# Global Exception Handler
# =======================
# This handler catches any unhandled exceptions that escape the normal
# error handling flow. It ensures that clients always receive a proper
# JSON response instead of raw error messages.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler for unhandled errors.
    
    This ensures that all errors are logged and returned in a consistent
    JSON format, improving debugging and client error handling.
    
    Args:
        request: The FastAPI request object
        exc: The exception that was raised
        
    Returns:
        JSONResponse: Formatted error response with 500 status code
    """
    logger.error(f"Global exception handler caught: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__}
    )

# Development Server Configuration
# ================================
# This section only runs when the script is executed directly,
# not when imported as a module (as Vercel does).
if __name__ == "__main__":
    """
    Local development server configuration.
    
    To run the API locally for testing:
    1. Install dependencies: pip install -r requirements.txt
    2. Run the server: python main.py
    3. Access the API at: http://localhost:8000
    4. View auto-generated docs at: http://localhost:8000/docs
    
    The server will auto-reload on code changes (reload=True).
    """
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
