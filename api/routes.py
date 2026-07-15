from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import List
import math

router = APIRouter()

# Request model for keypoints
class KeypointRequest(BaseModel):
    name: str
    keypoints: List[List[List[List[float]]]]  # frames → people → points → [x, y]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value

    @field_validator("keypoints")
    @classmethod
    def validate_keypoints(cls, frames):
        if not frames:
            raise ValueError("at least one frame is required")
        for frame in frames:
            for person in frame:
                for point in person:
                    if len(point) != 2 or not all(math.isfinite(value) for value in point):
                        raise ValueError("each keypoint must contain two finite coordinates")
        return frames

@router.post("/refine-pose")
async def refine_pose(request: KeypointRequest):
    """Refine 17-point keypoints using diffusion model. For now, receives keypoints from frontend."""
    frame_count = len(request.keypoints)
    
    # Log received keypoints
    people_count = len(request.keypoints[0]) if request.keypoints else 0
    print(f"📥 Received keypoints: {request.name} | {frame_count} frame(s) | {people_count} person(s)")
    
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
    raise HTTPException(status_code=501, detail="Move classification is not implemented yet")

@router.post("/analyze-sequence")
async def analyze_sequence(request: KeypointRequest):
    """Full pipeline: refine pose, classify move, analyze sequence."""
    raise HTTPException(status_code=501, detail="Sequence analysis is not implemented yet")
