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
WizardStyle=modern dark
WizardImageFile=art\wizard_164.bmp,art\wizard_192.bmp,art\wizard_246.bmp,art\wizard_273.bmp,art\wizard_328.bmp,art\wizard_410.bmp
WizardSmallImageFile=art\small_55.bmp,art\small_64.bmp,art\small_69.bmp,art\small_83.bmp,art\small_92.bmp,art\small_110.bmp,art\small_119.bmp,art\small_138.bmp
WizardImageStretch=no
DisableWelcomePage=no
AppPublisherURL=https://github.com/GuyWithPaper/snowrunner-minimap
AppSupportURL=https://github.com/GuyWithPaper/snowrunner-minimap/issues
AppUpdatesURL=https://github.com/GuyWithPaper/snowrunner-minimap/releases
AppCopyright=Map Runner contributors
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Map Runner Setup
VersionInfoProductName=Map Runner
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts"
Name: "autostart"; Description: "Start Map Runner with Windows (it stays in the tray until SnowRunner runs)"; GroupDescription: "Startup"

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
Filename: "{app}\MapRunner.exe"; Description: "Launch Map Runner now"; Flags: nowait postinstall

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM MapRunner.exe"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\MapRunner"


[Messages]
WelcomeLabel1=Welcome to Map Runner
WelcomeLabel2=Map Runner is a live minimap for SnowRunner. It follows your truck and draws each region from your own game files, with fog of war, the game's map markers, waypoints and pins.%n%nIt installs for your Windows account only and needs no administrator rights. It keeps itself up to date automatically.%n%nTip: play SnowRunner in Borderless or Windowed mode so the minimap can appear on top.
FinishedHeadingLabel=Map Runner is ready
FinishedLabelNoIcons=Start SnowRunner and get in a truck. Press Ctrl+Shift+M to show or hide the minimap, Ctrl+Shift+F for the big map, and Ctrl+Shift+O for settings.
FinishedLabel=Start SnowRunner and get in a truck. Press Ctrl+Shift+M to show or hide the minimap, Ctrl+Shift+F for the big map, and Ctrl+Shift+O for settings.
SelectTasksDesc=Choose a few options before installing.
ReadyLabel1=Map Runner is ready to install.
