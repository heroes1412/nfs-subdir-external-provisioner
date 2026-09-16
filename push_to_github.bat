@echo off
setlocal enabledelayedexpansion

echo ======================================================================
echo    Pushing NFS Subdir Provisioner to GitHub
echo    Target: https://github.com/heroes1412/nfs-subdir-external-provisioner
echo ======================================================================

cd /d "%~dp0"

REM 1. Clean up any obsolete kustomize leftovers before committing
if exist "deploy\kustomization.yaml" del /f /q "deploy\kustomization.yaml"
if exist "deploy\objects" rd /s /q "deploy\objects"

REM 2. Initialize git repository if not already initialized
if not exist ".git" (
    echo [*] Initializing new git repository...
    git init
    git branch -M main
    git remote add origin https://github.com/heroes1412/nfs-subdir-external-provisioner.git
) else (
    echo [*] Updating remote origin URL...
    git remote set-url origin https://github.com/heroes1412/nfs-subdir-external-provisioner.git
    git branch -M main
)

REM 3. Untrack vendor if previously staged
git rm -r --cached vendor 2>nul

REM 4. Stage all files (vendor is now excluded via .gitignore)
echo [*] Staging files (excluding vendor)...
git add -A

REM 5. Commit changes
echo [*] Committing changes...
git commit -m "feat: optimize for K8s v1.34, Go 1.26, multi-arch support and h2372/nfs-subdir-external-provisioner:v1 image"

REM 5. Push to GitHub
echo [*] Pushing to GitHub main branch...
git push -u origin main

if errorlevel 1 (
    echo.
    echo [!] Standard push failed. Trying with force push (or pull rebase)...
    git pull --rebase origin main
    git push -u origin main
)

if errorlevel 1 (
    echo.
    echo [!] If remote repository has existing commits and you want to replace with this repo:
    echo     Run: git push -u origin main --force
) else (
    echo.
    echo ======================================================================
    echo [SUCCESS] Successfully pushed to https://github.com/heroes1412/nfs-subdir-external-provisioner!
    echo ======================================================================
)

pause
