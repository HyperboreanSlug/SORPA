' Double-click this file to start the GUI with no console window.
' Uses run_gui.bat (deps + pythonw). Errors: MessageBox / gui_error.log
Option Explicit
Dim sh, fso, dir, bat, rc, logPath
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
bat = dir & "\run_gui.bat"
logPath = dir & "\gui_error.log"
If Not fso.FileExists(bat) Then
  MsgBox "run_gui.bat not found in:" & vbCrLf & dir, vbCritical, "SOR Public Archiver"
  WScript.Quit 1
End If
' 0 = hide console; bat launches pythonw and exits quickly
rc = sh.Run("cmd /c """ & bat & """", 0, True)
If rc <> 0 Then
  Dim extra
  extra = ""
  If fso.FileExists(logPath) Then
    extra = vbCrLf & vbCrLf & "See gui_error.log in:" & vbCrLf & dir
  End If
  MsgBox "Could not start the GUI (exit code " & rc & ")." & extra, _
    vbExclamation, "SOR Public Archiver"
  WScript.Quit rc
End If
