FROM ubuntu:24.04

ARG G4_VERSION=11.3.2
ARG G4_DATA_VERSION=11.3

ENV DEBIAN_FRONTEND=noninteractive \
    G4INSTALL=/opt/geant4 \
    G4DATA=/opt/geant4/data

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build git wget ca-certificates \
    libexpat1-dev libxerces-c-dev \
    libx11-dev libxmu-dev libxext-dev libxt-dev libxpm-dev libxft-dev \
    libxerces-c-dev qtbase5-dev libqt5opengl5-dev \
    libglu1-mesa-dev freeglut3-dev \
    python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

# Build GEANT4
WORKDIR /tmp
RUN wget -q https://gitlab.cern.ch/geant4/geant4/-/archive/v${G4_VERSION}/geant4-v${G4_VERSION}.tar.gz \
 && tar xf geant4-v${G4_VERSION}.tar.gz \
 && mkdir build && cd build \
 && cmake -GNinja \
      -DCMAKE_INSTALL_PREFIX=${G4INSTALL} \
      -DGEANT4_INSTALL_DATA=ON \
      -DGEANT4_USE_OPENGL_X11=ON \
      -DGEANT4_USE_QT=ON \
      -DGEANT4_USE_GDML=ON \
      -DGEANT4_BUILD_MULTITHREADED=ON \
      -DGEANT4_BUILD_TLS_MODEL=global-dynamic \
      ../geant4-v${G4_VERSION} \
 && ninja -j"$(nproc)" install \
 && cd / && rm -rf /tmp/*

ENV PATH=${G4INSTALL}/bin:${PATH}
RUN echo "source ${G4INSTALL}/bin/geant4.sh" >> /etc/bash.bashrc

WORKDIR /work
CMD ["/bin/bash"]