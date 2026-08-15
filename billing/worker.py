import hashlib,hmac,json,time,urllib.request
from datetime import datetime,timezone,timedelta
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import Outbox
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
        db.commit();return sent
if __name__=="__main__":
    while True:once();time.sleep(5)
