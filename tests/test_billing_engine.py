import os,tempfile,threading
from datetime import datetime,timezone,timedelta
from decimal import Decimal
os.environ["BILLING_DATABASE_URL"]="sqlite:////tmp/telnexa-tests.db"
from billing.db import Base,engine,SessionLocal
from sqlalchemy.orm import close_all_sessions
from billing.models import *
from billing.engine import *
import pytest
@pytest.fixture(autouse=True)
def fresh():
    close_all_sessions();Base.metadata.drop_all(engine);Base.metadata.create_all(engine);yield;close_all_sessions()
def seed(balance="10",billing_type="prepaid",credit="0"):
    db=SessionLocal();p=PricingPlan(name="Starter",billing_type=billing_type);t=Tenant(name="A",status="active",plan_id=p.id);db.add_all([p,t]);db.flush();a=BillingAccount(tenant_id=t.id,currency="EUR",billing_type=billing_type,credit_limit=Decimal(credit));db.add(a);db.flush();w=Wallet(tenant_id=t.id,billing_account_id=a.id,currency="EUR",available=Decimal(balance));db.add(w);now=datetime.now(timezone.utc);db.add_all([Rate(kind="provider",country="ZZ",prefix="+",currency="EUR",amount=Decimal("0.03"),provider="simulator",effective_from=now-timedelta(days=1)),Rate(kind="sell",country="ZZ",prefix="+",currency="EUR",amount=Decimal("0.05"),effective_from=now-timedelta(days=1))]);db.commit();return db,t,a,w
def test_gsm_unicode_and_multipart_segments():
    assert segment_info("a"*160)==("GSM-7",160,1);assert segment_info("a"*161)[2]==2;assert segment_info("€"*81)[2]==2;assert segment_info("🙂"*36)[2]==2
def test_wallet_credit_is_idempotent():
    db,t,a,w=seed();credit(db,a.id,"5","credit-1");credit(db,a.id,"5","credit-1");db.commit();db.refresh(w);assert w.available==Decimal("15");assert db.query(LedgerEntry).count()==1
def test_reserve_release_and_finalize():
    db,t,a,w=seed();r=reserve(db,a.id,"1","r1","m1",str(uuid.uuid4()));db.flush();assert w.reserved==1;release(db,r.id,str(uuid.uuid4()));assert w.available==10;r2=reserve(db,a.id,"2","r2","m2",str(uuid.uuid4()));finalize(db,r2.id,str(uuid.uuid4()));db.commit();assert w.available==8 and w.reserved==0;assert db.query(LedgerEntry).filter_by(type="message_charge").count()==1
def test_insufficient_balance_and_postpaid_limit():
    db,t,a,w=seed("0");
    with pytest.raises(ValueError,match="insufficient_balance"):reserve(db,a.id,"1","r","m",str(uuid.uuid4()))
    db.rollback();a.billing_type="postpaid";a.credit_limit=Decimal("2");db.commit();assert reserve(db,a.id,"2","r2","m2",str(uuid.uuid4())).amount==2
def test_simulator_success_duplicate_and_no_double_charge():
    db,t,a,w=seed();c=str(uuid.uuid4());m=send_simulated(db,a.id,"+49123","Telnexa","hello","same",c);db.commit();m2=send_simulated(db,a.id,"+49123","Telnexa","hello","same",c);db.commit();assert m.id==m2.id;assert db.query(Reservation).count()==1;assert db.query(LedgerEntry).count()==1;assert m.status=="delivered"
def test_simulator_failure_releases_and_unicode_prices_segments():
    db,t,a,w=seed();m=send_simulated(db,a.id,"+49123","Telnexa","🙂"*36,"fail",str(uuid.uuid4()),"reject");db.commit();assert m.status=="failed" and m.segments==2;assert w.available==10 and w.reserved==0
def test_rate_specificity_effective_date_and_margin():
    db,t,a,w=seed();db.add(Rate(kind="sell",tenant_id=t.id,country="ZZ",prefix="+49",currency="EUR",amount=Decimal("0.08"),priority=2,effective_from=datetime.now(timezone.utc)-timedelta(hours=1)));db.commit();r=resolve_rate(db,"sell",t.id,"ZZ","+49123");assert r.amount==Decimal("0.08");m=send_simulated(db,a.id,"+49123","Telnexa","x","margin",str(uuid.uuid4()));db.commit();u=db.query(Usage).one();assert u.revenue-u.cost==Decimal("0.05")
def test_tenant_isolation_queries():
    db,t,a,w=seed();other=Tenant(name="B");db.add(other);db.commit();assert db.scalars(select(Wallet).where(Wallet.tenant_id==other.id)).all()==[];assert db.scalars(select(Message).where(Message.tenant_id==other.id)).all()==[]
def test_invoice_lifecycle_and_lines():
    db,t,a,w=seed();now=datetime.now(timezone.utc);i=Invoice(tenant_id=t.id,number="INV-2026-000001",currency="EUR",period_start=now-timedelta(days=30),period_end=now,subtotal=10,total=10);db.add(i);db.flush();db.add(InvoiceLine(invoice_id=i.id,description="SMS",quantity=100,unit_price=Decimal("0.1"),amount=10));i.status="issued";i.issue_date=now;i.due_date=now+timedelta(days=14);db.commit();assert db.query(InvoiceLine).filter_by(invoice_id=i.id).count()==1
def test_payment_verified_duplicate_constraint():
    db,t,a,w=seed();db.add(Payment(tenant_id=t.id,provider="manual",provider_reference="bank-1",amount=10,currency="EUR",status="successful",verified=True));db.commit();db.add(Payment(tenant_id=t.id,provider="manual",provider_reference="bank-1",amount=10,currency="EUR",status="successful",verified=True));
    with pytest.raises(Exception):db.commit()
