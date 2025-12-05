from fastapi import APIRouter, UploadFile, File, HTTPException
from ultralytics import YOLO
from PIL import Image
import io

router = APIRouter()

# Load YOLO model (once at startup)
model = YOLO('yolov8n-pose.pt')

@router.post("/detect-pose")
async def detect_pose(file: UploadFile = File(...)):
    """Detect pose keypoints from uploaded image."""
    
    # Read image
    image_data = await file.read()
    image = Image.open(io.BytesIO(image_data))
    
    # Run detection
    results = model(image)
    
    # Extract keypoints
    keypoints = results[0].keypoints.xy.tolist()[0]  # First person
    
    return {
        "keypoints": keypoints,
        "success": True
    }