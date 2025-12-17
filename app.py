import os
import json
import asyncio
import re
import logging
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup
from cachetools import TTLCache, cached

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Gemini API configured successfully")

app = FastAPI(
    title="TripOnBuddy API",
    description="API for fetching travel destination images and generating AI-powered itineraries using Google Gemini",
    version="2.0.0"
)

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


# ===========================================
# Gemini AI Endpoints
# ===========================================

class ItineraryRequest(BaseModel):
    fromLocation: str
    location: str
    startDate: str
    duration: int
    budget: Optional[float] = None
    preferences: Optional[List[str]] = None


class PlaceDetailsRequest(BaseModel):
    placeQuery: str


def generate_itinerary_prompt(from_location: str, location: str, start_date: str, 
                               duration: int, budget: Optional[float], 
                               preferences: Optional[List[str]]) -> str:
    budget_constraint = ""
    if budget:
        budget_constraint = " Carefully calculate and display the total estimated cost by summing all the individual estimated costs listed under each activity and accommodation. Do not invent a new number. The value must be the actual sum of all costs shown. Display this sum as estimatedTotalCost."
    
    preference_instruction = ""
    if preferences and len(preferences) > 0:
        preference_instruction = f"""

IMPORTANT TRAVEL PREFERENCES: The user has specifically selected these travel interests: {', '.join(preferences)}. 

STRONGLY PRIORITIZE and emphasize activities that align with these preferences:
- Adventure: Include hiking, trekking, adventure sports, outdoor activities, nature exploration
- Culture: Focus on museums, historical sites, local cultural experiences, traditional performances, heritage sites
- Relaxation: Emphasize beaches, spas, leisurely activities, scenic viewpoints, peaceful locations
- Classical: Include traditional attractions, heritage sites, classical music venues, architectural marvels
- Shopping: Feature local markets, shopping districts, craft centers, souvenir shops, fashion areas
- Food: Highlight culinary experiences, local restaurants, street food tours, cooking classes, food markets

Make sure at least 70% of the suggested activities directly relate to the user's selected preferences. When describing activities, explicitly mention how they connect to the user's interests."""

    return f"""Create a detailed {duration}-day travel itinerary from {from_location} to {location} starting on {start_date}. {budget_constraint}{preference_instruction}

    IMPORTANT: Do not limit the number of activities for each day. Include all notable activities and attractions available for each day without restricting the itinerary to just 3 places per day.

    Include:

    1. A day-by-day schedule with:
       - Activities and attractions with time slots
       - Location details
       - Estimated costs
       - Booking information when available

    2. Accommodation suggestions with:
       - Hotel/lodging names
       - Price ranges
       - Availability
       - Booking websites links just plain websites links no prameters based on users input keep it simple just example https://booking.com thats it
       - Show as many as possible accomdations

    3. Additional information:
       - Total estimated cost
       - Best time to visit
       - Travel tips
       - Estimated travel time between locations

    4. Just make plan for the destination not for the start location, ignore the start location its just for user nothing to do for us with it, but ensure to calculate the distance between the start and the destination location, and the prices should be in INDIAN Rupees ₹.
    
    5. CRITICAL: For each activity, provide an "imageSearchQuery" field with a VERY SHORT search query (1-2 words maximum) that will return good images. These queries must:
       - Use ONLY the main landmark/place name (remove ALL action words like "Visit", "Take", "Explore", "Tour", "Lunch", "Dinner", "Shopping", etc.)
       - Keep it to 1-2 words maximum (e.g., "TajMahal", "RedFort", "GatewayIndia", "MarineBeach")
       - Remove articles (a, an, the) and prepositions (at, in, on, etc.)
       - Remove time-related words (morning, evening, lunch, dinner, etc.)
       - Examples: "Visit Taj Mahal" → "TajMahal", "Take lunch at restaurant" → "Restaurant", "Explore Red Fort" → "RedFort"
       - If location context is needed, add it without spaces: "TajMahalAgra", "GatewayMumbai"
       estimatedCost": "₹20" — Always keep this as a single, precise value. Avoid using ranges like ₹20–30, as they cause issues during calculations
    
    6. IMPORTANT: Include a "neighboringPlaces" section with 4-6 interesting places near {location} that travelers might also want to visit. These should be within 50-150km of the main destination. For each place include:
       - Name of the place
       - Distance from main destination
       - Brief description (1-2 lines)
       - Estimated time to reach
       - Best known for (key attraction)
       - imageSearchQuery: A short 1-2 word search query for the place (just the place name without any action words)
       
    Format the response as a structured JSON object. Example structure:
    {{
      "dailyPlans": [{{
        "day": 1,
        "activities": [{{
          "time": "9:00 AM",
          "activity": "Visit landmark",
          "location": "Address",
          "estimatedCost": "₹20",
          "imageSearchQuery": "LandmarkName",
          "bookingInfo": {{
            "availability": "Open 9AM-5PM",
            "price": "₹20/person",
            "bookingUrl": "optional-url"
          }}
        }}]
      }}],
      "accommodation": [{{
        "name": "Hotel Name",
        "type": "Hotel/Hostel/etc",
        "priceRange": "₹100-150/night",
        "availability": "Available",
        "rating": "4.5/5",
        "bookingUrl": "url of direct site redirect no parameters"
      }}],
      "estimatedTotalCost": "₹500",
      "bestTimeToVisit": "Spring/Summer",
      "travelTips": ["first tip should be always best time to visit the place like summer winter etc","Tip 1", "Tip 2"],
      "neighboringPlaces": [{{
        "name": "Place Name",
        "distance": "50km",
        "description": "Brief description of the place",
        "timeToReach": "1 hour by car",
        "bestKnownFor": "Key attraction or feature",
        "imageSearchQuery": "PlaceName"
      }}]
    }}"""


