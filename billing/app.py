import secrets, uuid, os, io, csv, hashlib, json, hmac
from pathlib import Path
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from .db import Base, engine, session
from .models import *
from .engine import send_simulated, credit
from .schemas import SendRequest, CreditRequest
from .oidc import validate_bearer

app = FastAPI(
    title="Telnexa Commercial API",
    version="1.0.0",
    docs_url="/developer/openapi",
    openapi_url="/api/v1/openapi.json",
)
Base.metadata.create_all(engine)
SENDS = Counter("telnexa_billing_sends_total", "Billing sends", ["status"])
DUPES = Counter("telnexa_billing_idempotent_duplicates_total", "Duplicate requests")
ph = PasswordHasher()


@app.middleware("http")
async def security(request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers.update(
        {
            "X-Request-ID": request_id,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; connect-src 'self' https://auth.codestra.co; style-src 'self' 'unsafe-inline'",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        }
    )
    return response


def authn(required="read"):
    def dependency(
        x_tenant_id: str = Header(...),
        x_api_key: str | None = Header(None),
        authorization: str | None = Header(None),
        db: Session = Depends(session),
    ):
        principal = validate_bearer(authorization, x_tenant_id, required)
        if principal:
            return x_tenant_id
        if not x_api_key:
            raise HTTPException(401, "authentication_required")
        row = db.scalar(
            select(ApiKey).where(
                ApiKey.tenant_id == x_tenant_id,
                ApiKey.prefix == x_api_key[:12],
                ApiKey.revoked == False,
            )
        )
        try:
            if not row:
                raise VerifyMismatchError
            ph.verify(row.secret_hash, x_api_key)
        except VerifyMismatchError:
            raise HTTPException(401, "invalid_api_key")
        scopes = set(row.scopes.split())
        aliases = {
            "read": {"read", "sms.read", "billing.read"},
            "messages:write": {"messages:write", "sms.send"},
            "bulk:write": {"bulk:write", "sms.bulk"},
        }
        if "admin" not in scopes and not scopes.intersection(
            aliases.get(required, {required})
        ):
            raise HTTPException(403, "insufficient_scope")
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return x_tenant_id

    return dependency


@app.get("/health")
def health(db: Session = Depends(session)):
    db.execute(select(1))
    return {"status": "ok", "simulator": True}


@app.get("/healthz")
def healthz(db: Session = Depends(session)):
    db.execute(select(1))
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Session = Depends(session)):
    db.execute(select(1))
    return {"status": "ready"}


@app.get("/readyz")
def readyz(db: Session = Depends(session)):
    db.execute(select(1))
    return {"status": "ready"}


@app.get("/version")
def version():
    return {
        "name": "telnexa",
        "version": app.version,
        "source_sha": os.environ.get("SOURCE_SHA", "development"),
        "simulator": os.environ.get("BILLING_SIMULATOR_ENABLED", "true").lower()
        == "true",
    }


@app.get("/metrics")
def metrics(authorization: str = Header(default="")):
    try:
        expected = Path(os.environ["TELNEXA_METRICS_TOKEN_FILE"]).read_text().strip()
    except (KeyError, OSError):
        raise HTTPException(404, "not_found")
    supplied = (
        authorization.removeprefix("Bearer ")
        if authorization.startswith("Bearer ")
        else ""
    )
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(404, "not_found")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/messages", status_code=202)
def send(
    body: SendRequest,
    idempotency_key: str = Header(...),
    x_correlation_id: str | None = Header(None),
    tenant_id: str = Depends(authn("messages:write")),
    db: Session = Depends(session),
):
    account = db.get(BillingAccount, body.billing_account_id)
    if not account or account.tenant_id != tenant_id:
        raise HTTPException(404, "billing_account_not_found")
    request_hash = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    prior = db.scalar(
        select(Message).where(
            Message.tenant_id == tenant_id, Message.idempotency_key == idempotency_key
        )
    )
    if prior:
        legacy_hash = (
            "legacy:"
            + hashlib.md5(
                (
                    body.destination
                    + "\n"
                    + body.sender
                    + "\n"
                    + hashlib.sha256(body.content.encode()).hexdigest()
                ).encode(),
                usedforsecurity=False,
            ).hexdigest()
        )
        if prior.request_hash not in (request_hash, legacy_hash):
            raise HTTPException(409, "idempotency_key_payload_mismatch")
        DUPES.inc()
        return message_json(prior)
    contact = db.scalar(
        select(Contact).where(
            Contact.tenant_id == tenant_id, Contact.phone == body.destination
        )
    )
    if body.category == "marketing" and (
        not contact or contact.consent_status != "opted_in" or contact.opted_out_at
    ):
        raise HTTPException(409, "marketing_consent_required_or_suppressed")
    sender = db.scalar(
        select(Sender).where(
            Sender.tenant_id == tenant_id, Sender.sender == body.sender
        )
    )
    if not sender or sender.status != "approved":
        raise HTTPException(409, "sender_not_approved")
    try:
        msg = send_simulated(
            db,
            account.id,
            body.destination,
            body.sender,
            body.content,
            idempotency_key,
            x_correlation_id or str(uuid.uuid4()),
            body.simulator_outcome,
            request_hash=request_hash,
        )
        db.commit()
        SENDS.labels(msg.status).inc()
        return message_json(msg)
    except ValueError as e:
        db.rollback()
        SENDS.labels("rejected").inc()
        raise HTTPException(402, str(e))


def message_json(m):
    return {
        "message_id": m.id,
        "status": m.status,
        "encoding": m.encoding,
        "characters": m.character_count,
        "segments": m.segments,
        "estimated_charge": str(m.estimated_sell_amount),
        "provider_message_id": m.provider_message_id,
        "simulated": m.provider == "simulator",
    }


@app.get("/api/v1/messages")
def messages(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    return {
        "items": [
            message_json(x)
            for x in db.scalars(
                select(Message)
                .where(Message.tenant_id == tenant_id)
                .order_by(Message.created_at.desc())
            ).all()
        ]
    }


@app.get("/api/v1/billing/wallet")
def wallet(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    w = db.scalar(select(Wallet).where(Wallet.tenant_id == tenant_id))
    return {
        "available": str(w.available),
        "reserved": str(w.reserved),
        "pending": str(w.pending),
        "currency": w.currency,
    }


@app.get("/api/v1/balance")
def balance(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    return wallet(tenant_id, db)


@app.get("/api/v1/billing/ledger")
def ledger(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    return {
        "items": [
            {
                "id": x.id,
                "amount": str(x.amount),
                "direction": x.direction,
                "type": x.type,
                "reference_id": x.reference_id,
                "timestamp": x.created_at,
            }
            for x in db.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.tenant_id == tenant_id)
                .order_by(LedgerEntry.created_at.desc())
            ).all()
        ]
    }


@app.get("/api/v1/transactions")
def transactions(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    return ledger(tenant_id, db)


@app.get("/api/v1/billing/invoices")
def invoices(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    return {
        "items": [
            {"id": x.id, "number": x.number, "total": str(x.total), "status": x.status}
            for x in db.scalars(
                select(Invoice).where(Invoice.tenant_id == tenant_id)
            ).all()
        ]
    }


@app.get("/api/v1/billing/invoices/{invoice_id}.pdf")
def invoice_pdf(
    invoice_id: str, tenant_id: str = Depends(authn()), db: Session = Depends(session)
):
    from reportlab.pdfgen import canvas

    inv = db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
    )
    if not inv:
        raise HTTPException(404, "invoice_not_found")
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.setTitle(inv.number)
    pdf.drawString(72, 780, "TELNEXA INVOICE")
    pdf.drawString(72, 750, f"Invoice: {inv.number}")
    pdf.drawString(
        72, 730, f"Period: {inv.period_start.date()} — {inv.period_end.date()}"
    )
    pdf.drawString(72, 710, f"Total: {inv.total} {inv.currency}")
    pdf.drawString(72, 690, f"Status: {inv.status}")
    pdf.showPage()
    pdf.save()
    return Response(
        output.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{inv.number}.pdf"'},
    )


@app.get("/api/v1/billing/usage.csv")
def usage_csv(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "message_id",
            "timestamp",
            "country",
            "provider",
            "sender",
            "segments",
            "status",
            "revenue",
            "provider_cost",
        ]
    )
    for x in db.scalars(
        select(Usage).where(Usage.tenant_id == tenant_id).order_by(Usage.created_at)
    ).all():
        writer.writerow(
            [
                x.message_id,
                x.created_at.isoformat(),
                x.country,
                x.provider,
                x.sender,
                x.segments,
                x.status,
                str(x.revenue),
                str(x.cost),
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="telnexa-usage.csv"'},
    )


@app.get("/api/v1/billing/usage")
def usage(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    r = db.execute(
        select(
            func.count(Usage.id),
            func.coalesce(func.sum(Usage.segments), 0),
            func.coalesce(func.sum(Usage.revenue), 0),
            func.coalesce(func.sum(Usage.cost), 0),
        ).where(Usage.tenant_id == tenant_id)
    ).one()
    return {
        "messages": r[0],
        "segments": r[1],
        "revenue": str(r[2]),
        "provider_cost": str(r[3]),
        "gross_profit": str(r[2] - r[3]),
    }


@app.get("/api/v1/usage")
def usage_alias(tenant_id: str = Depends(authn()), db: Session = Depends(session)):
    return usage(tenant_id, db)


@app.post("/api/v1/messages/bulk", status_code=202)
def bulk_send(
    items: list[SendRequest],
    idempotency_key: str = Header(...),
    x_correlation_id: str | None = Header(None),
    tenant_id: str = Depends(authn("bulk:write")),
    db: Session = Depends(session),
):
    if not items or len(items) > 1000:
        raise HTTPException(422, "bulk_size_must_be_1_to_1000")
    results = []
    for index, body in enumerate(items):
        results.append(
            send(
                body,
                f"{idempotency_key}:{index}",
                x_correlation_id or str(uuid.uuid4()),
                tenant_id,
                db,
            )
        )
    return {"accepted": len(results), "items": results}


@app.post("/api/v1/admin/credits")
def admin_credit(
    body: CreditRequest,
    x_admin_token: str = Header(...),
    idempotency_key: str = Header(...),
    db: Session = Depends(session),
):
    if not secrets.compare_digest(
        x_admin_token, os.environ.get("BILLING_ADMIN_TOKEN", "disabled")
    ):
        raise HTTPException(403, "forbidden")
    entry = credit(
        db, body.billing_account_id, body.amount, idempotency_key, "admin", body.reason
    )
    db.add(
        Audit(
            tenant_id=entry.tenant_id,
            actor="admin",
            action="wallet.credit",
            target=entry.id,
            correlation_id=str(uuid.uuid4()),
            after={"amount": str(body.amount), "reason": body.reason},
        )
    )
    db.commit()
    return {"ledger_entry_id": entry.id}


@app.post("/api/v1/admin/tenants/{tenant_id}/api-keys", status_code=201)
def create_key(
    tenant_id: str, x_admin_token: str = Header(...), db: Session = Depends(session)
):
    if not secrets.compare_digest(
        x_admin_token, os.environ.get("BILLING_ADMIN_TOKEN", "disabled")
    ):
        raise HTTPException(403, "forbidden")
    raw = "tnx_" + secrets.token_urlsafe(32)
    account = db.scalar(
        select(BillingAccount).where(BillingAccount.tenant_id == tenant_id)
    )
    row = ApiKey(
        tenant_id=tenant_id,
        account_id=account.id if account else None,
        prefix=raw[:12],
        secret_hash=ph.hash(raw),
    )
    db.add(row)
    db.flush()
    db.add(
        Audit(
            tenant_id=tenant_id,
            actor="admin",
            action="api_key.created",
            target=row.id,
            correlation_id=str(uuid.uuid4()),
            after={"prefix": row.prefix, "account_id": row.account_id},
        )
    )
    db.commit()
    return {"id": row.id, "secret": raw, "display_once": True}


@app.delete("/api/v1/api-keys/{key_id}")
def revoke_key(
    key_id: str, tenant_id: str = Depends(authn()), db: Session = Depends(session)
):
    row = db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
    )
    if not row:
        raise HTTPException(404, "api_key_not_found")
    row.revoked = True
    db.add(
        Audit(
            tenant_id=tenant_id,
            actor="tenant",
            action="api_key.revoked",
            target=row.id,
            correlation_id=str(uuid.uuid4()),
        )
    )
    db.commit()
    return {"revoked": True}


PORTAL_CSS = """body{margin:0;font:15px system-ui;background:#08101f;color:#edf3ff}header{display:flex;justify-content:space-between;align-items:center;padding:1rem 1.5rem;border-bottom:1px solid #26344d}button,a{color:#9fc0ff}button{background:#3159c5;color:white;border:0;border-radius:8px;padding:.65rem 1rem;cursor:pointer}.layout{display:grid;grid-template-columns:250px 1fr;min-height:calc(100vh - 70px)}nav{padding:1rem;display:flex;flex-direction:column;gap:.25rem;border-right:1px solid #26344d}nav button{text-align:left;background:transparent}.content{padding:2rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1rem}.card{background:#131f35;padding:1.2rem;border-radius:14px;overflow:auto}table{width:100%;border-collapse:collapse}td,th{padding:.6rem;border-bottom:1px solid #26344d;text-align:left}.muted{color:#a8b3c7}.error{color:#ff9b9b}@media(max-width:720px){.layout{grid-template-columns:1fr}nav{flex-direction:row;overflow:auto;border-right:0;border-bottom:1px solid #26344d}.content{padding:1rem}}"""
CLIENT_NAV = "Overview|balance,Usage|usage,Send SMS|developers,Bulk SMS|developers,Messages|messages,Delivery status|messages,Inbound messages|inbox,Numbers|numbers,Sender IDs|senders,API keys|developers,Webhooks|webhooks,Subaccounts|profile,Billing|balance,Transactions|transactions,Developers/API docs|developers,Compliance|messaging-policy,Opt-outs|opt-outs,Profile|profile,Security|security"
ADMIN_NAV = "Organizations|admin,Tenants|admin,Users|admin,Subaccounts|admin,Routes|admin,Carriers|admin,Connectors|admin,Pricing|admin,Balances|admin,Transactions|transactions,Messages|messages,DLRs|messages,Inbound SMS|inbox,Sender IDs|senders,Numbers|numbers,Campaigns|admin,Compliance|opt-outs,Suppression|opt-outs,Webhooks|webhooks,API clients|admin,System health|healthz,Audit log|admin"


def shell(title, nav, admin=False):
    links = "".join(
        f"<button data-view='{path}'>{label}</button>"
        for label, path in (x.split("|", 1) for x in nav.split(","))
    )
    return f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'><title>{title} | Telnexa</title><style>{PORTAL_CSS}</style></head><body data-page='dashboard' data-admin='{str(admin).lower()}'><header><strong>Telnexa</strong><div><span id=identity class=muted></span> <button id=logout>Logout</button></div></header><div class=layout><nav>{links}</nav><main class=content><h1>{title}</h1><div id=content class=grid><div class=card>Loading authenticated tenant data…</div></div></main></div><script src='/portal.js'></script></body></html>"


@app.get("/portal", response_class=HTMLResponse)
def portal():
    return shell("Client dashboard", CLIENT_NAV)


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><meta name=viewport content='width=device-width'><title>Telnexa | SMS infrastructure</title><style>body{font:18px system-ui;max-width:900px;margin:auto;padding:4rem;color:#10203d}nav{display:flex;gap:1rem;flex-wrap:wrap}a{color:#3159c5}.hero{padding:4rem 0}</style><nav><a href='/dashboard'>Dashboard</a><a href='/developers'>Developers</a><a href='/messaging-policy'>Messaging policy</a><a href='/privacy'>Privacy</a></nav><section class=hero><h1>Reliable, compliant SMS infrastructure</h1><p>Telnexa provides tenant-isolated messaging APIs, delivery events, inbound messaging, webhooks, prepaid billing, and compliance controls.</p></section>"""


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return portal()


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Sign in | Telnexa</title><style>{PORTAL_CSS}</style></head><body data-page='login'><main class=content style='max-width:440px;margin:10vh auto'><div class=card><h1>Sign in to Telnexa</h1><p>Secure organization login uses OpenID Connect Authorization Code with PKCE.</p><button id=login>Continue to identity provider</button><p id=status class=muted></p></div></main><script src='/portal.js'></script></body></html>"


@app.get("/developers", response_class=HTMLResponse)
def developers():
    return """<!doctype html><meta name=viewport content='width=device-width'><title>Developers | Telnexa</title><style>body{font:16px system-ui;max-width:760px;margin:auto;padding:3rem;color:#10203d}code{background:#eef2fa;padding:.2rem}</style><h1>Telnexa API</h1><p>OpenAPI: <a href='/developer/openapi'>interactive documentation</a>. All messaging writes require an idempotency key, tenant-bound credential, and correlation ID.</p><p>Production carrier submission remains disabled until provider approval.</p>"""


@app.get("/docs", response_class=HTMLResponse)
def docs_landing():
    return developers()


@app.get("/admin", response_class=HTMLResponse)
def admin():
    return shell("Admin dashboard", ADMIN_NAV, True)


@app.get("/portal.js")
def portal_js():
    response = Response(
        """(()=>{const issuer='/auth/realms/telnexa',client='telnexa-portal',redirect=location.origin+'/dashboard';const b64=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');const dec=t=>JSON.parse(atob(t.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));async function begin(){const raw=crypto.getRandomValues(new Uint8Array(48)),v=b64(raw),ch=b64(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v))),s=b64(crypto.getRandomValues(new Uint8Array(24)));sessionStorage.pkce=v;sessionStorage.state=s;location=issuer+'/protocol/openid-connect/auth?'+new URLSearchParams({client_id:client,response_type:'code',redirect_uri:redirect,scope:'openid',code_challenge:ch,code_challenge_method:'S256',state:s})}async function exchange(code){const r=await fetch(issuer+'/protocol/openid-connect/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'authorization_code',client_id:client,redirect_uri:redirect,code,code_verifier:sessionStorage.pkce})});if(!r.ok)throw Error('Token exchange failed');const z=await r.json();Object.entries(z).forEach(([k,v])=>sessionStorage[k]=v);history.replaceState({},'',redirect)}async function api(path){const c=dec(sessionStorage.access_token),r=await fetch('/api/v1/'+path,{headers:{Authorization:'Bearer '+sessionStorage.access_token,'X-Tenant-ID':c.tenant_id}});if(r.status===401){location='/login';throw Error('Session expired')}if(!r.ok)throw Error((await r.text()).slice(0,180));return r.json()}const esc=x=>String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));async function view(v){const box=document.querySelector('#content');if(['developers','profile','security','messaging-policy','admin'].includes(v)){box.innerHTML=`<div class=card><h2>${esc(v.replace('-',' '))}</h2><p>Use the <a href=/developer/openapi>OpenAPI console</a>. Privileged actions are tenant-bound and audit logged.</p></div>`;return}if(v==='healthz'){const r=await fetch('/healthz');box.innerHTML='<div class=card><h2>System health</h2><pre>'+esc(await r.text())+'</pre></div>';return}try{const z=await api(v),items=z.items||[];if(items.length){const keys=Object.keys(items[0]).slice(0,7);box.innerHTML='<div class=card style="grid-column:1/-1"><table><thead><tr>'+keys.map(k=>'<th>'+esc(k)+'</th>').join('')+'</tr></thead><tbody>'+items.slice(0,100).map(x=>'<tr>'+keys.map(k=>'<td>'+esc(typeof x[k]==='object'?JSON.stringify(x[k]):x[k])+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'}else box.innerHTML='<div class=card><pre>'+esc(JSON.stringify(z,null,2))+'</pre></div>'}catch(e){box.innerHTML='<div class="card error">'+esc(e.message)+'</div>'}}async function boot(){if(document.body.dataset.page==='login'){document.querySelector('#login').onclick=begin;return}const q=new URLSearchParams(location.search);if(q.get('code')){if(q.get('state')!==sessionStorage.state)throw Error('OIDC state mismatch');await exchange(q.get('code'))}if(!sessionStorage.access_token){location='/login';return}const c=dec(sessionStorage.access_token),roles=c.realm_access?.roles||[];document.querySelector('#identity').textContent=(c.preferred_username||c.sub)+' · '+c.tenant_id;if(document.body.dataset.admin==='true'&&!roles.some(x=>['OWNER','ADMIN','SUPPORT'].includes(x))){document.querySelector('#content').innerHTML='<div class="card error">Administrative role required.</div>'}else await view('balance');document.querySelectorAll('[data-view]').forEach(x=>x.onclick=()=>view(x.dataset.view));document.querySelector('#logout').onclick=async()=>{const rt=sessionStorage.refresh_token;sessionStorage.clear();if(rt)await fetch(issuer+'/protocol/openid-connect/logout',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:client,refresh_token:rt})});location='/login'}}boot().catch(e=>{const x=document.querySelector('#content')||document.querySelector('#status');if(x)x.textContent=e.message})})();""",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )
    response.body = response.body.replace(
        b"/auth/realms/telnexa", b"https://auth.codestra.co/realms/codestra"
    )
    response.headers["Content-Length"] = str(len(response.body))
    return response


LEGAL = {
    "sms-consent": (
        "SMS Consent",
        "Telnexa records explicit consent, its source, timestamp, purpose, and policy versions before marketing messaging.",
    ),
    "sms-opt-in": (
        "SMS Opt-in",
        "Opt in only through an identified tenant form or written agreement. Message frequency varies. Message and data rates may apply.",
    ),
    "sms-opt-out": (
        "SMS Opt-out",
        "Reply STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, or QUIT to stop non-exempt messages. Reply START to opt in again where permitted.",
    ),
    "terms": (
        "Terms",
        "Use is subject to applicable law, carrier rules, consent, content, payment, and acceptable-use requirements.",
    ),
    "privacy": (
        "Privacy",
        "We process account, messaging, delivery, consent, security, and billing data to provide and protect the service. Contact privacy@telnexa.co for requests.",
    ),
    "acceptable-use": (
        "Acceptable Use",
        "No unsolicited messaging, fraud, phishing, illegal content, evasion, purchased lists, or abusive traffic.",
    ),
    "messaging-policy": (
        "Messaging Policy",
        "Senders must document consent, identify themselves, honor STOP immediately, provide HELP, and observe quiet hours and carrier rules.",
    ),
    "anti-spam": (
        "Anti-spam",
        "Unsolicited commercial messaging is prohibited. Violations may cause immediate suspension and preservation of audit evidence.",
    ),
    "refund-policy": (
        "Refund Policy",
        "Approved refunds are returned according to the original funding method and applicable non-refundable carrier charges.",
    ),
    "contact": (
        "Contact",
        "Support: support@telnexa.co · Privacy: privacy@telnexa.co · Abuse: abuse@telnexa.co",
    ),
    "security": (
        "Security",
        "Telnexa uses TLS, tenant isolation, least privilege, immutable audit and billing records, monitoring, encrypted backups, and coordinated incident response.",
    ),
}


@app.get("/{page}", response_class=HTMLResponse)
def legal_page(page: str):
    if page not in LEGAL:
        raise HTTPException(404, "not_found")
    title, copy = LEGAL[page]
    return f"<!doctype html><meta name=viewport content='width=device-width'><title>{title} | Telnexa</title><style>body{{font:16px system-ui;max-width:760px;margin:auto;padding:3rem;line-height:1.6;color:#152033}}a{{color:#3159c5}}</style><h1>{title}</h1><p>{copy}</p><p><a href='/terms'>Terms</a> · <a href='/privacy'>Privacy</a> · <a href='/messaging-policy'>Messaging policy</a> · <a href='/contact'>Contact</a></p>"


from .product_api import router as product_router
from .auth_api import router as auth_router

app.include_router(product_router)
app.include_router(auth_router)
