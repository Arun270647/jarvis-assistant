; Jarvis's Windows installer, compiled by packaging/build_windows.py with Inno Setup 6 (ISCC), which passes the
; values below with /D. It installs the folder the build prepared (Jarvis.exe, runtime\, app\) for the current user
; only, in %LOCALAPPDATA%\Programs\Jarvis: no administrator rights, a Start Menu shortcut, a Desktop one if asked,
; and the entry in Settings > Apps. Then it opens Jarvis.
;
; Jarvis uninstalls itself (Settings > About > Uninstall, or Uninstall in Settings > Apps, which runs
; "Jarvis.exe --uninstall"), so the installer adds no uninstaller of its own; src/app/install.py and uninstall.py
; use the same shortcut names and the same Apps entry. The in-app updater runs a newer installer silently with
; /VERYSILENT /DIR=<this folder>: [InstallDelete] clears the old app\ and runtime\ first, so no file of the previous
; version lingers. Settings, memories and models live in the user's data folders and are never touched.
;
; Nothing is signed (no signing budget): Windows SmartScreen asks once, "More info" > "Run anyway".

#ifndef AppVersion
  #error Build with packaging/build_windows.py, which passes AppVersion, AppNumbers, SourceDir, OutputDir, OutputName, IconFile and SizeKB.
#endif

[Setup]
AppId=Jarvis.Assistant.Desktop
AppName=Jarvis
AppVersion={#AppVersion}
AppVerName=Jarvis {#AppVersion}
AppPublisher=The Jarvis authors
AppPublisherURL=https://jarvis-desktop-assistant.vercel.app
AppSupportURL=https://github.com/Arun270647/jarvis-assistant/issues
VersionInfoVersion={#AppNumbers}
VersionInfoProductName=Jarvis
VersionInfoDescription=Jarvis setup
VersionInfoCopyright=Apache License 2.0
DefaultDirName={userpf}\Jarvis
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
Uninstallable=no
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
SetupIconFile={#IconFile}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Jarvis"; Filename: "{app}\Jarvis.exe"; WorkingDir: "{app}"; Comment: "Jarvis voice assistant"
Name: "{userdesktop}\Jarvis"; Filename: "{app}\Jarvis.exe"; WorkingDir: "{app}"; Comment: "Jarvis voice assistant"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: string; ValueName: "DisplayName"; ValueData: "Jarvis"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: string; ValueName: "DisplayVersion"; ValueData: "{#AppVersion}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: string; ValueName: "Publisher"; ValueData: "The Jarvis authors"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: string; ValueName: "DisplayIcon"; ValueData: "{app}\Jarvis.exe,0"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: string; ValueName: "UninstallString"; ValueData: """{app}\Jarvis.exe"" --uninstall"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: dword; ValueName: "NoModify"; ValueData: 1
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: dword; ValueName: "NoRepair"; ValueData: 1
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"; ValueType: dword; ValueName: "EstimatedSize"; ValueData: {#SizeKB}

[Run]
Filename: "{app}\Jarvis.exe"; Description: "Open Jarvis"; Flags: nowait postinstall skipifsilent
