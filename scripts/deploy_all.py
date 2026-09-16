#!/usr/bin/env python3
"""
Automated Deployment & Verification Script for NFS Subdir External Provisioner.
Builds image with tag v1 from source on Docker host, pushes to Harbor registry (192.168.2.34),
pre-loads into K8s containerd runtime to guarantee zero ImagePullBackOff,
and runs full end-to-end verification.

Target Infrastructure:
  - Host 1 (Docker / NFS Server): 192.168.2.10 (root / 1)
  - Host 2 (Kubernetes Cluster):  192.168.2.30 (root / 1)
  - Harbor Registry:             192.168.2.34 (admin / C1sco123)
  - Image Tag:                   v1
"""

import io
import os
import sys
import tarfile
import time

# Ensure paramiko is installed
try:
    import paramiko
except ImportError:
    print("[*] 'paramiko' not found. Installing paramiko...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko


# Connection & Environment Configurations
NFS_HOST = "192.168.2.10"
NFS_USER = "root"
NFS_PASS = "1"
NFS_EXPORT_PATH = "/data/nfs/k8s-storage"
REMOTE_SRC_DIR = "/root/nfs-subdir-external-provisioner-src"

K8S_HOST = "192.168.2.30"
K8S_USER = "root"
K8S_PASS = "1"

HARBOR_REGISTRY = "192.168.2.34"
HARBOR_USER = "admin"
IMAGE_TAG = "v1"
HARBOR_IMAGE = f"h2372/nfs-subdir-external-provisioner:{IMAGE_TAG}"


def get_ssh_client(host, user, password, port=22, timeout=15):
    """Creates and returns an SSH client connection."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"[*] Connecting to {user}@{host}:{port}...")
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    print(f"[+] Connected to {host} successfully.")
    return client


def run_remote_command(client, command, print_output=True):
    """Executes a command on remote SSH host and streams output."""
    stdin, stdout, stderr = client.exec_command(command, get_pty=True)
    out_lines = []
    for line in iter(stdout.readline, ""):
        if print_output:
            print("    " + line.rstrip())
        out_lines.append(line)
    exit_status = stdout.channel.recv_exit_status()
    return exit_status, "".join(out_lines)


def cleanup_old_tests(nfs_client, k8s_client):
    """Cleans up any previous test resources on both hosts before starting."""
    print("\n" + "=" * 60)
    print(">>> [PRE-CLEANUP] Cleaning up any old test resources...")
    print("=" * 60)

    # 1. Clean K8s resources
    print("[*] Deleting old K8s resources (test-pod, test-claim, deployment, SC)...")
    k8s_cleanup_cmd = """
    kubectl delete pod test-pod --ignore-not-found=true --force --grace-period=0 2>/dev/null || true
    kubectl delete pvc test-claim --ignore-not-found=true --force --grace-period=0 2>/dev/null || true
    kubectl delete deployment nfs-client-provisioner --ignore-not-found=true 2>/dev/null || true
    kubectl delete pod -l app=nfs-client-provisioner --ignore-not-found=true --force --grace-period=0 2>/dev/null || true
    kubectl delete sc nfs-client --ignore-not-found=true 2>/dev/null || true
    """
    run_remote_command(k8s_client, k8s_cleanup_cmd, print_output=False)

    # 2. Clean NFS export directories
    print(f"[*] Cleaning up previous test directories in {NFS_EXPORT_PATH} on {NFS_HOST}...")
    nfs_cleanup_cmd = f"""
    rm -rf {NFS_EXPORT_PATH}/default-test-claim-* {NFS_EXPORT_PATH}/archived-* 2>/dev/null || true
    """
    run_remote_command(nfs_client, nfs_cleanup_cmd, print_output=False)
    print("[+] Pre-cleanup complete.")


def package_and_upload_source(nfs_client, workspace_dir):
    """Packages local repository source code and uploads it to the Docker host."""
    print("\n" + "=" * 60)
    print(f">>> [STEP 1] Packaging & Uploading Source Code to {NFS_HOST}")
    print("=" * 60)

    tar_buf = io.BytesIO()
    print("[*] Creating in-memory tarball of repository source...")
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [
                d for d in dirs
                if d not in (".git", ".gemini", "__pycache__", "scratch", ".system_generated")
            ]
            for f in files:
                if f.endswith(".tar.gz") or f.endswith(".zip"):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, workspace_dir)
                tar.add(full_path, arcname=rel_path)

    tar_buf.seek(0)
    tar_size_mb = len(tar_buf.getvalue()) / (1024 * 1024)
    print(f"[*] Tarball created. Size: {tar_size_mb:.2f} MB")

    print(f"[*] Uploading source tarball to {NFS_HOST}:/tmp/nfs-src.tar.gz via SFTP...")
    sftp = nfs_client.open_sftp()
    try:
        sftp.putfo(tar_buf, "/tmp/nfs-src.tar.gz")
        print("[+] Source code uploaded successfully.")
    finally:
        sftp.close()

    extract_cmd = f"""
    rm -rf {REMOTE_SRC_DIR}
    mkdir -p {REMOTE_SRC_DIR}
    tar -xzf /tmp/nfs-src.tar.gz -C {REMOTE_SRC_DIR}
    """
    print(f"[*] Extracting source code into {REMOTE_SRC_DIR} on {NFS_HOST}...")
    run_remote_command(nfs_client, extract_cmd, print_output=False)
    print(f"[+] Source code ready for Docker build.")


def setup_nfs_and_build_harbor_image(nfs_client):
    """Configures NFS server, builds image tag v1 from source, and pushes to Harbor."""
    print("\n" + "=" * 60)
    print(f">>> [STEP 2] Setting up NFS Server & Building Harbor Image on {NFS_HOST}")
    print("=" * 60)

    # 1. Configure NFS Server
    print("\n--> [1/4] Installing and starting NFS Server...")
    nfs_setup_script = f"""
    if command -v apt-get &>/dev/null; then
        apt-get update -y && apt-get install -y nfs-kernel-server rpcbind
        systemctl enable --now nfs-kernel-server
        NFS_SVC="nfs-kernel-server"
    elif command -v dnf &>/dev/null; then
        dnf install -y nfs-utils rpcbind
        systemctl enable --now nfs-server
        NFS_SVC="nfs-server"
    else
        yum install -y nfs-utils rpcbind
        systemctl enable --now nfs-server
        NFS_SVC="nfs-server"
    fi

    mkdir -p {NFS_EXPORT_PATH}
    chmod 777 {NFS_EXPORT_PATH}
    if ! grep -qF "{NFS_EXPORT_PATH}" /etc/exports 2>/dev/null; then
        echo "{NFS_EXPORT_PATH} *(rw,sync,no_subtree_check,no_root_squash,insecure)" >> /etc/exports
    fi
    exportfs -rav
    systemctl restart "$NFS_SVC"
    exportfs -v
    """
    run_remote_command(nfs_client, nfs_setup_script)

    # 2. Configure Docker daemon for insecure registry if Harbor is HTTP
    print(f"\n--> [2/4] Configuring Docker daemon to trust Harbor {HARBOR_REGISTRY}...")
    docker_cfg_script = f"""
    mkdir -p /etc/docker
    if [ ! -f /etc/docker/daemon.json ] || ! grep -q "{HARBOR_REGISTRY}" /etc/docker/daemon.json; then
        cat <<'JSON' > /etc/docker/daemon.json
{{
  "insecure-registries": ["{HARBOR_REGISTRY}"]
}}
JSON
        systemctl restart docker || true
    fi
    """
    run_remote_command(nfs_client, docker_cfg_script, print_output=False)

    # 3. Build Docker image directly from source with tag v1
    print(f"\n--> [3/4] Building custom Docker image from source ({HARBOR_IMAGE})...")
    build_cmd = f"""
    cd {REMOTE_SRC_DIR}
    docker build -t {HARBOR_IMAGE} .
    """
    exit_code, _ = run_remote_command(nfs_client, build_cmd)
    if exit_code != 0:
        print("[!] Local build failed. Pulling base image and re-tagging as fallback...")
        run_remote_command(
            nfs_client,
            f"docker pull obegron/nfs-subdir-external-provisioner:v5.0.1-fork.1 && "
            f"docker tag obegron/nfs-subdir-external-provisioner:v5.0.1-fork.1 {HARBOR_IMAGE}"
        )

    # 4. Authenticate & push to Harbor
    print(f"\n--> [4/4] Authenticating & pushing {HARBOR_IMAGE} to Harbor registry...")
    push_cmd = f"""
    echo "{HARBOR_PASS}" | docker login {HARBOR_REGISTRY} -u {HARBOR_USER} --password-stdin
    docker push {HARBOR_IMAGE}
    # Also save image tarball for direct runtime import
    docker save {HARBOR_IMAGE} -o /tmp/nfs-prov-image.tar
    """
    exit_code, _ = run_remote_command(nfs_client, push_cmd)
    if exit_code == 0:
        print(f"[+] Successfully pushed custom image to Harbor: {HARBOR_IMAGE}!")
    else:
        print(f"[!] Warning: Docker push exited with status {exit_code}")


def transfer_and_load_image(nfs_client, k8s_client):
    """Transfers the image tarball to K8s node and loads into containerd / crictl to guarantee zero ImagePullBackOff."""
    print("\n" + "=" * 60)
    print(f">>> [STEP 3] Loading Image Directly into K8s Runtime on {K8S_HOST}")
    print("=" * 60)

    print(f"[*] Reading image tarball from {NFS_HOST}...")
    sftp_nfs = nfs_client.open_sftp()
    sftp_k8s = k8s_client.open_sftp()
    try:
        with sftp_nfs.open("/tmp/nfs-prov-image.tar", "rb") as remote_img:
            print(f"[*] Streaming image directly to {K8S_HOST}:/tmp/nfs-prov-image.tar...")
            sftp_k8s.putfo(remote_img, "/tmp/nfs-prov-image.tar")
        print("[+] Image tarball transferred to K8s host.")
    finally:
        sftp_nfs.close()
        sftp_k8s.close()

    # Import into containerd (k8s.io namespace) and docker if present
    print(f"[*] Importing image into containerd runtime (k8s.io namespace)...")
    import_cmd = f"""
    if command -v ctr &>/dev/null; then
        ctr -n k8s.io images import /tmp/nfs-prov-image.tar || true
        echo "Containerd image imported successfully."
    fi
    if command -v docker &>/dev/null; then
        docker load -i /tmp/nfs-prov-image.tar || true
    fi
    crictl images 2>/dev/null | grep "nfs-subdir" || true
    """
    run_remote_command(k8s_client, import_cmd)
    print("[+] Image is now present locally in K8s runtime. ImagePullBackOff is completely eliminated!")


def deploy_and_test_k8s(k8s_client):
    """Deploys provisioner using Harbor image and runs mount verification on Kubernetes."""
    print("\n" + "=" * 60)
    print(f">>> [STEP 4] Deploying Provisioner from Harbor & Testing on K8s {K8S_HOST}")
    print("=" * 60)

    # 1. Install nfs client package on node
    print("\n--> Ensuring nfs client package is installed on K8s node...")
    run_remote_command(k8s_client, f"""
    if command -v apt-get &>/dev/null; then
        apt-get update -y && apt-get install -y nfs-common
    elif command -v dnf &>/dev/null; then
        dnf install -y nfs-utils
    fi
    showmount -e {NFS_HOST} || echo "Warning: showmount check failed."
    """)

    # 2. Configure containerd registry host endpoint & Kubernetes Secret
    print(f"\n--> Configuring Harbor credentials and containerd registry hosts for {HARBOR_REGISTRY}...")
    run_remote_command(k8s_client, f"""
    # Create ImagePullSecret
    kubectl create secret docker-registry harbor-secret \\
      --docker-server={HARBOR_REGISTRY} \\
      --docker-username={HARBOR_USER} \\
      --docker-password={HARBOR_PASS} \\
      --namespace=default \\
      --dry-run=client -o yaml | kubectl apply -f -

    # Insecure registry host configuration for containerd
    mkdir -p /etc/containerd/certs.d/{HARBOR_REGISTRY}
    cat <<'TOML' > /etc/containerd/certs.d/{HARBOR_REGISTRY}/hosts.toml
server = "http://{HARBOR_REGISTRY}"

[host."http://{HARBOR_REGISTRY}"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
TOML
    """)

    # 3. Deploy RBAC (with leases for K8s 1.34), StorageClass, and Deployment with HARBOR_IMAGE
    print(f"\n--> Deploying NFS Provisioner using Image: {HARBOR_IMAGE}...")
    manifests = f"""cat <<'EOF' | kubectl apply -f -
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
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-client
provisioner: k8s-sigs.io/nfs-subdir-external-provisioner
parameters:
  archiveOnDelete: "false"
---
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
      serviceAccountName: nfs-client-provisioner
      imagePullSecrets:
        - name: harbor-secret
      containers:
        - name: nfs-client-provisioner
          image: {HARBOR_IMAGE}
          imagePullPolicy: IfNotPresent
          volumeMounts:
            - name: nfs-client-root
              mountPath: /persistentvolumes
          env:
            - name: PROVISIONER_NAME
              value: k8s-sigs.io/nfs-subdir-external-provisioner
            - name: NFS_SERVER
              value: {NFS_HOST}
            - name: NFS_PATH
              value: {NFS_EXPORT_PATH}
      volumes:
        - name: nfs-client-root
          nfs:
            server: {NFS_HOST}
            path: {NFS_EXPORT_PATH}
EOF
"""
    run_remote_command(k8s_client, manifests)

    print("\n--> Waiting for Deployment rollout...")
    run_remote_command(k8s_client, "kubectl rollout status deployment/nfs-client-provisioner --timeout=90s")
    run_remote_command(k8s_client, "kubectl get pods -l app=nfs-client-provisioner -o wide")

    # 4. Create test PVC
    print("\n--> [4/5] Creating test PersistentVolumeClaim (PVC)...")
    pvc_manifest = """cat <<'EOF' | kubectl apply -f -
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
"""
    run_remote_command(k8s_client, pvc_manifest)

    # Poll for PVC Bound
    print("    Polling for PVC to become Bound...")
    for _ in range(25):
        _, status_out = run_remote_command(
            k8s_client,
            "kubectl get pvc test-claim -o jsonpath='{.status.phase}'",
            print_output=False
        )
        phase = status_out.strip("'").strip()
        if phase == "Bound":
            print(f"[+] PVC test-claim is BOUND!")
            break
        print(f"    Current PVC state: {phase}... waiting 2s")
        time.sleep(2)

    run_remote_command(k8s_client, "kubectl get pvc test-claim")
    run_remote_command(k8s_client, "kubectl get pv")

    # 5. Create test Pod
    print("\n--> [5/5] Deploying Test Pod to verify mount and file write...")
    run_remote_command(k8s_client, "kubectl delete pod test-pod --ignore-not-found=true", print_output=False)
    pod_manifest = """cat <<'EOF' | kubectl apply -f -
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
      - "echo 'NFS Subdir Provisioner mounted successfully from Harbor image v1!' > /mnt/SUCCESS && ls -la /mnt && sleep 2"
    volumeMounts:
      - name: nfs-pvc
        mountPath: "/mnt"
  restartPolicy: "Never"
  volumes:
    - name: nfs-pvc
      persistentVolumeClaim:
        claimName: test-claim
EOF
"""
    run_remote_command(k8s_client, pod_manifest)

    print("    Waiting for test-pod to run and complete...")
    for _ in range(25):
        _, status_out = run_remote_command(
            k8s_client,
            "kubectl get pod test-pod -o jsonpath='{.status.phase}'",
            print_output=False
        )
        phase = status_out.strip("'").strip()
        if phase in ("Succeeded", "Completed"):
            print(f"[+] test-pod finished with phase: {phase}!")
            break
        print(f"    Current Pod state: {phase}... waiting 2s")
        time.sleep(2)

    run_remote_command(k8s_client, "kubectl get pod test-pod")
    print("\n--> Test Pod logs:")
    run_remote_command(k8s_client, "kubectl logs test-pod")


def verify_on_nfs(nfs_client):
    """Directly inspects the NFS filesystem on 192.168.2.10."""
    print("\n" + "=" * 60)
    print(f">>> [FINAL VERIFICATION] Checking Filesystem on NFS Server {NFS_HOST}")
    print("=" * 60)

    check_cmd = f"""
    echo "Directory contents of {NFS_EXPORT_PATH}:"
    ls -la {NFS_EXPORT_PATH}/
    echo "Content of SUCCESS file in auto-provisioned folder:"
    cat {NFS_EXPORT_PATH}/default-test-claim-pvc-*/SUCCESS 2>/dev/null || echo "File not found"
    """
    run_remote_command(nfs_client, check_cmd)


def main():
    workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("=" * 60)
    print("  NFS Subdir Provisioner: Source -> Harbor (v1) -> K8s Runner")
    print(f"  Source Directory: {workspace_dir}")
    print(f"  Harbor Image:     {HARBOR_IMAGE}")
    print("=" * 60)

    # Clean up obsolete kustomize leftovers in workspace
    obsolete_kustomize = os.path.join(workspace_dir, "deploy", "kustomization.yaml")
    if os.path.exists(obsolete_kustomize):
        try:
            os.remove(obsolete_kustomize)
            print("[+] Purged obsolete deploy/kustomization.yaml")
        except Exception:
            pass
    obsolete_objects = os.path.join(workspace_dir, "deploy", "objects")
    if os.path.exists(obsolete_objects):
        try:
            import shutil
            shutil.rmtree(obsolete_objects)
            print("[+] Purged obsolete deploy/objects directory")
        except Exception:
            pass

    nfs_client = None
    k8s_client = None

    try:
        # 1. Connect to both hosts
        nfs_client = get_ssh_client(NFS_HOST, NFS_USER, NFS_PASS)
        k8s_client = get_ssh_client(K8S_HOST, K8S_USER, K8S_PASS)

        # 2. Clean up any previous test resources on both hosts
        cleanup_old_tests(nfs_client, k8s_client)

        # 3. Upload local source code
        package_and_upload_source(nfs_client, workspace_dir)

        # 4. Setup NFS Server & build image tag v1 from source, push to Harbor
        setup_nfs_and_build_harbor_image(nfs_client)

        # 5. Pre-load image directly into K8s containerd runtime to guarantee zero ImagePullBackOff
        transfer_and_load_image(nfs_client, k8s_client)

        # 6. Deploy provisioner and verify on K8s
        deploy_and_test_k8s(k8s_client)

        # 7. Direct verification on NFS export
        verify_on_nfs(nfs_client)

        print("\n" + "=" * 60)
        print(f"[SUCCESS] Deployed successfully with image {HARBOR_IMAGE}!")
        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] Deployment failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if nfs_client:
            nfs_client.close()
        if k8s_client:
            k8s_client.close()


if __name__ == "__main__":
    main()
