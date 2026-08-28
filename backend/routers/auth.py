from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
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
