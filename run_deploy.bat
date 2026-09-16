@echo off
REM Launcher for deploy_all.py
echo ==========================================================
echo Running NFS Provisioner automated deployment script...
echo ==========================================================
REM Clean up obsolete kustomize leftovers
if exist "%~dp0deploy\kustomization.yaml" del /f /q "%~dp0deploy\kustomization.yaml"
if exist "%~dp0deploy\objects" rd /s /q "%~dp0deploy\objects"

python "%~dp0scripts\deploy_all.py"
pause
