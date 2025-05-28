import os
import json
import asyncio
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup
from cachetools import TTLCache, cached

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # or ["http://localhost:4173"]
    allow_methods=["*"],
    allow_headers=["*"],
)

# cache & concurrency
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
        bing_url = f"https://www.bing.com/images/search?q={query}&count={max_images}"
        async with aiohttp.ClientSession(headers=HEADERS) as sess:
            async with sess.get(bing_url) as resp:
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
        except json.JSONDecodeError:
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

@app.post("/api/bulk_images")
async def bulk_images(req: Request):
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    if isinstance(data, list):
        if not all(isinstance(item, str) for item in data):
            raise HTTPException(400, "List items must be strings")
        tasks = [fetch_images(loc) for loc in data]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {loc: (res if isinstance(res, list) else []) for loc, res in zip(data, results)}

    if isinstance(data, dict) and "location" in data:
        loc = data["location"]
        if not isinstance(loc, str):
            raise HTTPException(400, "`location` must be a string")
        try:
            return {"images": await fetch_images(loc)}
        except:
            return {"images": []}

    raise HTTPException(400, "Expected list or object with 'location'")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
