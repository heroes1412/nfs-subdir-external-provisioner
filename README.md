# Kubernetes NFS Subdir External Provisioner

**NFS Subdir External Provisioner** is an automatic provisioner that uses an *existing and already configured* NFS server to support dynamic provisioning of Kubernetes Persistent Volumes via Persistent Volume Claims (PVC).

When a PVC requests this StorageClass, a subdirectory is automatically created on your NFS server using the following pattern:
```
${namespace}-${pvcName}-${pvName}
```

### Key Highlights & Kubernetes v1.34 Compatibility
- **Full Kubernetes v1.34+ Compatibility**: Updated RBAC with **`coordination.k8s.io/leases`** leader election permissions (avoids leader election crash loops in modern Kubernetes).
- **Cleaned Up Legacy APIs**: Removed deprecated `PodSecurityPolicy` manifests and permissions that fail on modern Kubernetes clusters.
- **Modern Helm 3 Ready**: Chart upgraded to **`apiVersion: v2`**.
- **Modern Go Toolchain**: Built with **Go 1.26**.
- **No Kustomize Overhead**: Completely removed Kustomize dependencies for direct, straightforward native YAML and Helm deployments.

---

## 1. Prerequisites

### A. NFS Server Setup
You must have a functioning NFS server. If you need to quickly deploy one:

- **On Ubuntu / Debian:**
  ```bash
  sudo apt-get update && sudo apt-get install -y nfs-kernel-server rpcbind
  sudo mkdir -p /data/nfs/k8s-storage
  sudo chmod 777 /data/nfs/k8s-storage
  echo "/data/nfs/k8s-storage *(rw,sync,no_subtree_check,no_root_squash,insecure)" | sudo tee -a /etc/exports
  sudo exportfs -rav
  sudo systemctl enable --now nfs-kernel-server
  sudo systemctl restart nfs-kernel-server
  ```

- **On RHEL / CentOS / Rocky / AlmaLinux:**
  ```bash
  sudo dnf install -y nfs-utils rpcbind
  sudo mkdir -p /data/nfs/k8s-storage
  sudo chmod 777 /data/nfs/k8s-storage
  echo "/data/nfs/k8s-storage *(rw,sync,no_subtree_check,no_root_squash,insecure)" | sudo tee -a /etc/exports
  sudo exportfs -rav
  sudo systemctl enable --now nfs-server
  sudo systemctl restart nfs-server
  ```

### B. NFS Client on Kubernetes Worker Nodes
> **IMPORTANT:** Kubelet requires NFS client utilities on **all Kubernetes worker nodes** to mount NFS volumes into Pods.

- **Ubuntu / Debian worker nodes:**
  ```bash
  sudo apt-get update && sudo apt-get install -y nfs-common
  ```
- **RHEL / CentOS / Rocky worker nodes:**
  ```bash
  sudo dnf install -y nfs-utils
  ```

---

## 2. Installation Methods

Choose either **Method 1 (Native Manifests)** or **Method 2 (Helm Chart)**.

---

### Method 1: Native Manual Installation (`kubectl apply`)

Deploy standard Kubernetes manifests directly from the `deploy/` directory.

#### Step 1: Configure NFS Server Details
Open `deploy/deployment.yaml` and configure your NFS Server IP and share path:

```yaml
        env:
          - name: PROVISIONER_NAME
            value: k8s-sigs.io/nfs-subdir-external-provisioner
          - name: NFS_SERVER
            value: 192.168.2.10          # Your NFS Server IP
          - name: NFS_PATH
            value: /data/nfs/k8s-storage # Your NFS export path
      volumes:
        - name: nfs-client-root
          nfs:
            server: 192.168.2.10        # Your NFS Server IP
            path: /data/nfs/k8s-storage # Your NFS export path
```

*(Note: If deploying into a namespace other than `default`, update the `namespace:` field in `deploy/rbac.yaml` and `deploy/deployment.yaml`)*.

