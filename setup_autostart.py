import os
import winreg

vbs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "start_monitor.vbs"))
cmd = f'wscript.exe "{vbs_path}"'

# 1. Registry HKCU Run
try:
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE
    )
    winreg.SetValueEx(key, "OpenCodeLiveMonitor", 0, winreg.REG_SZ, cmd)
    winreg.CloseKey(key)
    print("Registry Run key set successfully:", cmd)
except Exception as e:
    print("Failed to set Registry Run key:", e)

# 2. Startup folder shortcut / script
try:
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    if os.path.exists(startup_dir):
        startup_vbs = os.path.join(startup_dir, "OpenCodeLiveMonitor.vbs")
        with open(startup_vbs, "w", encoding="utf-8") as f:
            f.write('Set WshShell = CreateObject("WScript.Shell")\n')
            f.write(f'WshShell.Run "wscript.exe \"\"{vbs_path}\"\"", 0, False\n')
        print("Startup folder script created successfully:", startup_vbs)
except Exception as e:
    print("Failed to write to Startup folder:", e)
