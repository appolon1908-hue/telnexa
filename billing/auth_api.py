import hashlib,secrets
from datetime import datetime,timezone,timedelta
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel,EmailStr,Field
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from .db import session
from .models import AuthToken,LoginAttempt,TeamMember

router=APIRouter(prefix="/api/v1/auth")
def digest(v):return hashlib.sha256(v.encode()).hexdigest()
class Login(BaseModel):tenant_id:str;email:EmailStr;password:str=Field(min_length=8,max_length=200)
class ResetRequest(BaseModel):tenant_id:str;email:EmailStr
class ResetConfirm(BaseModel):token:str;new_password:str=Field(min_length=12,max_length=200)

@router.post("/login")
def login(body:Login,request:Request,response:Response,db:Session=Depends(session)):
    from .app import ph
    now=datetime.now(timezone.utc);ip=digest(request.client.host if request.client else "unknown");identity=digest(f"{body.tenant_id}:{body.email.lower()}")
    failures=db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.identity_hash==identity,LoginAttempt.ip_hash==ip,LoginAttempt.succeeded==False,LoginAttempt.created_at>now-timedelta(minutes=15))) or 0
    if failures>=5:raise HTTPException(429,"login_rate_limited")
    member=db.scalar(select(TeamMember).where(TeamMember.tenant_id==body.tenant_id,TeamMember.email==body.email.lower(),TeamMember.status=="active"));ok=False
    try:
        if member:ph.verify(member.password_hash,body.password);ok=True
    except Exception:pass
    db.add(LoginAttempt(identity_hash=identity,ip_hash=ip,succeeded=ok))
    if not ok:db.commit();raise HTTPException(401,"invalid_credentials")
    if member.mfa_enabled:db.commit();raise HTTPException(428,"mfa_required")
    raw=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24);db.add(AuthToken(tenant_id=body.tenant_id,member_id=member.id,kind="session",token_hash=digest(raw),csrf_hash=digest(csrf),user_agent_hash=digest(request.headers.get("user-agent","")),ip_hash=ip,expires_at=now+timedelta(hours=12)));db.commit();response.set_cookie("telnexa_session",raw,max_age=43200,secure=True,httponly=True,samesite="strict",path="/");return {"authenticated":True,"csrf_token":csrf,"role":member.role}

@router.post("/logout",status_code=204)
def logout(request:Request,response:Response,db:Session=Depends(session)):
    raw=request.cookies.get("telnexa_session");row=db.scalar(select(AuthToken).where(AuthToken.token_hash==digest(raw or ""),AuthToken.revoked_at==None))
    if row:row.revoked_at=datetime.now(timezone.utc);db.commit()
    response.delete_cookie("telnexa_session",secure=True,httponly=True,samesite="strict",path="/")

@router.post("/password/forgot",status_code=202)
def forgot(body:ResetRequest,db:Session=Depends(session)):
    # Deliberately identical response for unknown identities. Delivery is an outbox integration.
    member=db.scalar(select(TeamMember).where(TeamMember.tenant_id==body.tenant_id,TeamMember.email==body.email.lower()))
    if member:
        raw=secrets.token_urlsafe(32);db.add(AuthToken(tenant_id=body.tenant_id,member_id=member.id,kind="password_reset",token_hash=digest(raw),expires_at=datetime.now(timezone.utc)+timedelta(minutes=30)));db.commit()
    return {"accepted":True}

@router.post("/password/reset")
def reset(body:ResetConfirm,db:Session=Depends(session)):
    from .app import ph
    now=datetime.now(timezone.utc);row=db.scalar(select(AuthToken).where(AuthToken.token_hash==digest(body.token),AuthToken.kind=="password_reset",AuthToken.revoked_at==None,AuthToken.expires_at>now))
    if not row:raise HTTPException(400,"invalid_or_expired_token")
    member=db.get(TeamMember,row.member_id);member.password_hash=ph.hash(body.new_password);row.revoked_at=now
    for s in db.scalars(select(AuthToken).where(AuthToken.member_id==member.id,AuthToken.kind=="session",AuthToken.revoked_at==None)).all():s.revoked_at=now
    db.commit();return {"reset":True,"sessions_revoked":True}