#### Step 2: Apply RBAC Permissions
Grant the provisioner permissions to manage PVs, PVCs, and leader election leases:
```bash
kubectl apply -f deploy/rbac.yaml
```

#### Step 3: Create StorageClass
Create the `nfs-client` StorageClass:
```bash
kubectl apply -f deploy/class.yaml
```

*(Optional: To make `nfs-client` the default StorageClass, add the annotation: `storageclass.kubernetes.io/is-default-class: "true"` to `deploy/class.yaml`)*.

#### Step 4: Deploy the Provisioner
```bash
kubectl apply -f deploy/deployment.yaml
```

#### Step 5: Verify Deployment
```bash
kubectl get pods -l app=nfs-client-provisioner
kubectl logs -l app=nfs-client-provisioner -f
```
Verify that the pod is `Running` and has acquired the leader election lease.

---

### Method 2: Helm Chart Installation (Recommended)

Deploy and manage the provisioner with a single command using Helm 3.

#### Quick Install with CLI Flags:
Run from the root of this repository:

```bash
helm install nfs-provisioner ./charts/nfs-subdir-external-provisioner \
  --namespace kube-system \
  --create-namespace \
  --set nfs.server=192.168.2.10 \
  --set nfs.path=/data/nfs/k8s-storage \
  --set storageClass.name=nfs-client \
  --set storageClass.defaultClass=true
```

#### Or Install via `values.yaml`:
1. Edit `charts/nfs-subdir-external-provisioner/values.yaml`:
   ```yaml
   nfs:
     server: 192.168.2.10
     path: /data/nfs/k8s-storage

   storageClass:
     create: true
     name: nfs-client
     defaultClass: true
   ```
2. Deploy the chart:
   ```bash
   helm install nfs-provisioner ./charts/nfs-subdir-external-provisioner \
     -f ./charts/nfs-subdir-external-provisioner/values.yaml \
     -n kube-system \
     --create-namespace
   ```

---

## 3. Testing & Verification

Verify dynamic volume provisioning and Pod mounting end-to-end:

### Step 1: Create a PersistentVolumeClaim
Apply the test claim:
```bash
kubectl apply -f deploy/test-claim.yaml
```

Verify the claim status:
```bash
kubectl get pvc test-claim
```
**Expected Result:** The `STATUS` column changes to **`Bound`**, indicating the PV was dynamically provisioned and bound.

### Step 2: Create a Test Pod
Apply the test pod manifest:
```bash
kubectl apply -f deploy/test-pod.yaml
```

Verify Pod execution:
```bash
kubectl get pod test-pod
```
The pod will mount the PVC, write a `SUCCESS` file to `/mnt/SUCCESS`, and terminate with status **`Completed`**.

### Step 3: Check Files on the NFS Server
On your NFS Server (`192.168.2.10`), verify that the directory and file were created:
```bash
ls -la /data/nfs/k8s-storage/
# You will see the subdirectory: default-test-claim-pvc-.../
cat /data/nfs/k8s-storage/default-test-claim-pvc-*/SUCCESS
```

### Step 4: Clean Up Test Resources
```bash
kubectl delete -f deploy/test-pod.yaml
kubectl delete -f deploy/test-claim.yaml
```
Deleting the PVC automatically triggers the provisioner to clean up or archive the directory on the NFS server.

---

## 4. StorageClass & Provisioner Configuration

### StorageClass Parameters & Options

You can customize StorageClass settings in `deploy/class.yaml` or Helm `values.yaml`:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-client
provisioner: k8s-sigs.io/nfs-subdir-external-provisioner
reclaimPolicy: Delete          # Default: Delete (PV deleted when PVC is deleted)
parameters:
  archiveOnDelete: "false"     # Set "false" to purge data, "true" to rename to archived-*
  onDelete: "delete"           # Explicit delete behavior (overrides archiveOnDelete)
