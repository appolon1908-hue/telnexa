# Deployment identities

Verified 2026-08-15 on application server `37.27.128.39` / `10.40.0.2`.

| Service | Unix identity | Controlled application path | Root-owned management command |
|---|---|---|---|
| Kyqra | `kyqra-deploy` | `/opt/kyqra` | `/usr/local/sbin/kyqra-stack` |
| Telnexa | `telnexa-deploy` | `/opt/telnexa` | `/usr/local/sbin/telnexa-stack` |
| Klyrow | `klyrow-deploy` | `/opt/klyrow` | `/usr/local/sbin/klyrow-stack` |

Each account has a private home, `/bin/bash`, a locked password and one dedicated ED25519 public key in its own `~/.ssh/authorized_keys`. Key fingerprints are different. `.ssh` is mode 0700 and `authorized_keys` is mode 0600.

The dedicated Kyqra and Telnexa keys were removed from root's active `authorized_keys`; unrelated root credentials and password/root SSH settings remain enabled.

Deployment users are not members of the `docker` group. `/etc/sudoers.d/deployment-identities` permits only their own root-owned wrapper and `/usr/local/sbin/deploy-firewall-status`. Wrappers accept the fixed operations `status`, `config`, `pull`, `up`, `restart`, and `logs`; they contain fixed Compose project/file paths and accept no caller-controlled service, file, project or Docker arguments. Cross-service wrapper execution is denied.

Current root/password SSH access and global SSH daemon configuration were intentionally left unchanged.

Middleware must retain a different private key for each alias and map aliases as follows:

```sshconfig
Host kyqra-server
    HostName 10.40.0.2
    User kyqra-deploy
    IdentityFile <dedicated-kyqra-private-key>
    IdentitiesOnly yes

Host telnexa-server
    HostName 10.40.0.2
    User telnexa-deploy
    IdentityFile <dedicated-telnexa-private-key>
    IdentitiesOnly yes

Host klyrow-server
    HostName 10.40.0.2
    User klyrow-deploy
    IdentityFile <dedicated-klyrow-private-key>
    IdentitiesOnly yes
```

Do not copy private keys to the application server. The middleware alias and negative authentication tests remain a middleware-side launch gate because SSH to `10.40.0.1:22` timed out during this execution.
