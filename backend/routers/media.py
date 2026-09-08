"""Authenticated proxy in front of the private S3/MinIO media bucket
(storage.py). Question images/PDFs are exam-board copyrighted material, so
nothing here is servable without a valid session - source PDFs (the
original uploaded paper) additionally require admin, since students never
need the raw file, only the extracted questions/images."""
import storage
from botocore.exceptions import ClientError
from dependencies import get_current_admin_id, get_current_user_id
from fastapi import APIRouter, Depends, HTTPException, Response

router = APIRouter()


@router.get("/papers/{filename}")
async def get_paper_pdf(filename: str, admin_id: str = Depends(get_current_admin_id)):
    try:
        data, content_type = await storage.get_bytes(f"papers/{filename}")
    except ClientError:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=data, media_type=content_type)


@router.get("/question-images/{filename}")
async def get_question_image(filename: str, user_id: str = Depends(get_current_user_id)):
    try:
        data, content_type = await storage.get_bytes(f"question-images/{filename}")
    except ClientError:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=data, media_type=content_type)
