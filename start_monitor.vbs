Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir

' Kiem tra neu app.py da dang chay thi khong mo them instance moi
Set objWMIService = GetObject("winmgmts:\\.\root\cimv2")
Set colItems = objWMIService.ExecQuery("Select * from Win32_Process Where (Name = 'python.exe' Or Name = 'pythonw.exe') And CommandLine Like '%app.py%'")

If colItems.Count > 0 Then
    ' Da chay roi, kich hoat cua so len tren cung
    WshShell.AppActivate "OpenCodeLiveMonitor"
Else
    ' Chua chay, bat dau chay ngam khong hien cua so console
    WshShell.Run "pythonw app.py", 0, False
End If

