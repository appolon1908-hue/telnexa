# Payments

Payment records are provider-neutral and deduplicate provider/reference pairs. `manual` is the only enabled provider and requires authenticated back-office verification; browser redirects never credit a wallet. Future Stripe, PayPal, bank and Odoo adapters must verify signed server callbacks, record pending/success/failure/refund transitions, and issue idempotent ledger entries through the billing engine.
