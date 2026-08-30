from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from typing import Optional
import bcrypt
import json
from database import get_db
from dependencies import create_access_token, get_current_user_id

router = APIRouter()

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str
    exam_board: str = None
    year_group: str = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    exam_board: Optional[str] = None
    year_group: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class AddUserSubjectRequest(BaseModel):
    exam_board: str
    level: str
    subject: str

class UpdateUserSubjectRequest(BaseModel):
    exam_board: Optional[str] = None
    level: Optional[str] = None
    subject: Optional[str] = None

@router.post("/register")
async def register(req: RegisterRequest, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    hashed_password = bcrypt.hashpw(req.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    try:
        async with db.acquire() as conn:
            user_id = await conn.fetchval(
                """
                INSERT INTO users (email, password_hash, display_name, exam_board, year_group)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                req.email, hashed_password, req.display_name, req.exam_board, req.year_group
            )
            return {"user_id": str(user_id)}
    except Exception as e:
        if "unique constraint" in str(e).lower():
            raise HTTPException(status_code=400, detail="Email already registered")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/login")
async def login(req: LoginRequest, response: Response, db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    async with db.acquire() as conn:
        user = await conn.fetchrow("SELECT id, password_hash FROM users WHERE email = $1", req.email)
        
    if not user or not user["password_hash"] or not bcrypt.checkpw(req.password.encode('utf-8'), user["password_hash"].encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    async with db.acquire() as conn:
        await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user["id"])
        
    access_token = create_access_token(data={"sub": str(user["id"])})
    
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=60*24*7*60,
        samesite="lax"
    )
    return {"message": "Login successful"}

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="access_token")
    return {"message": "Logout successful"}

@router.get("/google")
async def google_oauth_login():
    # In a real app, this would redirect to Google's OAuth consent screen
    return {"message": "Redirect to Google OAuth (stub)"}

@router.get("/google/callback")
async def google_oauth_callback(code: str, response: Response, db=Depends(get_db)):
    # In a real app, exchange code for tokens, get user info, and log them in
    # For now, this is just a stub
    return {"message": "Google OAuth Callback (stub)"}

@router.get("/me")
async def get_me(user_id: str = Depends(get_current_user_id), db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")
        
    async with db.acquire() as conn:
        user = await conn.fetchrow("SELECT id, email, display_name, exam_board, year_group, is_admin FROM users WHERE id = $1", user_id)
        
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return dict(user)

@router.patch("/me")
async def update_me(req: UpdateProfileRequest, user_id: str = Depends(get_current_user_id), db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields = []
    params = []
    for col, val in updates.items():
        params.append(val)
        fields.append(f"{col} = ${len(params)}")
    params.append(user_id)

    async with db.acquire() as conn:
        await conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ${len(params)}", *params)
        user = await conn.fetchrow(
            "SELECT id, email, display_name, exam_board, year_group, is_admin FROM users WHERE id = $1", user_id
        )

    return dict(user)

@router.post("/me/password")
async def change_password(req: ChangePasswordRequest, user_id: str = Depends(get_current_user_id), db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    async with db.acquire() as conn:
        user = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1", user_id)
        if not user or not user["password_hash"] or not bcrypt.checkpw(
            req.current_password.encode('utf-8'), user["password_hash"].encode('utf-8')
        ):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        new_hash = bcrypt.hashpw(req.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2", new_hash, user_id)

    return {"message": "Password updated"}

@router.get("/me/subjects")
async def list_my_subjects(user_id: str = Depends(get_current_user_id), db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, exam_board, level, subject FROM user_subjects WHERE user_id = $1 ORDER BY created_at ASC",
            user_id
        )
    return [dict(r) for r in rows]

@router.post("/me/subjects")
async def add_my_subject(req: AddUserSubjectRequest, user_id: str = Depends(get_current_user_id), db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_subjects (user_id, exam_board, level, subject)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, exam_board, level, subject) DO UPDATE SET exam_board = EXCLUDED.exam_board
            RETURNING id, exam_board, level, subject
            """,
            user_id, req.exam_board, req.level, req.subject
        )
    return dict(row)

@router.patch("/me/subjects/{subject_id}")
async def update_my_subject(
    subject_id: str,
    req: UpdateUserSubjectRequest,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_db)
):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields = []
    params = []
    for col, val in updates.items():
        params.append(val)
        fields.append(f"{col} = ${len(params)}")
    params.append(subject_id)
    params.append(user_id)

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE user_subjects SET {', '.join(fields)}
            WHERE id = ${len(params) - 1} AND user_id = ${len(params)}
            RETURNING id, exam_board, level, subject
            """,
            *params
        )
    if not row:
        raise HTTPException(status_code=404, detail="Subject not found")
    return dict(row)

@router.delete("/me/subjects/{subject_id}")
async def remove_my_subject(subject_id: str, user_id: str = Depends(get_current_user_id), db=Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_subjects WHERE id = $1 AND user_id = $2", subject_id, user_id
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Subject not found")
    return {"message": "Subject removed"}
