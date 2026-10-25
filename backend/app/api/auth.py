import secrets
import time
import httpx
from datetime import datetime, timedelta
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession
from app.db.session import get_db
from app.models.domain import User, Session
from app.schemas.domain import UserResponse
from app.config import settings

router = APIRouter()

# OAuth `state` values issued by this process -> issue time. Single process only;
# entries expire so abandoned logins do not accumulate.
oauth_states: dict[str, float] = {}
OAUTH_STATE_TTL_SECONDS = 600


def _prune_states(now: float) -> None:
    for key in [k for k, issued in oauth_states.items() if now - issued > OAUTH_STATE_TTL_SECONDS]:
        oauth_states.pop(key, None)

@router.get("/github/login")
async def github_login():
    if not settings.GITHUB_OAUTH_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GitHub OAuth not configured")
        
    now = time.time()
    _prune_states(now)
    state = secrets.token_urlsafe(32)
    oauth_states[state] = now

    query = urlencode({"client_id": settings.GITHUB_OAUTH_CLIENT_ID, "state": state,
                       "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI})
    url = f"https://github.com/login/oauth/authorize?{query}"
    
    return RedirectResponse(url=url, status_code=302)

@router.get("/github/callback")
async def github_callback(code: str, state: str, response: Response, db: DBSession = Depends(get_db)):
    issued = oauth_states.pop(state, None) if state else None  # single use
    if issued is None or time.time() - issued > OAUTH_STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    
    # Exchange code
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI
            }
        )
        token_data = token_resp.json()
        
        if "access_token" not in token_data:
            raise HTTPException(status_code=400, detail="Failed to retrieve access token")
            
        access_token = token_data["access_token"]
        
        # Get user profile
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_resp.json()
        
    github_user_id = str(user_data["id"])
    github_login = user_data["login"]
    github_name = user_data.get("name")
    github_avatar_url = user_data.get("avatar_url")
    
    user = db.query(User).filter(User.github_user_id == github_user_id).first()
    if not user:
        user = User(
            github_user_id=github_user_id,
            github_login=github_login,
            github_name=github_name,
            github_avatar_url=github_avatar_url
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.github_login = github_login
        user.github_name = github_name
        user.github_avatar_url = github_avatar_url
        db.commit()
        
    # Create DB Session
    session_id = secrets.token_urlsafe(64)
    expires_at = datetime.utcnow() + timedelta(hours=settings.SESSION_EXPIRE_HOURS)
    
    db_session = Session(
        session_id=session_id,
        user_id=user.id,
        expires_at=expires_at
    )
    db.add(db_session)
    db.commit()
    
    # We must return a redirect to frontend since this is the callback URL!
    redir = RedirectResponse(url=settings.FRONTEND_URL.rstrip("/") + "/", status_code=302)
    
    # HttpOnly, SameSite=Lax (needed for the OAuth redirect); Secure whenever the
    # API itself is served over HTTPS.
    redir.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=settings.GITHUB_OAUTH_REDIRECT_URI.lower().startswith("https://"),
        max_age=settings.SESSION_EXPIRE_HOURS * 3600
    )
    
    return redir

def get_current_user(request: Request, db: DBSession = Depends(get_db)) -> User:
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    session = db.query(Session).filter(Session.session_id == session_id).first()
    if not session or session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session invalid or expired")
        
    return session.user

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/logout")
def logout(response: Response, request: Request, db: DBSession = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    if session_id:
        session = db.query(Session).filter(Session.session_id == session_id).first()
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie("session_id")
    return {"message": "Logged out successfully"}
