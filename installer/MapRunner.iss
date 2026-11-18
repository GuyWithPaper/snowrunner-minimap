; Inno Setup script. Built by release.ps1, which passes /DAppVersion=x.y.z
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{8C5B9F3E-6A1D-4E2B-9C47-2F1E5D7A3B90}
AppName=Map Runner
AppVersion={#AppVersion}
AppPublisher=Map Runner
DefaultDirName={localappdata}\Programs\MapRunner
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MapRunner-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MapRunner.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start the minimap automatically with Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\build\pyi\dist\MapRunner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{localappdata}\Programs\SnowRunnerMinimap"
Type: files; Name: "{userprograms}\SnowRunner Minimap.lnk"
Type: files; Name: "{userdesktop}\SnowRunner Minimap.lnk"
Type: files; Name: "{userstartup}\SnowRunner Minimap.lnk"

[Icons]
Name: "{userprograms}\Map Runner"; Filename: "{app}\MapRunner.exe"
Name: "{userdesktop}\Map Runner"; Filename: "{app}\MapRunner.exe"; Tasks: desktopicon
Name: "{userstartup}\Map Runner"; Filename: "{app}\MapRunner.exe"; Tasks: autostart

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var R: Integer;
begin
  if CurStep = ssInstall then
    Exec('taskkill', '/F /IM SnowRunnerMinimap.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
end;

[Run]
Filename: "{app}\MapRunner.exe"; Description: "Start Map Runner"; Flags: nowait postinstall

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM MapRunner.exe"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\MapRunner"

