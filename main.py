from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from cachetools import TTLCache, cached
import aiohttp, asyncio, json
from bs4 import BeautifulSoup
from typing import List, Dict, Optional, Union
from pydantic import BaseModel

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Concurrency and cache
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
                    raise HTTPException(502, f"Bing returned status {resp.status}")
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
        except:
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
async def bulk_images(req: Request):
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(400, "Request body must be JSON")

    # Format 1: List of locations
    if isinstance(data, list):
        if not all(isinstance(item, str) for item in data):
            raise HTTPException(400, "All items in the list must be strings.")
        tasks = [fetch_images(loc, max_images=5) for loc in data]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            loc: res if isinstance(res, list) else []
            for loc, res in zip(data, results)
        }

    # Format 2 & 3: Object with location field
    elif isinstance(data, dict) and "location" in data:
        location = data["location"]
        if not isinstance(location, str):
            raise HTTPException(400, "The 'location' field must be a string.")
        try:
            images = await fetch_images(location, max_images=5)
            return {"images": images}
        except:
            return {"images": []}

    raise HTTPException(400, "Invalid request format. Expected list or object with 'location'.")

# To run locally with: `python app.py`
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
