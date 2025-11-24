from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import math
import os
import io # NEW: Needed to handle the file stream
from datetime import datetime
from dotenv import load_dotenv
import boto3 # NEW: AWS SDK for S3

# --- APP SETUP ---
app = FastAPI() 
load_dotenv()

# --- S3 CONFIGURATION (READ FROM .env) ---
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_BUCKET_NAME = os.getenv("AWS_BUCKET_NAME")

# --- CTA API KEY ---
# IMPORTANT: Swapping to os.getenv for deployment consistency
CTA_API_KEY = os.getenv("CTA_API_KEY") # This line now reads from .env / Render

# Check all critical keys
if not (CTA_API_KEY and AWS_BUCKET_NAME and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY):
    raise ValueError("Missing critical environment variables (CTA_API_KEY, AWS_BUCKET_NAME, or S3 credentials).")

# Initialize S3 Client (Requires the environment variables to be set)
try:
    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )
except Exception as e:
    print(f"Error initializing S3 client: {e}")
    # We still raise an error if the credentials aren't found (above), but log if client fails.
    
# --- CORS & BASE URL ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "http://lapi.transitchicago.com/api/1.0/ttpositions.aspx"

# --- NEW: EVIDENCE UPLOAD ENDPOINT (Cloud Save) ---
@app.post("/submit-report")
async def submit_report(
    file: UploadFile = File(...), 
    run_number: str = Form(...),
    gps: str = Form(...)
):
    # 1. Prepare file and generate Cloud filename (S3 Key)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    s3_key = f"reports/{timestamp}_RUN{run_number}.jpg"
    
    # 2. Upload file directly to S3 from the in-memory stream
    try:
        # Read the file content into memory
        file_content = await file.read()
        
        # Use upload_fileobj, reading from the content we just loaded
        s3_client.upload_fileobj(
            Fileobj=io.BytesIO(file_content), # Read from memory buffer
            Bucket=AWS_BUCKET_NAME,
            Key=s3_key,
            ExtraArgs={'ContentType': file.content_type} # Ensure image type is set
        )
        
        file_url = f"https://{AWS_BUCKET_NAME}.s3.amazonaws.com/{s3_key}"
        
        return {"status": "success", "file_url": file_url, "message": "Evidence secured in AWS S3."}
    
    except Exception as e:
        # If any part of the AWS process fails (e.g., wrong bucket name, invalid keys)
        print(f"AWS Upload Failed: {e}")
        raise HTTPException(status_code=500, detail=f"AWS Upload Failed: {e}")

# --- EXISTING CODE REMAINS UNCHANGED BELOW THIS POINT ---

@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "Error: index.html not found."

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

    live_trains = []
    
    for t in raw_trains:
        # Check Ghost Flag: '1' = Ghost, '0' = Live
        if t.get('isSch', '0') == '0':
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
