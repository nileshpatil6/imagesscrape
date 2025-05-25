from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from cachetools import TTLCache, cached
import aiohttp, asyncio, json
from bs4 import BeautifulSoup
from typing import List, Dict, Union, Optional
from pydantic import BaseModel

app = FastAPI()

# Allow CORS from anywhere
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    async with SEM:
        url = f"https://www.bing.com/images/search?q={query}&count={max_images}"
        async with aiohttp.ClientSession(headers=HEADERS) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    raise HTTPException(502, f"Bing returned {resp.status}")
                html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    elems = soup.select("a.iusc")
    out = []

    for e in elems:
        try:
            data = json.loads(e.get("m", "{}"))
            img = data.get("murl")
            if img and not is_watermark_source(img):
                out.append(img)
        except Exception:
            continue
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

@app.post("/api/bulk_images")
async def bulk_images(request: Union[List[str], Dict, LocationRequest]):
    """
    Accepts:
    1. JSON array of location strings: ["location1", "location2", ...]
       Returns: { location1: [urls...], location2: [...] }

    2. JSON object with location: { "location": "some_place", ... }
       Returns: { "images": [urls...] }
    """
    # Case 1: List of strings
    if isinstance(request, list):
        if not all(isinstance(loc, str) for loc in request):
            raise HTTPException(400, "All list items must be strings.")
        
        tasks = [fetch_images(loc, max_images=5) for loc in request]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: Dict[str, List[str]] = {}
        for loc, res in zip(request, results):
            output[loc] = res if isinstance(res, list) else []

        return output

    # Case 2: Dict with 'location'
    elif isinstance(request, dict) and "location" in request:
        location = request.get("location")
        try:
            images = await fetch_images(location, max_images=5)
            return {"images": images}
        except Exception:
            return {"images": []}

    # Case 3: Proper Pydantic model
    elif isinstance(request, LocationRequest):
        try:
            images = await fetch_images(request.location, max_images=5)
            return {"images": images}
        except Exception:
            return {"images": []}

    # Invalid request
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid request format. Expected JSON array or object with 'location'."
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app_updated:app", host="0.0.0.0", port=8000, reload=True, workers=4)
