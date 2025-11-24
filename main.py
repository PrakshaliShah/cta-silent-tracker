from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import math
import os
from dotenv import load_dotenv

# 1. Initialize the App correctly
app = FastAPI() 

# 2. Load Environment Variables
load_dotenv()

# 3. Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Securely Load API Key
CTA_API_KEY = os.getenv("CTA_API_KEY")

# Safety Check
if not CTA_API_KEY:
    raise ValueError("No API Key found! Make sure you created a .env file with CTA_API_KEY inside.")

BASE_URL = "http://lapi.transitchicago.com/api/1.0/ttpositions.aspx"

# --- Root Endpoint (Serves Frontend) ---
@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "Error: index.html not found. Make sure it is in the same folder!"

# --- Distance Calculator ---
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# --- Train Finder Logic ---
@app.get("/find-train/{route}")
def find_user_train(route: str, lat: float, lon: float):
    try:
        response = requests.get(
            BASE_URL,
            params={"key": CTA_API_KEY, "rt": route, "outputType": "JSON"}
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to CTA: {str(e)}")

    if data.get('ctatt', {}).get('errNm'):
        raise HTTPException(status_code=400, detail=f"CTA API Error: {data['ctatt']['errNm']}")

    try:
        raw_trains = data['ctatt']['route'][0]['train']
    except (KeyError, IndexError):
        return {"found": False, "message": "No trains found on this line right now."}

    live_trains = []
    for t in raw_trains:
        # STRICT FILTER: Only accept trains with isSch not equal to '1'
        if t.get('isSch', '0') != '1':
            t_lat = float(t['lat'])
            t_lon = float(t['lon'])
            dist_meters = calculate_distance(lat, lon, t_lat, t_lon)
            
            live_trains.append({
                "run_number": t['rn'],
                "destination": t['destNm'],
                "next_stop": t['nextStaNm'],
                "lat": t_lat,
                "lon": t_lon,
                "distance_meters": round(dist_meters, 1)
            })

    live_trains.sort(key=lambda x: x['distance_meters'])

    if live_trains:
        closest = live_trains[0]
        return {
            "found": True,
            "closest_train": closest,
            "confidence": "High" if closest['distance_meters'] < 200 else "Low",
            "all_trains": live_trains 
        }
    
    return {"found": False, "message": "No live trains found."}
