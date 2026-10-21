# Standalone local runtime. Only writable job storage is mounted at runtime.
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends libasound2t64 libbsd0 libxtst6 libxi6 libxrender1 \
       python3 python3-venv ca-certificates xvfb xauth libnss3 libxcomposite1 \
       libxcursor1 libxdamage1 libxrandr2 libegl1 libgl1 libdbus-1-3 libfontconfig1 \
       libfreetype6 libxkbcommon-x11-0 libxcb-cursor0 libxss1 libglib2.0-0t64 \
       libopengl0 fonts-dejavu-core openjdk-21-jdk-headless squashfs-tools \
    && rm -rf /var/lib/apt/lists/*
# Extract official packages without desktop registration or FUSE privileges.
RUN --mount=type=bind,source=downloads,target=/downloads \
    echo 'f20113aaa33b3149ec8d6a09b2a7963360e65fafd92d69389987a85bbc3ec7a3  /downloads/Audiveris-5.11.0-ubuntu24.04-x86_64.deb' | sha256sum -c - \
    && echo '9233ed1b87d3e6b45722278f3c286dcd41e83da778bd0f80a1dd04949696ad93  /downloads/MuseScore-Studio-4.7.4.260706075-x86_64.AppImage' | sha256sum -c - \
    && dpkg-deb -x /downloads/Audiveris-5.11.0-ubuntu24.04-x86_64.deb / \
    && python3 -c "from pathlib import Path; import subprocess,re; p=Path('/downloads/MuseScore-Studio-4.7.4.260706075-x86_64.AppImage'); data=p.read_bytes(); offsets=[m.start() for m in re.finditer(b'hsqs',data) if data[m.start()+28:m.start()+32]==bytes([4,0,0,0])]; assert len(offsets)==1; offset=offsets[0]; subprocess.run(['unsquashfs','-no-progress','-d','/opt/musescore','-o',str(offset),str(p)],check=True)"
RUN python3 -m venv /opt/python \
    && /opt/python/bin/pip install --no-cache-dir PyMuPDF==1.26.7 pillow==13.0.0 mido==1.3.3 packaging==26.3
RUN printf '#!/bin/sh\nexec xvfb-run -a /opt/musescore/AppRun "$@"\n' > /usr/local/bin/mscore \
    && chmod +x /usr/local/bin/mscore
ENV PATH="/opt/python/bin:${PATH}" PDF_AUDIVERIS=/opt/audiveris/bin/Audiveris \
    PDF_MUSESCORE=/usr/local/bin/mscore PDF_TESSDATA=/tools/tessdata \
    PDF_EXPORT_CACHE=/cache/export-fix PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp/home XDG_CONFIG_HOME=/tmp/home/.config XDG_CACHE_HOME=/tmp/home/.cache
RUN apt-get update && apt-get install -y --no-install-recommends libgtk-3-0t64 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
ENV LANG=C.UTF-8 JDK_JAVA_OPTIONS="-Xmx512m -XX:ActiveProcessorCount=2"

COPY .build/support/eng.traineddata /opt/tessdata/eng.traineddata
COPY .build/support/ecj-3.44.0.jar /opt/livesheet-cache/export-fix/ecj-3.44.0.jar
RUN echo 'daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047  /opt/tessdata/eng.traineddata' | sha256sum -c - \
    && echo '14c46ddc2e511c46c998da728f6a80f13a689c920f3832402f7ad150f6a6188d  /opt/livesheet-cache/export-fix/ecj-3.44.0.jar' | sha256sum -c -
COPY pyproject.toml LICENSE THIRD_PARTY_NOTICES.md /build/
COPY livesheet /build/livesheet
RUN pip install --no-cache-dir --no-deps /build \
    && PDF_EXPORT_CACHE=/opt/livesheet-cache/export-fix python -c "from livesheet.audiveris_export_recovery import build_overlay; from livesheet.pdf_ocr_guard import SOURCES; p='/opt/audiveris/bin/Audiveris'; build_overlay(p); build_overlay(p,sources=SOURCES,cache_name='ocr-guard')" \
    && mkdir -p /opt/livesheet-notices \
    && cp /build/LICENSE /build/THIRD_PARTY_NOTICES.md /opt/livesheet-notices/ \
    && rm -rf /build
ENV PDF_TESSDATA=/opt/tessdata PDF_EXPORT_CACHE=/data/cache/export-fix LIVESHEET_STORAGE=/data/jobs
WORKDIR /data
USER 1000:1000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3176/health',timeout=3).read()"
ENTRYPOINT ["python", "-m", "livesheet.container"]
CMD ["--bind", "0.0.0.0", "--storage", "/data/jobs"]

FROM runtime AS test
COPY tests /opt/livesheet-tests
COPY tools /opt/livesheet-tools
