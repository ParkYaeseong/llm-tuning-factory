#!/bin/sh
set -eu
mkdir -p /workspace/factory /root/.ssh /run/sshd
chmod 700 /root/.ssh
if [ -f /run/secrets/authorized_keys ]; then
    cp /run/secrets/authorized_keys /root/.ssh/authorized_keys
elif [ -n "${PUBLIC_KEY:-}" ]; then
    printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
fi
if [ ! -s /root/.ssh/authorized_keys ]; then
    echo 'Provide PUBLIC_KEY or /run/secrets/authorized_keys before starting the worker image.' >&2
    exit 1
fi
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
exec /usr/sbin/sshd -D -e
