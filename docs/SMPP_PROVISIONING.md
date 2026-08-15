# SMPP provisioning

Billing plans define SMPP TPS and bind limits, but credentials are provisioned through the existing guarded Jasmin operator workflow. Generate secrets once, store only strong hashes in the portal, show plaintext once, require fixed-IP/VPN allowlists, and audit resets. Ports 2775 and 8990 remain private. No real provider or customer SMPP credential is included.
