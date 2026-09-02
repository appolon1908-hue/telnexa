from billing import sms_provider_service


def test_provider_runtime_does_not_create_production_schema_implicitly(monkeypatch):
    observed = {}

    def factory(database_url, *, create_schema):
        observed.update(database_url=database_url, create_schema=create_schema)
        return object(), object()

    class Service:
        def __init__(self, *, session_factory, transport):
            observed.update(session_factory=session_factory, transport=transport)

    monkeypatch.delenv("TELNEXA_SMS_AUTO_CREATE_SCHEMA", raising=False)
    monkeypatch.setattr(sms_provider_service, "create_session_factory", factory)
    monkeypatch.setattr(sms_provider_service, "SmsProviderService", Service)

    sms_provider_service.build_provider_service()

    assert observed["create_schema"] is False
