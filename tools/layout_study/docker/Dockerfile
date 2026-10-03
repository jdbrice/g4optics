# Public 11.4.2-gui multi-architecture index, verified 2026-10-02.
# Its arm64 child is the public base of the tested Apple Silicon environment.
# No display server is used: OpNovice2 is built/run in batch mode.
FROM carlomt/geant4@sha256:7e660c618ad80506c66e9f509a285e7d9e76dadd75b66f28cbe378ccf91ae3f5
USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/layout-requirements.txt
RUN python3 -m venv /opt/layout-venv \
    && /opt/layout-venv/bin/python -m pip install --no-cache-dir -r /tmp/layout-requirements.txt \
    && /opt/layout-venv/bin/python -m pip freeze > /opt/layout-python-freeze.txt \
    && test "$(/opt/geant4/bin/geant4-config --version)" = 11.4.2
COPY docker/entrypoint.sh /opt/layout-entrypoint.sh
RUN chmod 755 /opt/layout-entrypoint.sh
ENV PATH="/opt/layout-venv/bin:/opt/geant4/bin:${PATH}" \
    GEANT4_DATA_DIR=/g4data \
    G4RUN_MANAGER_TYPE=Serial \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
ENTRYPOINT ["/opt/layout-entrypoint.sh"]
CMD ["bash"]
