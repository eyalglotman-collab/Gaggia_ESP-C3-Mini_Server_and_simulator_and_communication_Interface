Option Explicit

Dim shell, fso, scriptDir, ps1Path, port, buildFirstArg, cmd, rc
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path = fso.BuildPath(scriptDir, "flash_hidden.ps1")

port = "COM4"
buildFirstArg = ""

If WScript.Arguments.Count >= 1 Then
    port = WScript.Arguments(0)
End If

If WScript.Arguments.Count >= 2 Then
    If LCase(WScript.Arguments(1)) = "buildfirst" Or LCase(WScript.Arguments(1)) = "-buildfirst" Then
        buildFirstArg = " -BuildFirst"
    End If
End If

cmd = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1Path & """ -Port """ & port & """" & buildFirstArg
rc = shell.Run(cmd, 0, True)
WScript.Quit rc