@app.post("/api/generate_itinerary")
async def generate_itinerary(request: ItineraryRequest):
    if not GEMINI_API_KEY:
        logger.error("Gemini API key not configured")
        raise HTTPException(
            status_code=503, 
            detail="Gemini API is not configured. Please set GEMINI_API_KEY environment variable."
        )
    
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        
        prompt = generate_itinerary_prompt(
            from_location=request.fromLocation,
            location=request.location,
            start_date=request.startDate,
            duration=request.duration,
            budget=request.budget,
            preferences=request.preferences
        )
        
        logger.info(f"Generating itinerary for {request.location} ({request.duration} days)")
        
        response = model.generate_content(prompt)
        text = response.text
        
        logger.info("🤖 Raw AI Response received")
        
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            logger.error(f"❌ No JSON found in response")
            raise HTTPException(status_code=500, detail="Failed to extract JSON from Gemini response")
        
        try:
            itinerary_json = json.loads(json_match.group())
        except json.JSONDecodeError as parse_error:
            logger.error(f"❌ JSON Parse Error: {parse_error}")
            raise HTTPException(status_code=500, detail=f"Failed to parse JSON from Gemini response: {str(parse_error)}")
        
        logger.info("🎯 AI Response Parsed Successfully")
        logger.info(f"💰 Total Estimated Cost from AI: {itinerary_json.get('estimatedTotalCost', 'N/A')}")
        
        return itinerary_json
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating itinerary: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate itinerary. Please try again later. Error: {str(e)}")


@app.post("/api/generate_place_details")
async def generate_place_details(request: PlaceDetailsRequest):
    if not GEMINI_API_KEY:
        logger.error("Gemini API key not configured")
        raise HTTPException(
            status_code=503, 
            detail="Gemini API is not configured. Please set GEMINI_API_KEY environment variable."
        )
    
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        
        prompt = f'Provide a concise description (around 50-100 words) for the place: "{request.placeQuery}". Focus on key highlights, significance, or what visitors can expect. Respond with only the description text, no extra formatting.'
        
        logger.info(f"Generating place details for: {request.placeQuery}")
        
        response = model.generate_content(prompt)
        description = response.text.strip()
        
        logger.info(f"✅ Successfully generated description for {request.placeQuery}")
        
        return {"description": description}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating place details for '{request.placeQuery}': {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get details for {request.placeQuery}. Error: {str(e)}")


@app.get("/")
async def root():
    return {"status": "ok", "service": "triponbuddy-api"}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "cache_size": len(cache),
        "service": "triponbuddy-api",
        "gemini_configured": bool(GEMINI_API_KEY)
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