mountOptions:                  # Optional: passed directly to client Pod NFS mounts
  - nfsvers=4.2                # NFS version (4.2, 4.1, 3); omittable to auto-negotiate
  - noatime                    # Performance boost: disables file access time updates
  - hard                       # Prevents silent I/O failure during transient network drops
  - timeo=600                  # Timeout in deciseconds (60 seconds)
  - retrans=2                  # Retry count
```

| Parameter / Field | Default | Description |
| :--- | :--- | :--- |
| `reclaimPolicy` | `Delete` | `Delete` deletes the PV and its underlying NFS folder upon PVC deletion. Set `Retain` if you want K8s PV retained. |
| `archiveOnDelete` | `"false"` | If `"true"`, deleting PVC renames directory to `archived-<dir>` on NFS. If `"false"`, directory is permanently deleted. |
| `onDelete` | `"delete"` | Explicit action on volume deletion (`delete` or `retain`). Overrides `archiveOnDelete`. |
| `mountOptions` | *(unset)* | NFS mount options passed to worker node Kubelet. If omitted, Linux kernel automatically negotiates highest supported version (NFSv4.2/4.1). |
| `pathPattern` | *(unset)* | Dynamic directory naming on NFS using PVC metadata, e.g., `${.PVC.namespace}/${.PVC.name}`. |

### Note on PVC Storage Requests (`storage: 1Mi`)
> [!NOTE]
> Standard Linux NFS servers do **not** enforce per-directory disk quotas. The `resources.requests.storage: 1Mi` inside your PVC manifest is a **formal requirement mandated by Kubernetes OpenAPI schema validation**. It does not physically restrict Pod write capacity. You can use `1Mi` or `1Gi` as a symbolic placeholder. To allow developers to omit `storage` entirely, deploy a `LimitRange` with a default storage request in the target namespace.

### Pod Resource Limits
The provisioner is an extremely lightweight control-plane Go process (it only watches K8s API and runs `mkdir/rm`). Standard production limits configured in the manifests and Helm chart:
```yaml
resources:
  requests:
    cpu: 10m
    memory: 32Mi
  limits:
    cpu: 100m
    memory: 128Mi
```

---

## 5. Building the Container Image & Pushing to Registry

### Option A: Standard Build (Single Architecture, Local Host)
Uses [Dockerfile.standard](file:///c:/Users/Administrator/Desktop/nfs-subdir-external-provisioner-master/Dockerfile.standard). It does not require copying the `vendor/` directory; it fetches dependencies cleanly via `go mod download`.

```bash
# 1. Authenticate to Docker Registry (Docker Hub or Harbor)
docker login -u h2372

# 2. Build standard image
docker build -f Dockerfile.standard -t h2372/nfs-subdir-external-provisioner:v1 .

# 3. Push image
docker push h2372/nfs-subdir-external-provisioner:v1
```

### Option B: Multi-Architecture Build (amd64 + arm64)
Uses [Dockerfile](file:///c:/Users/Administrator/Desktop/nfs-subdir-external-provisioner-master/Dockerfile) with Docker Buildx to cross-compile for both x86_64 and ARM64 clusters:

```bash
# 1. Setup Buildx builder (if not already initialized)
docker buildx create --name multi-builder --use || docker buildx use multi-builder
docker buildx inspect --bootstrap

# 2. Build and push multi-arch manifest list directly
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t h2372/nfs-subdir-external-provisioner:v1 \
  --push .
