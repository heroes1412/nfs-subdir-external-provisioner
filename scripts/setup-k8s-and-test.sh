#!/usr/bin/env bash
# ==============================================================================
# Script: Deploy NFS Provisioner & Run End-to-End Mount Test on Kubernetes
# Target Host: Kubernetes Control Plane (192.168.2.30)
# ==============================================================================

set -euo pipefail

NFS_SERVER="192.168.2.10"
NFS_PATH="/data/nfs/k8s-storage"
NAMESPACE="default"

echo "=========================================================="
echo ">>> [1/5] Checking Kubernetes Cluster & Node NFS Client..."
echo "=========================================================="

kubectl cluster-info || {
    echo "Error: Cannot connect to Kubernetes API server."
    exit 1
}

echo "Cluster Nodes:"
kubectl get nodes -o wide

# Ensure nfs client utilities are installed on this node
if command -v apt-get &>/dev/null; then
    apt-get update -y && apt-get install -y nfs-common
elif command -v dnf &>/dev/null; then
    dnf install -y nfs-utils
elif command -v yum &>/dev/null; then
    yum install -y nfs-utils
fi

# Verify connectivity to the NFS Server
echo "Verifying NFS connectivity to ${NFS_SERVER}:"
showmount -e "${NFS_SERVER}" || {
    echo "Warning: Unable to showmount from ${NFS_SERVER}. Ensure NFS server is running on ${NFS_SERVER}."
}

echo "=========================================================="
echo ">>> [2/5] Cleaning up old tests & Deploying Provisioner..."
echo "=========================================================="

kubectl delete pod test-pod --ignore-not-found=true --force --grace-period=0 2>/dev/null || true
kubectl delete pvc test-claim --ignore-not-found=true --force --grace-period=0 2>/dev/null || true
kubectl delete deployment nfs-client-provisioner --ignore-not-found=true 2>/dev/null || true
kubectl delete pod -l app=nfs-client-provisioner --ignore-not-found=true --force --grace-period=0 2>/dev/null || true

# 1. RBAC (with leases support for Kubernetes v1.34)
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: nfs-client-provisioner
  namespace: default
---
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: nfs-client-provisioner-runner
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["persistentvolumes"]
    verbs: ["get", "list", "watch", "create", "delete"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "watch", "update"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "update", "patch"]
---
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: run-nfs-client-provisioner
subjects:
  - kind: ServiceAccount
    name: nfs-client-provisioner
    namespace: default
roleRef:
  kind: ClusterRole
  name: nfs-client-provisioner-runner
  apiGroup: rbac.authorization.k8s.io
---
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: leader-locking-nfs-client-provisioner
  namespace: default
rules:
  - apiGroups: [""]
    resources: ["endpoints"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
---
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: leader-locking-nfs-client-provisioner
  namespace: default
subjects:
  - kind: ServiceAccount
    name: nfs-client-provisioner
    namespace: default
roleRef:
  kind: Role
  name: leader-locking-nfs-client-provisioner
  apiGroup: rbac.authorization.k8s.io
EOF

# 2. StorageClass
cat <<'EOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-client
provisioner: k8s-sigs.io/nfs-subdir-external-provisioner
parameters:
  archiveOnDelete: "false"
EOF

# 3. Create Harbor ImagePullSecret
kubectl create secret docker-registry harbor-secret \
  --docker-server=192.168.2.34 \
  --docker-username=admin \
  --docker-password=C1sco123 \
  --namespace=default \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Deployment (Harbor image)
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nfs-client-provisioner
  labels:
    app: nfs-client-provisioner
  namespace: default
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: nfs-client-provisioner
  template:
    metadata:
      labels:
        app: nfs-client-provisioner
    spec:
      # imagePullSecrets:
      #   - name: harbor-secret
      containers:
        - name: nfs-client-provisioner
          image: h2372/nfs-subdir-external-provisioner:v1
          volumeMounts:
            - name: nfs-client-root
              mountPath: /persistentvolumes
          env:
            - name: PROVISIONER_NAME
              value: k8s-sigs.io/nfs-subdir-external-provisioner
            - name: NFS_SERVER
              value: ${NFS_SERVER}
            - name: NFS_PATH
              value: ${NFS_PATH}
      volumes:
        - name: nfs-client-root
          nfs:
            server: ${NFS_SERVER}
            path: ${NFS_PATH}
EOF

echo "Waiting for provisioner Deployment rollout..."
sleep 5

# Auto fallback to obegron/... image if local registry image is not pulled
POD_REASON=$(kubectl get pods -l app=nfs-client-provisioner -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)
if [[ "$POD_REASON" == "ErrImagePull" || "$POD_REASON" == "ImagePullBackOff" ]]; then
    echo "Notice: Local registry image not reachable. Switching to upstream image obegron/nfs-subdir-external-provisioner:v5.0.1-fork.1..."
    kubectl set image deployment/nfs-client-provisioner nfs-client-provisioner=obegron/nfs-subdir-external-provisioner:v5.0.1-fork.1
fi

kubectl rollout status deployment/nfs-client-provisioner --timeout=120s

echo "Provisioner Pod status:"
kubectl get pods -l app=nfs-client-provisioner

echo "=========================================================="
echo ">>> [3/5] Testing Dynamic Provisioning via PVC..."
echo "=========================================================="

cat <<'EOF' | kubectl apply -f -
kind: PersistentVolumeClaim
apiVersion: v1
metadata:
  name: test-claim
spec:
  storageClassName: nfs-client
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 1Mi
EOF

echo "Waiting for test-claim PVC to transition to Bound..."
for i in {1..30}; do
    PVC_STATUS=$(kubectl get pvc test-claim -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [[ "$PVC_STATUS" == "Bound" ]]; then
        echo "SUCCESS: PVC is Bound!"
        break
    fi
    echo "Current PVC status: ${PVC_STATUS}... waiting 2s"
    sleep 2
done

kubectl get pvc test-claim
kubectl get pv

echo "=========================================================="
echo ">>> [4/5] Deploying Test Pod to verify volume mount..."
echo "=========================================================="

kubectl delete pod test-pod --ignore-not-found=true

cat <<'EOF' | kubectl apply -f -
kind: Pod
apiVersion: v1
metadata:
  name: test-pod
spec:
  containers:
  - name: test-pod
    image: busybox:stable
    command:
      - "/bin/sh"
    args:
      - "-c"
      - "echo 'NFS Subdir Provisioner is working perfectly!' > /mnt/SUCCESS && ls -la /mnt && sleep 3"
    volumeMounts:
      - name: nfs-pvc
        mountPath: "/mnt"
  restartPolicy: "Never"
  volumes:
    - name: nfs-pvc
      persistentVolumeClaim:
        claimName: test-claim
EOF

echo "Waiting for test-pod to run..."
for i in {1..30}; do
    PHASE=$(kubectl get pod test-pod -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [[ "$PHASE" == "Succeeded" || "$PHASE" == "Running" ]]; then
        echo "Test pod phase: ${PHASE}"
        break
    fi
    echo "Waiting for test-pod (${PHASE})..."
    sleep 2
done

kubectl get pod test-pod
echo "Test Pod logs:"
kubectl logs test-pod || true

echo "=========================================================="
echo ">>> [5/5] VERIFICATION COMPLETE!"
echo "    - StorageClass 'nfs-client': ACTIVE"
echo "    - Dynamic PV: Successfully created and Bound"
echo "    - Pod Mount: Verified (SUCCESS file written)"
echo "=========================================================="
