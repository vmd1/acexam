from fastapi import FastAPI
from contextlib import asynccontextmanager
from database import init_db, close_db
from routers import auth, exams, generate, feedback, analytics, ingestion, qualifications, internal, media

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
app.include_router(qualifications.router, prefix="/api/admin/qualifications", tags=["qualifications"])
app.include_router(media.router, prefix="/api/media", tags=["media"])
# Not under /api - Traefik's HTTP provider reaches this directly via the
# Docker network (http://backend:8000/internal/traefik-config), not
# through the frontend's /api proxy. Guarded by a shared-secret header
# (routers/internal.py), not user/admin auth.
app.include_router(internal.router, prefix="/internal", tags=["internal"])

@app.get("/api/health")
async def health():
    return {"status": "ok"}
