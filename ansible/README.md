# Deploying CKAD Lab to a VPS (Debian 13)

Two playbooks, run from your machine. **Status: syntax-checked and linted, not yet applied to a server.**
The first real run is the real test; the smoke test at the end of the `ckad_lab` role proves that rootless
kind works before the service is started.

## What it builds

```
Internet ──:80──> ufw ──> nginx (basic auth, rate limits, Host allow-list)
                              │  127.0.0.1:8000 only
                              v
                     ckad-lab.service  (user `ckad`: no shell, no SSH, sandboxed by systemd)
                              │  rootless Docker socket
                              v
                     kind clusters + student shells  (privileged only inside a user namespace)
```

| Layer | What it does |
|---|---|
| SSH | Key only, only `deploy` may log in, root login off. The cloud-init drop-in re-enables passwords, so ours is `00-...` (first value wins). |
| Firewall | ufw: deny all inbound except SSH (rate limited) and the web port. Optional CIDR allow-lists for both. |
| nginx | HTTP basic auth, hostname allow-list (other Host headers get 444), per-IP limits (session creation is 6/min), no `/docs`, only the methods the app uses, app hidden behind loopback. |
| fail2ban | Bans repeated failures on sshd and on nginx basic auth. |
| Rootless Docker | The important one. kind nodes are privileged containers; with rootless Docker an escape lands as the unprivileged `ckad` user, not root. |
| systemd sandbox | `NoNewPrivileges`, no capabilities, read-only filesystem except the data dir, restricted namespaces and address families. |
| Egress filter | The `ckad` user (the app **and every student pod**) may only reach ports 80/443 and DNS, and never private, CGNAT or link-local ranges. No spam relay, no scanning, no mining pool on odd ports, no reaching internal services. |
| Host | sysctl hardening, unattended security updates, LLMNR/mDNS off (the stock image listens on 5355). |

## Run it

Prerequisites on your machine: `ansible` (with the `community.general` and `ansible.posix` collections), `rsync`, an SSH key.

```bash
cd ansible
cp inventory.example.ini inventory.ini      # your server (git-ignored)
cp vault.example.yml vault.yml              # set a long random password, then:
ansible-vault encrypt vault.yml             #   remember the vault password

ansible-playbook bootstrap.yml                                # once, as root: creates `deploy`, PROVES key login works
ansible-playbook site.yml -e @vault.yml --ask-vault-pass      # hardening + deployment (connects as `deploy`)
```

`bootstrap.yml` aborts if key login as `deploy` fails, so `site.yml` never locks you out. Re-run `site.yml` any
time to redeploy the app from your local checkout (no Git credentials ever reach the server).

Useful variables (`group_vars/all.yml`): `ckad_allowed_cidrs` (who may reach the web port), `ssh_allowed_cidrs`,
`ckad_max_sessions` (each cluster needs 1-1.5 GB RAM; a 7.8 GB server handles 2-3), `ckad_smoke_test`.

## Read this before opening it to the internet

- **Basic auth over plain HTTP sends the password in clear text.** Port 80 is what was asked for, but for anything
  beyond a few trusted friends, put a domain in front, enable TLS (443), or set `ckad_allowed_cidrs` so only known
  addresses can reach it at all. Until then, an `ssh -L 8080:127.0.0.1:80 deploy@server` tunnel is the safe way in.
- **Everyone with the password gets a shell inside a Kubernetes node.** Rootless Docker turns a container escape
  into an unprivileged account, which is a big improvement, but kernel bugs in user namespaces exist. Treat the
  VPS as disposable: nothing else valuable on it, no shared credentials, snapshots enabled.
- There is no per-user login in the app itself: one shared password, and the session id is the only secret between
  students. Per-user accounts and quotas are still on the roadmap.
- If you lock yourself out anyway, use the provider's web console to remove
  `/etc/ssh/sshd_config.d/00-ckad-hardening.conf` and `ufw disable`.

## Where things live on the server

| What | Where |
|---|---|
| App code (root-owned, read-only for the service) | `/opt/ckad-lab` |
| Session records and per-session work dirs | `/var/lib/ckad-lab` |
| Service / logs | `systemctl status ckad-lab`, `journalctl -u ckad-lab` |
| Egress filter | `/etc/ckad-egress.nft`, `ckad-egress.service` |
| nginx site / password file | `/etc/nginx/conf.d/ckad-lab.conf`, `/etc/nginx/ckad-lab.htpasswd` |
| Rootless Docker (as `ckad`) | `sudo -u ckad XDG_RUNTIME_DIR=/run/user/$(id -u ckad) DOCKER_HOST=unix:///run/user/$(id -u ckad)/docker.sock docker ps` |
