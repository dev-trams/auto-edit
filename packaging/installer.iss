; Inno Setup script. Usage:  iscc /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B1C1E52-7A0B-4E0C-9C55-AE0D17E0A1F4}
AppName=auto-edit
AppVersion={#AppVersion}
AppPublisher=ctrls-studio
DefaultDirName={autopf}\auto-edit
DefaultGroupName=auto-edit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=auto-edit-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\auto-edit\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\auto-edit"; Filename: "{app}\auto-edit.exe"
Name: "{autodesktop}\auto-edit"; Filename: "{app}\auto-edit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\auto-edit.exe"; Description: "Launch auto-edit"; Flags: nowait postinstall skipifsilent
