@echo off
rem Win11Forge (Linux-built Windows To Go): one-time boot fix-up.
rem Runs from a tiny Windows PE on the drive's first boot, writes the real Windows boot files
rem with Microsoft's bcdboot, removes itself and reboots straight into Windows.
title Win11Forge - preparing Windows To Go
echo.
echo   Win11Forge: finishing boot setup, the PC will restart by itself...
echo.
set W=
set S=
for %%d in (C D E F G H I J K L M N O P Q R S T U V W Y Z) do (
    if exist %%d:\forge-windows.tag set W=%%d:
    if exist %%d:\forge-esp.tag set S=%%d:
)
if "%W%"=="" goto fail
if "%S%"=="" goto fail
echo   Windows partition: %W%   boot partition: %S%
del /a /f /q %S%\EFI\Microsoft\Boot\BCD >nul 2>&1
del /a /f /q %S%\boot\BCD >nul 2>&1
bcdboot %W%\Windows /s %S% /f UEFI
if errorlevel 1 goto fail
del /f /q %S%\sources\boot.wim >nul 2>&1
rd /s /q %S%\sources >nul 2>&1
del /f /q %S%\forge-esp.tag >nul 2>&1
del /f /q %W%\forge-windows.tag >nul 2>&1
wpeutil reboot
exit /b 0
:fail
echo.
echo   Could not find the Win11Forge partitions (W=%W% S=%S%).
echo   Run:  bcdboot X:\Windows /s Y: /f UEFI   by hand, then: wpeutil reboot
cmd /k
