; Inno Setup script. Built by release.ps1, which passes /DAppVersion=x.y.z
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{8C5B9F3E-6A1D-4E2B-9C47-2F1E5D7A3B90}
AppName=SnowRunner Minimap
AppVersion={#AppVersion}
AppPublisher=SnowRunner Minimap
DefaultDirName={localappdata}\Programs\SnowRunnerMinimap
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SnowRunnerMinimap-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\SnowRunnerMinimap.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start the minimap automatically with Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\build\pyi\dist\SnowRunnerMinimap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\SnowRunner Minimap"; Filename: "{app}\SnowRunnerMinimap.exe"
Name: "{userdesktop}\SnowRunner Minimap"; Filename: "{app}\SnowRunnerMinimap.exe"; Tasks: desktopicon
Name: "{userstartup}\SnowRunner Minimap"; Filename: "{app}\SnowRunnerMinimap.exe"; Tasks: autostart

[Run]
Filename: "{app}\SnowRunnerMinimap.exe"; Description: "Start SnowRunner Minimap"; Flags: nowait postinstall

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM SnowRunnerMinimap.exe"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\SnowRunnerMinimap"
