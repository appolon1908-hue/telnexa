import secrets, uuid, os
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.responses import HTMLResponse,Response
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from prometheus_client import Counter,generate_latest,CONTENT_TYPE_LATEST
from .db import Base,engine,session
from .models import *
from .engine import send_simulated,credit
from .schemas import SendRequest,CreditRequest
app=FastAPI(title="Telnexa Billing API",version="1.0.0",docs_url="/developer/openapi")
Base.metadata.create_all(engine)
SENDS=Counter("telnexa_billing_sends_total","Billing sends",["status"]); DUPES=Counter("telnexa_billing_idempotent_duplicates_total","Duplicate requests"); ph=PasswordHasher()
@app.middleware("http")
async def security(request,call_next):
    response=await call_next(request);response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"same-origin","Content-Security-Policy":"default-src 'self'; style-src 'self' 'unsafe-inline'","Strict-Transport-Security":"max-age=31536000; includeSubDomains"});return response
def tenant(x_tenant_id:str=Header(...),x_api_key:str=Header(...),db:Session=Depends(session)):
    row=db.scalar(select(ApiKey).where(ApiKey.tenant_id==x_tenant_id,ApiKey.prefix==x_api_key[:12],ApiKey.revoked==False));
    try:
        if not row:raise VerifyMismatchError
        ph.verify(row.secret_hash,x_api_key)
    except VerifyMismatchError:raise HTTPException(401,"invalid_api_key")
    row.last_used_at=datetime.now(timezone.utc);db.commit();return x_tenant_id
@app.get("/health")
def health(db:Session=Depends(session)):db.execute(select(1));return {"status":"ok","simulator":True}
@app.get("/ready")
def ready(db:Session=Depends(session)):db.execute(select(1));return {"status":"ready"}
@app.get("/metrics")
def metrics():return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)
@app.post("/api/v1/messages",status_code=202)
def send(body:SendRequest,idempotency_key:str=Header(...),x_correlation_id:str|None=Header(None),tenant_id:str=Depends(tenant),db:Session=Depends(session)):
    account=db.get(BillingAccount,body.billing_account_id)
    if not account or account.tenant_id!=tenant_id:raise HTTPException(404,"billing_account_not_found")
    prior=db.scalar(select(Message).where(Message.tenant_id==tenant_id,Message.idempotency_key==idempotency_key));
    if prior:DUPES.inc();return message_json(prior)
    try:msg=send_simulated(db,account.id,body.destination,body.sender,body.content,idempotency_key,x_correlation_id or str(uuid.uuid4()),body.simulator_outcome);db.commit();SENDS.labels(msg.status).inc();return message_json(msg)
    except ValueError as e:db.rollback();SENDS.labels("rejected").inc();raise HTTPException(402,str(e))
def message_json(m):return {"message_id":m.id,"status":m.status,"encoding":m.encoding,"characters":m.character_count,"segments":m.segments,"estimated_charge":str(m.estimated_sell_amount),"provider_message_id":m.provider_message_id,"simulated":m.provider=="simulator"}
@app.get("/api/v1/messages")
def messages(tenant_id:str=Depends(tenant),db:Session=Depends(session)):return {"items":[message_json(x) for x in db.scalars(select(Message).where(Message.tenant_id==tenant_id).order_by(Message.created_at.desc())).all()]}
@app.get("/api/v1/billing/wallet")
def wallet(tenant_id:str=Depends(tenant),db:Session=Depends(session)):
    w=db.scalar(select(Wallet).where(Wallet.tenant_id==tenant_id));return {"available":str(w.available),"reserved":str(w.reserved),"pending":str(w.pending),"currency":w.currency}
