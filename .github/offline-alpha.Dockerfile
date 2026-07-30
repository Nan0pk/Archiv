FROM fedora:44
COPY . /opt/archiv-source
RUN /bin/bash /opt/archiv-source/tools/setup-fedora.sh --prefix /opt/archiv
ENV PATH="/opt/archiv/bin:${PATH}"
WORKDIR /opt/archiv-source
ENTRYPOINT ["/bin/bash", "/opt/archiv-source/tools/run-offline-acceptance.sh"]
