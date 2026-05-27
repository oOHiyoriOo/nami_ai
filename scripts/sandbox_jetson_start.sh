#!/bin/bash
# Startup script for the Jetson GPU sandbox container.
# Minimal SSH setup — Conda/NVM/SDKMAN are pre-baked into the jetson-containers image.
set -e

apt-get update -qq
apt-get install -y --no-install-recommends openssh-server

mkdir -p /run/sshd /secrets /root/.ssh /workspace
chmod 700 /root/.ssh

ssh-keygen -A

# ── SSH password ─────────────────────────────────────────────────────────────
if [ -z "${SANDBOX_PASSWORD}" ]; then
    SANDBOX_PASSWORD=$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 32)
fi
echo "root:${SANDBOX_PASSWORD}" | chpasswd
echo "${SANDBOX_PASSWORD}" > /secrets/sandbox_password

# ── SSH keys ─────────────────────────────────────────────────────────────────
if [ ! -f /secrets/sandbox_ssh_key ]; then
    ssh-keygen -t ed25519 -f /secrets/sandbox_ssh_key -N '' -C 'nami_ai_sandbox'
fi
cp /secrets/sandbox_ssh_key.pub /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# ── SSHD config ──────────────────────────────────────────────────────────────
echo 'PermitRootLogin prohibit-password' >> /etc/ssh/sshd_config
echo 'PasswordAuthentication no'         >> /etc/ssh/sshd_config

printf 'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n' \
    > /workspace/.sandbox_profile

exec /usr/sbin/sshd -D -e
