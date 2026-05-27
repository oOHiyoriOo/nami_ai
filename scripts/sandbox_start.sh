#!/bin/bash
# Startup script for the standard sandbox container (Ubuntu 22.04, x86/ARM).
# Installs SSH, Conda, NVM, SDKMAN on first boot (volumes persist the installs).
set -e

apt-get update -qq
apt-get install -y --no-install-recommends \
    openssh-server bash curl wget git build-essential python3 python3-pip

# ── Miniconda ────────────────────────────────────────────────────────────────
if [ ! -f /opt/conda/bin/conda ]; then
    CONDA_ARCH=$(case "$(uname -m)" in
        x86_64)           echo x86_64 ;;
        aarch64|arm64)    echo aarch64 ;;
        *) echo "Unsupported Miniconda architecture: $(uname -m)" >&2; exit 1 ;;
    esac)
    curl -fsSL "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${CONDA_ARCH}.sh" \
        -o /root/miniconda.sh
    bash /root/miniconda.sh -b -p /opt/conda
    rm /root/miniconda.sh
fi

# ── NVM ──────────────────────────────────────────────────────────────────────
export NVM_DIR=/root/.nvm
if [ ! -d /root/.nvm ]; then
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
fi

# ── SDKMAN ───────────────────────────────────────────────────────────────────
export SDKMAN_DIR=/root/.sdkman
if [ ! -d /root/.sdkman ]; then
    curl -fsSL https://get.sdkman.io | bash
fi

# ── Directories & profile ────────────────────────────────────────────────────
mkdir -p /run/sshd /workspace /secrets /root/.ssh
cat > /workspace/.sandbox_profile <<'PROFILE'
export SDKMAN_DIR=/root/.sdkman
[ -s /root/.sdkman/bin/sdkman-init.sh ] && source /root/.sdkman/bin/sdkman-init.sh
export NVM_DIR=/root/.nvm
[ -s /root/.nvm/nvm.sh ] && source /root/.nvm/nvm.sh
export PATH=/opt/conda/bin:$PATH
PROFILE

chmod 700 /root/.ssh

# ── SSH password ─────────────────────────────────────────────────────────────
if [ -z "${SANDBOX_PASSWORD}" ]; then
    SANDBOX_PASSWORD=$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 32)
fi
echo "root:${SANDBOX_PASSWORD}" | chpasswd
echo "${SANDBOX_PASSWORD}" > /secrets/sandbox_password

# ── SSH keys ─────────────────────────────────────────────────────────────────
ssh-keygen -A
if [ ! -f /secrets/sandbox_ssh_key ]; then
    ssh-keygen -t ed25519 -f /secrets/sandbox_ssh_key -N '' -C 'nami_ai_sandbox'
fi
cp /secrets/sandbox_ssh_key.pub /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# ── SSHD config ──────────────────────────────────────────────────────────────
echo 'PermitRootLogin prohibit-password' >> /etc/ssh/sshd_config
echo 'PasswordAuthentication no'         >> /etc/ssh/sshd_config

exec /usr/sbin/sshd -D -e
