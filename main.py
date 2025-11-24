from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import math
import os
import shutil
from datetime import datetime
from dotenv import load_dotenv

app = FastAPI() 
load_dotenv()

# --- NEW: Create Evidence Folder ---
os.makedirs("evidence_photos", exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CTA_API_KEY = os.getenv("CTA_API_KEY")
if not CTA_API_KEY:
    raise ValueError("No API Key found! Make sure you created a .env file.")

BASE_URL = "http://lapi.transitchicago.com/api/1.0/ttpositions.aspx"

@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "Error: index.html not found."

# --- NEW: EVIDENCE UPLOAD ENDPOINT ---
@app.post("/submit-report")
async def submit_report(
    file: UploadFile = File(...), 
    run_number: str = Form(...),
    gps: str = Form(...)
):
    # 1. Generate unique filename (Time + Run Number)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"evidence_photos/REPORT_{timestamp}_RUN{run_number}.jpg"
    
    # 2. Save file locally
    with open(filename, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"status": "success", "filename": filename, "message": "Evidence secured at HQ."}

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.get("/find-train/{route}")
def find_user_train(route: str, lat: float, lon: float):
    try:
        response = requests.get(BASE_URL, params={"key": CTA_API_KEY, "rt": route, "outputType": "JSON"})
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

    all_mapped_trains = []

    for t in raw_trains:
        # Check Ghost Flag: '1' = Ghost, '0' = Live
        is_ghost = (t.get('isSch', '0') == '1')
        
        t_lat = float(t['lat'])
        t_lon = float(t['lon'])
        dist_meters = calculate_distance(lat, lon, t_lat, t_lon)
        
        all_mapped_trains.append({
            "run_number": t['rn'],
            "destination": t['destNm'],
            "next_stop": t['nextStaNm'],
            "lat": t_lat,
            "lon": t_lon,
            "distance_meters": round(dist_meters, 1),
            "is_ghost": is_ghost 
        })

    # Filter: Closest match MUST be a Live train
    live_trains_only = [t for t in all_mapped_trains if not t['is_ghost']]
    live_trains_only.sort(key=lambda x: x['distance_meters'])

    closest = None
    if live_trains_only:
        closest = live_trains_only[0]

    return {
        "found": (closest is not None),
        "closest_train": closest,
        "confidence": "High" if (closest and closest['distance_meters'] < 200) else "Low",
        "all_trains": all_mapped_trains 
    }
