#define MyAppName "DCWatch"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "DCWatch contributors"
#define MyAppExeName "DCWatch.exe"

[Setup]
AppId={{35B63D61-79B6-41D1-BDE5-DCWATCH0001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=..\..\release
OutputBaseFilename=DCWatch-{#MyAppVersion}-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\DCWatch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DCWatch"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\DCWatch"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Run DCWatch"; Flags: nowait postinstall skipifsilent
