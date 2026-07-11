' Double-click this file to start the GUI with no console window.
' Uses run_gui.bat (core deps + pythonw). DeepFace installs in the background
' after the GUI starts (see gui.py) so this launcher is not blocked.
' Errors: MessageBox / gui_error.log in this folder.
Option Explicit
Dim sh, fso, dir, bat, rc, logPath, ts
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
bat = dir & "\run_gui.bat"
logPath = dir & "\gui_error.log"

If Not fso.FileExists(bat) Then
  MsgBox "run_gui.bat not found in:" & vbCrLf & dir, vbCritical, "SOR Public Archiver"
  WScript.Quit 1
End If

' Working directory = project root (double-click often uses System32 otherwise)
sh.CurrentDirectory = dir

' 0 = hide console; True = wait for bat (bat must exit quickly after start)
On Error Resume Next
rc = sh.Run("cmd /c call """ & bat & """", 0, True)
If Err.Number <> 0 Then
  MsgBox "Failed to run launcher:" & vbCrLf & Err.Description & vbCrLf & bat, _
    vbCritical, "SOR Public Archiver"
  WScript.Quit 1
End If
On Error GoTo 0

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
