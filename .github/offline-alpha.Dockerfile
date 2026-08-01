FROM fedora:44
ARG ARCHIV_REF=main
COPY tools/install-fedora.sh /tmp/install-fedora.sh
RUN /bin/bash /tmp/install-fedora.sh \
    --ref "$ARCHIV_REF" \
    --prefix /opt/archiv \
    --bin-dir /opt/archiv/bin
COPY . /opt/archiv-source
ENV PATH="/opt/archiv/bin:${PATH}"
WORKDIR /opt/archiv-source
ENTRYPOINT ["/bin/bash", "/opt/archiv-source/tools/run-offline-acceptance.sh"]
