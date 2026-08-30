from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from dependencies import rate_limit, get_current_admin_id
from database import get_db

router = APIRouter(dependencies=[Depends(rate_limit), Depends(get_current_admin_id)])

class UpdateQualificationRequest(BaseModel):
    custom_paper_target_marks: Optional[int] = None
    custom_paper_time_limit_minutes: Optional[int] = None

@router.get("")
async def list_qualifications(db=Depends(get_db)):
    """
    Backs the admin "Manage Subjects" page - one row per (exam_board,
    level, subject) qualification, with its custom-paper target marks/time
    limit (NULL if not yet configured, in which case /generate/custom-paper
    falls back to its own heuristic).
    """
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    async with db.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, exam_board, level, subject, tiers,
                   custom_paper_target_marks, custom_paper_time_limit_minutes
            FROM qualifications
            ORDER BY exam_board, level, subject
        ''')
    return [dict(r) for r in rows]

@router.put("/{qualification_id}")
async def update_qualification(qualification_id: str, req: UpdateQualificationRequest, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
    if req.custom_paper_target_marks is not None and req.custom_paper_target_marks <= 0:
        raise HTTPException(status_code=400, detail="Target marks must be a positive number")
    if req.custom_paper_time_limit_minutes is not None and req.custom_paper_time_limit_minutes <= 0:
        raise HTTPException(status_code=400, detail="Time limit must be a positive number of minutes")

    async with db.acquire() as conn:
        row = await conn.fetchrow('''
            UPDATE qualifications
            SET custom_paper_target_marks = $1, custom_paper_time_limit_minutes = $2
            WHERE id = $3
            RETURNING id, exam_board, level, subject, tiers,
                      custom_paper_target_marks, custom_paper_time_limit_minutes
        ''', req.custom_paper_target_marks, req.custom_paper_time_limit_minutes, qualification_id)

    if not row:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return dict(row)
