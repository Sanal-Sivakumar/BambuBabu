"""
BambuBabu — Logs API
Serves log files to the frontend dashboard.
"""
from fastapi import APIRouter
from pydantic import BaseModel
import os
from pathlib import Path
from backend.config import settings

router = APIRouter(prefix="/api/logs", tags=["Logs"])

class LogResponse(BaseModel):
    content: str
    filename: str

@router.get("/all")
def get_logs(limit: int = 80) -> LogResponse:
    """Read the tail of the bambububu log file."""
    log_path = Path("logs/bambububu.log")
    
    if not log_path.exists():
        return LogResponse(content="No log file found yet.", filename="bambububu.log")
        
    try:
        # Just a simple tail approach without external libraries
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Get the last 'limit' lines
        tail_lines = lines[-limit:] if len(lines) > limit else lines
        
        return LogResponse(
            content="".join(tail_lines),
            filename="bambububu.log"
        )
    except Exception as e:
        return LogResponse(
            content=f"Error reading logs: {str(e)}",
            filename="bambububu.log"
        )