```

---

## 6. Multi-NFS Architecture: Multiple Servers or Multiple Export Paths

A common real-world requirement is having:
- **Scenario A**: Two or more independent NFS servers (e.g., `192.168.2.10` for fast SSD app storage and `192.168.2.20` for HDD backup storage).
- **Scenario B**: One NFS server with multiple export shares (e.g., `192.168.2.10:/data/nfs/apps` and `192.168.2.10:/data/nfs/database`).

### The Golden Rule
> Each provisioner Deployment manages **one NFS mount** (`NFS_SERVER` + `NFS_PATH`) and pairs with a unique **`PROVISIONER_NAME`**.
> To support multiple NFS servers or paths, deploy **multiple provisioner Deployments**, each associated with its own `StorageClass`. They can share the same RBAC `ServiceAccount` and `ClusterRole`!

---

### Option 1: Native YAML Deployment

#### 1. RBAC (Deploy Once)
Deploy `deploy/rbac.yaml` once. Both provisioner instances can share the ServiceAccount `nfs-client-provisioner`.

#### 2. Instance 1: High-Performance / App Storage (`nfs-apps`)
**StorageClass:**
```yaml
# deploy/class-apps.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-apps
provisioner: k8s-sigs.io/nfs-apps # Must match PROVISIONER_NAME in deployment-apps
parameters:
  archiveOnDelete: "false"
```

**Deployment:**
```yaml
# deploy/deployment-apps.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nfs-provisioner-apps
  namespace: default
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: nfs-provisioner-apps
  template:
    metadata:
      labels:
        app: nfs-provisioner-apps
    spec:
      serviceAccountName: nfs-client-provisioner
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
              value: k8s-sigs.io/nfs-apps
            - name: NFS_SERVER
              value: 192.168.2.10
            - name: NFS_PATH
              value: /data/nfs/apps
      volumes:
        - name: nfs-client-root
          nfs:
            server: 192.168.2.10
            path: /data/nfs/apps
```

#### 3. Instance 2: Database / Secondary Storage (`nfs-db`)
**StorageClass:**
```yaml
# deploy/class-db.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-db
provisioner: k8s-sigs.io/nfs-db # Must match PROVISIONER_NAME in deployment-db
parameters:
  archiveOnDelete: "true" # Keep archived directory on delete for safety
```

**Deployment:**
*(For a 2nd server use `server: 192.168.2.20`; for a 2nd path on the same server use `path: /data/nfs/database`)*:
```yaml
# deploy/deployment-db.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nfs-provisioner-db
  namespace: default
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: nfs-provisioner-db
  template:
    metadata:
      labels:
        app: nfs-provisioner-db
    spec:
      serviceAccountName: nfs-client-provisioner
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
              value: k8s-sigs.io/nfs-db
            - name: NFS_SERVER
              value: 192.168.2.10          # Or 192.168.2.20 for a 2nd server
            - name: NFS_PATH
              value: /data/nfs/database     # 2nd export path
      volumes:
        - name: nfs-client-root
          nfs:
            server: 192.168.2.10          # Or 192.168.2.20
            path: /data/nfs/database
```

#### 4. How PVCs Select Storage
Applications simply choose the appropriate `storageClassName`:
```yaml
# PVC for general application
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: app-pvc
spec:
  storageClassName: nfs-apps
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
---
# PVC for database
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: database-pvc
spec:
  storageClassName: nfs-db
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 20Gi
```

---

### Option 2: Helm 3 Multi-Instance Deployment

With Helm, managing multiple NFS targets requires just two separate release names:

```bash
# 1. Deploy Instance 1 (Apps)
helm install nfs-apps ./charts/nfs-subdir-external-provisioner \
  --namespace kube-system \
  --set nfs.server=192.168.2.10 \
  --set nfs.path=/data/nfs/apps \
  --set storageClass.name=nfs-apps \
  --set storageClass.provisionerName=k8s-sigs.io/nfs-apps

# 2. Deploy Instance 2 (Database or 2nd Server)
helm install nfs-db ./charts/nfs-subdir-external-provisioner \
  --namespace kube-system \
  --set nfs.server=192.168.2.20 \
  --set nfs.path=/data/nfs/database \
  --set storageClass.name=nfs-db \
  --set storageClass.provisionerName=k8s-sigs.io/nfs-db
```
*(Both instances operate concurrently in the same cluster without interference)*.


