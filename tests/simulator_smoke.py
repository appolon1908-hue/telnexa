"""Safe deployed-stack smoke test. Creates only namespaced simulator data."""
import json,os,uuid,urllib.request
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from sqlalchemy import select
from billing.app import ph
from billing.db import SessionLocal
from billing.models import ApiKey,BillingAccount,Contact,PricingPlan,Provider,Rate,Route,Tenant,Wallet

def request(path,method="GET",body=None,headers=None):
    req=urllib.request.Request("http://127.0.0.1:8000"+path,data=json.dumps(body).encode() if body else None,method=method,headers={"Content-Type":"application/json",**(headers or {})})
    with urllib.request.urlopen(req,timeout=10) as response:return response.status,json.loads(response.read()) if response.length!=0 else {}

run=uuid.uuid4().hex[:10];raw="tnx_"+uuid.uuid4().hex+uuid.uuid4().hex[:8]
with SessionLocal() as db:
    plan=PricingPlan(name="Simulator "+run);tenant=Tenant(name="Simulator smoke "+run,status="active",plan_id=plan.id);db.add_all([plan,tenant]);db.flush();account=BillingAccount(tenant_id=tenant.id,currency="EUR");db.add(account);db.flush();db.add_all([Wallet(tenant_id=tenant.id,billing_account_id=account.id,currency="EUR",available=Decimal("10")),ApiKey(tenant_id=tenant.id,prefix=raw[:12],secret_hash=ph.hash(raw),scopes="admin")]);now=datetime.now(timezone.utc);db.add_all([Rate(kind="provider",country="ZZ",prefix="+",currency="EUR",amount=Decimal(".02"),provider="simulator",effective_from=now-timedelta(days=1)),Rate(kind="sell",country="ZZ",prefix="+",currency="EUR",amount=Decimal(".04"),effective_from=now-timedelta(days=1))]);provider=Provider(name="Simulator "+run,connector="simulator:"+run,state="enabled",health_score=100,circuit_state="closed",tps=100);db.add(provider);db.flush();db.add(Route(country="ZZ",prefix="+",provider_id=provider.id,priority=100,enabled=True));db.commit();tenant_id=tenant.id;account_id=account.id
h={"X-Tenant-ID":tenant_id,"X-API-Key":raw,"Idempotency-Key":"smoke-"+run}
status,message=request("/api/v1/messages","POST",{"billing_account_id":account_id,"destination":"+4915550001","sender":"Telnexa","content":"Simulator smoke € 🙂","simulator_outcome":"sent"},h);assert status==202 and message["simulated"]
admin=os.environ["BILLING_ADMIN_TOKEN"]
status,dlr=request("/api/v1/simulator/dlr","POST",{"tenant_id":tenant_id,"message_id":message["message_id"],"external_event_id":"dlr-"+run,"status":"delivered"},{"X-Simulator-Token":admin});assert status==202 and dlr["status"]=="delivered"
status,mo=request("/api/v1/simulator/mo","POST",{"tenant_id":tenant_id,"provider_message_id":"mo-"+run,"sender":"+4915550001","destination":"+4915559999","content":"STOP"},{"X-Simulator-Token":admin});assert status==202 and mo["event"]=="sms.opted_out"
status,preview=request("/api/v1/admin/routes/preview?destination=%2B4915550001&tenant_id="+tenant_id,headers={"X-Admin-Token":admin});assert status==200 and preview["selected"]["simulator"]
print(json.dumps({"result":"PASS","tenant_id":tenant_id,"message_id":message["message_id"],"dlr":dlr["status"],"mo":mo["event"],"route":"simulator"}))
