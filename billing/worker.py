import hashlib,hmac,json,time,urllib.request,uuid
from datetime import datetime,timezone,timedelta
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import Outbox,Webhook,WebhookDelivery,MessageEvent,Message,InboundMessage
from .webhooks import decrypt_secret,validate_webhook_url

def deliver_webhooks(db,sender=None):
    now=datetime.now(timezone.utc);rows=db.scalars(select(WebhookDelivery).where(WebhookDelivery.status.in_(["pending","retrying"]),((WebhookDelivery.next_attempt_at==None)|(WebhookDelivery.next_attempt_at<=now))).order_by(WebhookDelivery.created_at).limit(20).with_for_update(skip_locked=True)).all();sent=0
    for row in rows:
        hook=db.scalar(select(Webhook).where(Webhook.id==row.webhook_id,Webhook.tenant_id==row.tenant_id,Webhook.enabled==True))
        event=db.scalar(select(MessageEvent).where(MessageEvent.id==row.event_id,MessageEvent.tenant_id==row.tenant_id)); inbound=None
        if event:
            msg=db.scalar(select(Message).where(Message.id==event.message_id,Message.tenant_id==row.tenant_id));event_type="sms."+event.status;payload={"message_id":event.message_id,"status":event.status,"raw_provider_status":event.provider_response,"correlation_id":msg.correlation_id if msg else None}
        else:
            inbound=db.scalar(select(InboundMessage).where(InboundMessage.id==row.event_id,InboundMessage.tenant_id==row.tenant_id));event_type="sms.received";payload={"inbound_message_id":inbound.id,"from":inbound.sender,"to":inbound.destination,"content":inbound.content} if inbound else {}
        try:
            if not hook or (not event and not inbound):raise RuntimeError("webhook_or_event_unavailable")
            validate_webhook_url(hook.url);body=json.dumps({"id":row.id,"type":event_type,"tenant_id":row.tenant_id,"created_at":row.created_at.isoformat(),"data":payload},separators=(",",":"),sort_keys=True).encode();ts=str(int(time.time()));secret=decrypt_secret(hook.secret_ciphertext);sig=hmac.new(secret.encode(),ts.encode()+b"."+body,hashlib.sha256).hexdigest();req=urllib.request.Request(hook.url,data=body,headers={"Content-Type":"application/json","User-Agent":"Telnexa-Webhooks/1.0","X-Telnexa-Timestamp":ts,"X-Telnexa-Signature":"sha256="+sig,"X-Telnexa-Event-Id":row.id})
            (sender or urllib.request.urlopen)(req,timeout=10);row.status="delivered";row.response_code=200;sent+=1
        except Exception:
            row.attempts+=1;row.status="failed" if row.attempts>=8 else "retrying";row.next_attempt_at=now+timedelta(seconds=min(3600,30*(2**min(row.attempts,7))))
    return sent
def once(sender=None):
    with SessionLocal() as db:
        now=datetime.now(timezone.utc);rows=db.scalars(select(Outbox).where(Outbox.state.in_(["pending","retrying"]),((Outbox.next_attempt_at==None)|(Outbox.next_attempt_at<=now))).order_by(Outbox.created_at).limit(20).with_for_update(skip_locked=True)).all();sent=0
        for row in rows:
            body=json.dumps(row.envelope,separators=(",",":"),sort_keys=True).encode();ts=str(int(time.time()));sig=hmac.new(settings.middleware_hmac_secret.encode(),f"{ts}\n{row.id}\ntelnexa\n".encode()+body,hashlib.sha256).hexdigest()
            req=urllib.request.Request(settings.middleware_url,data=body,headers={"Content-Type":"application/json","Authorization":"Bearer "+settings.middleware_api_key,"X-Event-Id":row.id,"X-Timestamp":ts,"X-Signature":"sha256="+sig,"Idempotency-Key":row.idempotency_key})
            try:
                if not settings.middleware_api_key or not settings.middleware_hmac_secret:raise RuntimeError("middleware_credentials_unavailable")
                (sender or urllib.request.urlopen)(req,timeout=10);row.state="delivered";sent+=1
            except Exception as e:
                row.attempts+=1;row.state="dead-lettered" if row.attempts>=4 else "retrying";row.last_error=str(e)[:500];row.next_attempt_at=datetime.now(timezone.utc)+timedelta(seconds=[60,300,900,3600][min(row.attempts-1,3)])
        sent+=deliver_webhooks(db,sender);db.commit();return sent
if __name__=="__main__":
    while True:once();time.sleep(5)
