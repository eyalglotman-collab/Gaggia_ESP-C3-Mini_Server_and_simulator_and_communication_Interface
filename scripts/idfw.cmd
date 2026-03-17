@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

set "IDF_PATH=C:\Espressif\.espressif\v5.5.2\esp-idf"
set "IDF_TOOLS_PATH=C:\Espressif"
set "IDF_PYTHON_ENV_PATH=C:\Espressif\python_env\idf5.5_py3.11_env"
set "PYTHON_EXE=%IDF_PYTHON_ENV_PATH%\Scripts\python.exe"
set "FIRMWARE_ROOT=%REPO_ROOT%\firmware\esp32c3_bridge"
set "BUILD_DIR=%REPO_ROOT%\.idfbuild\esp32c3_bridge"
set "CACHE_DIR=%REPO_ROOT%\.cache"

if "%ESPPORT%"=="" set "ESPPORT=COM4"

if not exist "%PYTHON_EXE%" (
    echo ESP-IDF Python not found at "%PYTHON_EXE%"
    exit /b 1
)

if not exist "%IDF_PATH%\tools\idf.py" (
    echo ESP-IDF not found at "%IDF_PATH%"
    exit /b 1
)

if not exist "%FIRMWARE_ROOT%" (
    echo Firmware root not found at "%FIRMWARE_ROOT%"
    exit /b 1
)

if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"
if not exist "%CACHE_DIR%\Espressif\ComponentManager" mkdir "%CACHE_DIR%\Espressif\ComponentManager"
if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

set "XDG_CACHE_HOME=%CACHE_DIR%"
set "PYTHONNOUSERSITE=True"
set "PYTHONPATH="
set "PYTHONHOME="

set "PATH=%IDF_PYTHON_ENV_PATH%\Scripts;C:\Program Files\Git\cmd;C:\Program Files\Git\mingw64\bin;C:\Program Files\Git\usr\bin;C:\Espressif\tools\cmake\3.30.2\bin;C:\Espressif\tools\ninja\1.12.1;C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20251107\xtensa-esp-elf\bin;C:\Espressif\tools\riscv32-esp-elf\esp-14.2.0_20251107\riscv32-esp-elf\bin;C:\Espressif;%PATH%"

"%PYTHON_EXE%" "%IDF_PATH%\tools\idf.py" -C "%FIRMWARE_ROOT%" -B "%BUILD_DIR%" -DIDF_TARGET=esp32c3 %*
exit /b %ERRORLEVEL%
