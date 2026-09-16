#!/usr/bin/env bash
# ==============================================================================
# Script: Automated NFS Server & Harbor Image Setup
# Target Host: Docker / NFS host (192.168.2.10)
# ==============================================================================

set -euo pipefail

NFS_EXPORT_DIR="/data/nfs/k8s-storage"
HARBOR_REGISTRY="192.168.2.34"
HARBOR_USER="admin"
IMAGE_TAG="h2372/nfs-subdir-external-provisioner:v1"

echo "=========================================================="
echo ">>> [1/4] Installing NFS Server packages..."
echo "=========================================================="

if command -v apt-get &>/dev/null; then
    echo "Detected Debian/Ubuntu system."
    apt-get update -y
    apt-get install -y nfs-kernel-server rpcbind
    NFS_SERVICE="nfs-kernel-server"
elif command -v dnf &>/dev/null; then
    echo "Detected RHEL/CentOS/Rocky/AlmaLinux system."
    dnf install -y nfs-utils rpcbind
    NFS_SERVICE="nfs-server"
elif command -v yum &>/dev/null; then
    echo "Detected legacy CentOS/RHEL system."
    yum install -y nfs-utils rpcbind
    NFS_SERVICE="nfs-server"
else
    echo "Error: Unsupported package manager. Please install NFS server manually."
    exit 1
fi

echo ">>> [2/4] Setting up NFS Export directory: ${NFS_EXPORT_DIR}"
mkdir -p "${NFS_EXPORT_DIR}"
chmod 777 "${NFS_EXPORT_DIR}"

EXPORT_LINE="${NFS_EXPORT_DIR} *(rw,sync,no_subtree_check,no_root_squash,insecure)"
if ! grep -qF "${NFS_EXPORT_DIR}" /etc/exports 2>/dev/null; then
    echo "${EXPORT_LINE}" >> /etc/exports
    echo "Export line added to /etc/exports"
else
    echo "Export configuration already exists in /etc/exports"
fi

exportfs -rav

systemctl enable --now rpcbind || true
systemctl enable --now "${NFS_SERVICE}"
systemctl restart "${NFS_SERVICE}"

echo ">>> Verifying active NFS exports:"
exportfs -v

echo "=========================================================="
echo ">>> [3/4] Authenticating to Harbor & Pushing Image (${IMAGE_TAG})..."
echo "=========================================================="

if command -v docker &>/dev/null; then
    mkdir -p /etc/docker
    if [ ! -f /etc/docker/daemon.json ] || ! grep -q "${HARBOR_REGISTRY}" /etc/docker/daemon.json; then
        cat <<EOF > /etc/docker/daemon.json
{
  "insecure-registries": ["${HARBOR_REGISTRY}"]
}
EOF
        systemctl restart docker || true
    fi

    echo "Logging into Harbor ${HARBOR_REGISTRY}..."
    echo "${HARBOR_PASS}" | docker login "${HARBOR_REGISTRY}" -u "${HARBOR_USER}" --password-stdin || true

    echo "Preparing provisioner image for Harbor..."
    docker pull obegron/nfs-subdir-external-provisioner:v5.0.1-fork.1 || true
    docker tag obegron/nfs-subdir-external-provisioner:v5.0.1-fork.1 "${IMAGE_TAG}"
    docker push "${IMAGE_TAG}" || echo "Notice: Push failed; check Harbor connectivity."
else
    echo "Docker not found on this machine. Skipping image push."
fi

echo "=========================================================="
echo ">>> [4/4] SETUP COMPLETED ON 192.168.2.10!"
echo "    NFS Export:   ${NFS_EXPORT_DIR}"
echo "    Harbor Image: ${IMAGE_TAG}"
echo "=========================================================="
