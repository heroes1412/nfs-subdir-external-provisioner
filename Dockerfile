# Copyright 2026 The Kubernetes Authors.
# Modified in Obegron fork; see git history for details.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM --platform=$BUILDPLATFORM golang:1.26 AS build-env

ARG TARGETOS
ARG TARGETARCH

WORKDIR /src
COPY . .

# Auto-recovery: If vendor directory is missing or incomplete, fall back to downloading modules
RUN if [ -d "vendor" ]; then \
        go build -mod=vendor -o /dev/null ./cmd/nfs-subdir-external-provisioner 2>/dev/null || (echo "[*] Incomplete vendor detected, downloading via go.mod..." && rm -rf vendor && go mod download); \
    else \
        go mod download; \
    fi

# Compile static binary for target architecture
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} \
    go build -a -ldflags '-extldflags "-static"' \
    -o /bin/main ./cmd/nfs-subdir-external-provisioner

FROM scratch
COPY --from=build-env /bin/main /nfs-subdir-external-provisioner
COPY --from=build-env /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

ENTRYPOINT ["/nfs-subdir-external-provisioner"]
