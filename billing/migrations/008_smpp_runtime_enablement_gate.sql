UPDATE smpp_credentials
   SET enabled = false
 WHERE enabled = true;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'ck_smpp_credentials_runtime_provisioned_before_enable'
       AND conrelid = 'smpp_credentials'::regclass
  ) THEN
    ALTER TABLE smpp_credentials
      ADD CONSTRAINT ck_smpp_credentials_runtime_provisioned_before_enable
      CHECK (enabled = false);
  END IF;
END
$$;
