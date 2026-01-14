from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from typing import List

router = APIRouter()

# Request model for keypoints
class KeypointRequest(BaseModel):
    name: str
    keypoints: List[List[List[List[float]]]]  # frames → people → points → [x, y]

@router.post("/refine-pose")
async def refine_pose(request: KeypointRequest):
    """Refine 17-point keypoints using diffusion model. For now, receives keypoints from frontend."""
    frame_count = len(request.keypoints)
    
    if frame_count == 0:
        raise HTTPException(status_code=400, detail="No frames provided")
    
    # TODO: Add diffusion model refinement here
    
    return {
        "success": True,
        "name": request.name,
        "frame_count": frame_count,
        "message": f"Received {frame_count} frames with keypoints"
    }

@router.post("/classify-move")
async def classify_move(request: KeypointRequest):
    """Classify dance move from keypoints sequence."""
    # TODO: Add classifier model here
    return {
        "success": True,
        "message": "hello world"
    }

@router.post("/analyze-sequence")
async def analyze_sequence(request: KeypointRequest):
    """Full pipeline: refine pose, classify move, analyze sequence."""
    # TODO: Add full pipeline here
    return {
        "success": True,
        "message": "hello world"
    }