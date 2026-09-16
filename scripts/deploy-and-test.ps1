# ==============================================================================
# PowerShell Helper Script: Deploy NFS & Test on Kubernetes
# ==============================================================================

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ">>> QUICK 2-STEP EXECUTION GUIDE FOR YOUR SERVERS <<<" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan

Write-Host @"
[STEP 1] Setup NFS Server on host 192.168.2.10:
  Connect via SSH:
    ssh root@192.168.2.10
    (Password: 1)

  Then copy & paste the following command block:
"@ -ForegroundColor Yellow

$nfsScript = Get-Content -Raw -Path "$PSScriptRoot\setup-nfs-server.sh"
Write-Host @"
-------------------- PASTE INTO 192.168.2.10 TERMINAL --------------------
bash -c '$($nfsScript -replace "'", "'\''")'
--------------------------------------------------------------------------
"@ -ForegroundColor Gray

Write-Host @"
[STEP 2] Deploy Provisioner & Test Pod Mount on K8s 192.168.2.30:
  Connect via SSH:
    ssh root@192.168.2.30
    (Password: 1)

  Then copy & paste the following command block:
"@ -ForegroundColor Yellow

$k8sScript = Get-Content -Raw -Path "$PSScriptRoot\setup-k8s-and-test.sh"
Write-Host @"
-------------------- PASTE INTO 192.168.2.30 TERMINAL --------------------
bash -c '$($k8sScript -replace "'", "'\''")'
--------------------------------------------------------------------------
"@ -ForegroundColor Gray
