# Telnexa SMS fabric branch map

```text
feat/codestra-keycloak-webhooks              # PR #15: separate machine trust
  -> integration/codestra-sms-fabric-v2      # PR #16: source-only design contract
       -> integration/middleware-sms-api-v1
       -> automation/sms-event-outbox-v1
       -> feature/sms-sender-template-policy-v1
       -> feature/sms-optout-suppression-v1
       -> feature/sms-conversation-routing-v1
       -> test/sms-fabric-contracts-v1
```

The child names are a planning map, not evidence of deployed implementations.
Existing billing, production-adapter and cross-server certification PRs remain
separate. Preserve current protected-main source and its accepted durable SMS
implementation. Merge #15 through its protected review gate before promoting
#16 to main; revalidate the exact source and current merge result after retargeting.
No branch in this map authorizes delivery, provider credentials, runtime changes,
force pushes or protection bypasses.
