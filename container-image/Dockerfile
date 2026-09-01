# syntax=docker/dockerfile:1.7
ARG OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw@sha256:2f5ce8848a1a69b3c460622e566cb9395da9fd18d7ef7b038cd8e2c4f195decf
FROM ${OPENCLAW_IMAGE}

ARG OPENCLAW_VERSION=2026.7.1-1
ARG TEMPLATE_IDENTIFY
ARG AGENT_MANAGER_VERSION=0.5.0
ARG LAYOUT_PROTOCOL_VERSION=1

LABEL io.dola.unitag.template-identify="${TEMPLATE_IDENTIFY}" \
      io.dola.unitag.openclaw-version="${OPENCLAW_VERSION}" \
      io.dola.unitag.agent-manager-version="${AGENT_MANAGER_VERSION}" \
      io.dola.unitag.layout-protocol-version="${LAYOUT_PROTOCOL_VERSION}"

USER root
ENV PYTHONPATH=/opt/unitag/agent-manager
COPY agent_manage /opt/unitag/agent-manager/agent_manage
COPY scripts /opt/unitag/agent-manager/scripts

RUN test -n "${TEMPLATE_IDENTIFY}"
COPY container-image/templates/${TEMPLATE_IDENTIFY}.zip /home/node/template/${TEMPLATE_IDENTIFY}.zip
RUN mkdir -p /opt/unitag/openclaw-seed \
    && mkdir -p /home/node/.openclaw \
    && printf '{}\n' > /home/node/.openclaw/openclaw.json \
    && chown -R node:node /home/node/template /home/node/.openclaw /opt/unitag/openclaw-seed

USER node
RUN python3 /opt/unitag/agent-manager/scripts/agentctl.py add-agent \
      --template-name "${TEMPLATE_IDENTIFY}" \
      --workspace-root /home/node/.openclaw/data \
    && cp -a /home/node/.openclaw/. /opt/unitag/openclaw-seed/

USER root
RUN python3 -m agent_manage.seed \
      --seed /opt/unitag/openclaw-seed \
      --validate-only \
    && chown -R node:node /opt/unitag/openclaw-seed
COPY container-image/unitag-openclaw-entrypoint.sh /usr/local/bin/unitag-openclaw-entrypoint
RUN chmod 0755 /usr/local/bin/unitag-openclaw-entrypoint

USER node
ENTRYPOINT ["/usr/local/bin/unitag-openclaw-entrypoint"]
CMD ["node", "openclaw.mjs", "gateway", "--bind", "lan"]
