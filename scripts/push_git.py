#!/usr/bin/env python3
"""
Automates git init, remote setup, and push to GitHub repository:
https://github.com/heroes1412/nfs-subdir-external-provisioner
"""

import os
import shutil
import subprocess
import sys

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REMOTE_URL = "https://github.com/heroes1412/nfs-subdir-external-provisioner.git"


def run_cmd(cmd, check=True):
    print(f"[*] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_DIR, text=True)
    if check and result.returncode != 0:
        print(f"[!] Command failed with return code {result.returncode}")
        return False
    return True


def main():
    print("=" * 60)
    print("  Pushing to GitHub: " + REMOTE_URL)
    print("=" * 60)

    # 1. Clean up obsolete kustomize leftovers
    kust_file = os.path.join(REPO_DIR, "deploy", "kustomization.yaml")
    if os.path.exists(kust_file):
        try:
            os.remove(kust_file)
            print("[+] Purged deploy/kustomization.yaml")
        except Exception:
            pass

    objects_dir = os.path.join(REPO_DIR, "deploy", "objects")
    if os.path.exists(objects_dir):
        try:
            shutil.rmtree(objects_dir)
            print("[+] Purged deploy/objects")
        except Exception:
            pass

    # 2. Check and initialize git
    if not os.path.exists(os.path.join(REPO_DIR, ".git")):
        print("[*] Initializing git repository...")
        run_cmd(["git", "init"])
        run_cmd(["git", "branch", "-M", "main"])
        run_cmd(["git", "remote", "add", "origin", REMOTE_URL])
    else:
        print("[*] Updating origin URL...")
        run_cmd(["git", "remote", "set-url", "origin", REMOTE_URL])
        run_cmd(["git", "branch", "-M", "main"])

    # 3. Untrack vendor if staged previously
    run_cmd(["git", "rm", "-r", "--cached", "vendor"], check=False)

    # 4. Stage & Commit
    print("[*] Staging all files (excluding vendor via .gitignore)...")
    run_cmd(["git", "add", "-A"])

    print("[*] Committing...")
    run_cmd([
        "git", "commit", "-m",
        "feat: optimize for K8s v1.34, Go 1.26, multi-arch support and h2372/nfs-subdir-external-provisioner:v1 image"
    ], check=False)

    # 4. Push
    print("[*] Pushing to main...")
    if not run_cmd(["git", "push", "-u", "origin", "main"], check=False):
        print("[!] Standard push failed. Checking remote branch...")
        run_cmd(["git", "pull", "--rebase", "origin", "main"], check=False)
        if not run_cmd(["git", "push", "-u", "origin", "main"], check=False):
            print("\n[!] Notice: If remote repository already contains initial files, you can force push:")
            print("    git push -u origin main --force")
            return

    print("\n" + "=" * 60)
    print("[SUCCESS] Code pushed to https://github.com/heroes1412/nfs-subdir-external-provisioner")
    print("=" * 60)


if __name__ == "__main__":
    main()
