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

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# More specific CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.triponbuddy.com", "http://localhost:*"],  # Be more specific in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# In-memory cache & concurrency limiter
cache = TTLCache(maxsize=1000, ttl=3600)
SEM = asyncio.Semaphore(50)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/113.0.0.0 Safari/537.36"
    )
}

WATERMARK_DOMAINS = {
    "shutterstock.com", "alamy.com", "istockphoto.com", "dreamstime.com",
    "gettyimages.com", "123rf.com", "depositphotos.com", "bigstockphoto.com"
}

def is_watermark_source(url: str) -> bool:
    return any(d in url for d in WATERMARK_DOMAINS)

@cached(cache)
async def fetch_images(query: str, max_images: int = 5) -> List[str]:
    """Fetch images from Bing search"""
    async with SEM:
        url = f"https://www.bing.com/images/search?q={query}&count={max_images}"
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error(f"Bing returned status {resp.status} for query: {query}")
                        raise HTTPException(502, f"Bing returned {resp.status}")
                    html = await resp.text()
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching images for query: {query}")
            raise HTTPException(504, "Request timeout")
        except Exception as e:
            logger.error(f"Error fetching images: {str(e)}")
            raise HTTPException(502, "Failed to fetch images")

    soup = BeautifulSoup(html, "html.parser")
    elems = soup.select("a.iusc")
    out = []
    
    for e in elems:
        try:
            data = json.loads(e.get("m", "{}"))
            img = data.get("murl")
            if img and not is_watermark_source(img):
                out.append(img)
        except Exception as e:
            logger.debug(f"Failed to parse image element: {str(e)}")
            pass
        
        if len(out) >= max_images:
            break
    
    return out

class ImageParams(BaseModel):
    aspectRatio: Optional[str] = None
    minWidth: Optional[int] = None
    minHeight: Optional[int] = None
    preferredOrientation: Optional[str] = None
    highQuality: Optional[bool] = None

class LocationRequest(BaseModel):
    location: str
    params: Optional[ImageParams] = None

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "ok", "service": "image-scraper"}

@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "cache_size": len(cache),
        "service": "image-scraper"
    }

@app.post("/api/bulk_images")
async def bulk_images(request: Union[List[str], LocationRequest]):
    """
    Accepts two formats:
    1. JSON array of location strings: ["location1", "location2", ...]
       Returns: { location1: [urls...], location2: [...] }
    
    2. JSON object with location property: { "location": "location1", ... }
       Returns: { "images": [urls...] }
    """
    try:
        # Handle array of strings (original format)
        if isinstance(request, list):
            if not request:
                raise HTTPException(400, "Empty location list provided")
            
            if not all(isinstance(loc, str) for loc in request):
                raise HTTPException(400, "When sending an array, all items must be strings.")
            
            # Limit number of locations to prevent abuse
            if len(request) > 20:
                raise HTTPException(400, "Maximum 20 locations allowed per request")
            
            # Kick off all scrapes in parallel
            tasks = [fetch_images(loc, max_images=5) for loc in request]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            output: Dict[str, List[str]] = {}
            for loc, res in zip(request, results):
                if isinstance(res, Exception):
                    logger.error(f"Error fetching images for {loc}: {str(res)}")
                    output[loc] = []
                else:
                    output[loc] = res

            return output
        
        # Handle object with location property
        elif isinstance(request, LocationRequest) or (isinstance(request, dict) and "location" in request):
            location = request.location if isinstance(request, LocationRequest) else request["location"]
            
            if not location:
                raise HTTPException(400, "Location cannot be empty")
            
            try:
                images = await fetch_images(location, max_images=5)
                return {"images": images}
            except Exception as e:
                logger.error(f"Error fetching images for {location}: {str(e)}")
                return {"images": [], "error": str(e)}
        
        # Invalid request format
        else:
            raise HTTPException(
                status_code=400, 
                detail="Invalid request format. Expected JSON array of strings or object with location property."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in bulk_images: {str(e)}")
        raise HTTPException(500, "Internal server error")

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception handler caught: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
