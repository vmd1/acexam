from fastapi import FastAPI
from contextlib import asynccontextmanager
from database import init_db, close_db
from routers import auth, exams, generate, feedback, analytics, ingestion

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

app = FastAPI(title="Acexam API", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(exams.router, prefix="/api/exams", tags=["exams"])
app.include_router(generate.router, prefix="/api/generate", tags=["generate"])
app.include_router(feedback.router, prefix="/api/feedback", tags=["feedback"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(ingestion.router, prefix="/api/admin/ingestion", tags=["ingestion"])

from fastapi.staticfiles import StaticFiles
import os

MEDIA_DIR = os.path.join(os.path.dirname(__file__), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/api/media", StaticFiles(directory=MEDIA_DIR), name="media")

@app.get("/api/health")
async def health():
    return {"status": "ok"}
