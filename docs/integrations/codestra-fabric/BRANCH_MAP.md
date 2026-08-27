# Telnexa SMS fabric branch map

```text
feat/codestra-keycloak-webhooks
  -> integration/codestra-sms-fabric-v2
       -> integration/middleware-sms-api-v1
       -> automation/sms-event-outbox-v1
       -> feature/sms-sender-template-policy-v1
       -> feature/sms-optout-suppression-v1
       -> feature/sms-conversation-routing-v1
       -> test/sms-fabric-contracts-v1
```

Existing billing, production-adapter and cross-server certification PRs remain separate. This branch does not rewrite or activate them.