@app.get("/api/v1/billing/ledger")
def ledger(tenant_id:str=Depends(tenant),db:Session=Depends(session)):return {"items":[{"id":x.id,"amount":str(x.amount),"direction":x.direction,"type":x.type,"reference_id":x.reference_id,"timestamp":x.created_at} for x in db.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id==tenant_id).order_by(LedgerEntry.created_at.desc())).all()]}
@app.get("/api/v1/billing/invoices")
def invoices(tenant_id:str=Depends(tenant),db:Session=Depends(session)):return {"items":[{"id":x.id,"number":x.number,"total":str(x.total),"status":x.status} for x in db.scalars(select(Invoice).where(Invoice.tenant_id==tenant_id)).all()]}
@app.get("/api/v1/billing/usage")
def usage(tenant_id:str=Depends(tenant),db:Session=Depends(session)):
    r=db.execute(select(func.count(Usage.id),func.coalesce(func.sum(Usage.segments),0),func.coalesce(func.sum(Usage.revenue),0),func.coalesce(func.sum(Usage.cost),0)).where(Usage.tenant_id==tenant_id)).one();return {"messages":r[0],"segments":r[1],"revenue":str(r[2]),"provider_cost":str(r[3]),"gross_profit":str(r[2]-r[3])}
@app.post("/api/v1/admin/credits")
def admin_credit(body:CreditRequest,x_admin_token:str=Header(...),idempotency_key:str=Header(...),db:Session=Depends(session)):
    if not secrets.compare_digest(x_admin_token,os.environ.get('BILLING_ADMIN_TOKEN','disabled')):raise HTTPException(403,"forbidden")
    entry=credit(db,body.billing_account_id,body.amount,idempotency_key,"admin",body.reason);db.add(Audit(tenant_id=entry.tenant_id,actor="admin",action="wallet.credit",target=entry.id,correlation_id=str(uuid.uuid4()),after={"amount":str(body.amount),"reason":body.reason}));db.commit();return {"ledger_entry_id":entry.id}
@app.post("/api/v1/admin/tenants/{tenant_id}/api-keys",status_code=201)
def create_key(tenant_id:str,x_admin_token:str=Header(...),db:Session=Depends(session)):
    if not secrets.compare_digest(x_admin_token,os.environ.get('BILLING_ADMIN_TOKEN','disabled')):raise HTTPException(403,"forbidden")
    raw="tnx_"+secrets.token_urlsafe(32);row=ApiKey(tenant_id=tenant_id,prefix=raw[:12],secret_hash=ph.hash(raw));db.add(row);db.flush();db.add(Audit(tenant_id=tenant_id,actor="admin",action="api_key.created",target=row.id,correlation_id=str(uuid.uuid4()),after={"prefix":row.prefix}));db.commit();return {"id":row.id,"secret":raw,"display_once":True}
@app.delete("/api/v1/api-keys/{key_id}")
def revoke_key(key_id:str,tenant_id:str=Depends(tenant),db:Session=Depends(session)):
    row=db.scalar(select(ApiKey).where(ApiKey.id==key_id,ApiKey.tenant_id==tenant_id))
    if not row:raise HTTPException(404,"api_key_not_found")
    row.revoked=True;db.add(Audit(tenant_id=tenant_id,actor="tenant",action="api_key.revoked",target=row.id,correlation_id=str(uuid.uuid4())));db.commit();return {"revoked":True}
@app.get("/portal",response_class=HTMLResponse)
def portal():return """<!doctype html><meta name=viewport content='width=device-width'><title>Telnexa Portal</title><style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:2rem;background:#08101f;color:#edf3ff}nav{display:flex;gap:1rem;flex-wrap:wrap}.card{background:#131f35;padding:1.2rem;border-radius:14px;margin-top:1rem}</style><h1>Telnexa</h1><nav>Dashboard · Messages · API keys · Developer · SMPP · Sender IDs · Webhooks · Billing · Team</nav><div class=card><h2>Customer portal</h2><p>Use tenant-authenticated APIs for live wallet, message, invoice, statement, rate and usage data. Simulator mode is clearly labelled.</p></div>"""
@app.get("/admin",response_class=HTMLResponse)
def admin():return """<!doctype html><meta name=viewport content='width=device-width'><title>Telnexa Admin</title><style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:2rem;background:#160d20;color:#fff}nav{display:flex;gap:1rem;flex-wrap:wrap}.card{background:#291936;padding:1.2rem;border-radius:14px;margin-top:1rem}</style><h1>Telnexa Admin</h1><nav>Customers · Billing · Pricing · Providers · Margins · Messaging · Security · Audit</nav><div class=card><h2>Billing operations</h2><p>Privileged mutations require admin authentication, reason, idempotency key, and immutable audit evidence.</p></div>"""
