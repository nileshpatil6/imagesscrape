# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from cachetools import TTLCache, cached
import aiohttp, asyncio, json
from bs4 import BeautifulSoup
from typing import List, Dict, Union, Optional
from pydantic import BaseModel

app = FastAPI()

# ——— CORS — allow your frontend domain (or '*' for all)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.triponbuddy.com",],  # ← restrict to your domain in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ——— Cache & concurrency limiter
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
    out: List[str] = []
    for e in soup.select("a.iusc"):
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
    aspectRatio: Optional[str]
    minWidth: Optional[int]
    minHeight: Optional[int]
    preferredOrientation: Optional[str]
    highQuality: Optional[bool]

class LocationRequest(BaseModel):
    location: str
    params: Optional[ImageParams]

@app.post("/api/bulk_images")
async def bulk_images(request: Union[List[str], Dict, LocationRequest]):
    # 1️⃣ List of locations
    if isinstance(request, list):
        if not all(isinstance(loc, str) for loc in request):
            raise HTTPException(400, "All list items must be strings.")
        tasks = [fetch_images(loc) for loc in request]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            loc: res if isinstance(res, list) else []
            for loc, res in zip(request, results)
        }

    # 2️⃣ Dict with `location`
    if isinstance(request, dict) and "location" in request:
        loc = request["location"]
        try:
            imgs = await fetch_images(loc)
        except Exception:
            imgs = []
        return {"images": imgs}

    # 3️⃣ Pydantic model
    if isinstance(request, LocationRequest):
        try:
            imgs = await fetch_images(request.location)
        except Exception:
            imgs = []
        return {"images": imgs}

    # ❌ Otherwise
    raise HTTPException(400, "Invalid request format.")
