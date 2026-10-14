FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/workspace/cache/huggingface \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends openssh-server ca-certificates git \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd /root/.ssh \
    && printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/factory.conf

WORKDIR /opt/factory
COPY containers/gpu-requirements.lock /opt/factory/containers/gpu-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r containers/gpu-requirements.lock
COPY pyproject.toml README.md uv.lock ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY containers/entrypoint.sh /usr/local/bin/factory-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/factory-entrypoint && chmod 755 /usr/local/bin/factory-entrypoint
EXPOSE 22
ENTRYPOINT ["factory-entrypoint"]
