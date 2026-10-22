; PyPackHub - Windows installer recipe (Inno Setup 6+).
;
; The release workflow runs this on the Windows runner after PyInstaller has
; produced dist/PyPackHub, and names the result after the version:
;
;   iscc /DAppVersion=0.1.1 packaging\windows\installer.iss
;   -> release\PyPackHub-0.1.1-windows-x86_64-setup.exe
;
; Two deliberate choices:
;
; * PrivilegesRequired=lowest - the installer never asks for elevation, so the
;   installed application never has privileges the user did not already have.
;   PyPackHub manages other environments through their own interpreters and
;   refuses to touch a system Python (SPEC section 11); an installer that ran
;   as Administrator would contradict that. In no mode does this installer
;   modify the registry beyond its own Add/Remove Programs entry, or touch
;   anything outside its install directory.
; * Nothing is written into {app} at run time - settings, logs and snapshots go
;   to the per-user application data directory through platformdirs - so
;   uninstalling removes the program completely and leaves the user's data
;   alone.

; All three paths are overridable from the command line so the workflow can pass
; absolute paths. The defaults are relative to this script, which is what Inno
; treats as the base for Source, OutputDir and SetupIconFile, but an absolute
; path removes the question entirely and makes a mis-invocation obvious.
#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif

#ifndef BundleDir
  #define BundleDir "..\..\dist\PyPackHub"
#endif

#ifndef OutputDirectory
  #define OutputDirectory "..\..\release"
#endif

#ifndef IconFile
  #define IconFile "..\..\packaging\icon.ico"
#endif

[Setup]
AppId={{9C4B7E21-5E3A-4F1B-9D6E-2A8F0C7B31D4}
AppName=PyPackHub
AppVersion={#AppVersion}
AppPublisher=PyPackHub contributors
AppPublisherURL=https://github.com/MY-Jafari/PyPackHub
AppSupportURL=https://github.com/MY-Jafari/PyPackHub/issues
AppUpdatesURL=https://github.com/MY-Jafari/PyPackHub/releases
VersionInfoDescription=PyPackHub package manager
DefaultDirName={autopf}\PyPackHub
DefaultGroupName=PyPackHub
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
OutputDir={#OutputDirectory}
OutputBaseFilename=PyPackHub-{#AppVersion}-windows-x86_64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\PyPackHub.exe
UninstallDisplayName=PyPackHub
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
DisableDirPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; The whole PyInstaller directory, so the layout on disk is exactly what the
; smoke test validated.
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PyPackHub"; Filename: "{app}\PyPackHub.exe"
Name: "{group}\Uninstall PyPackHub"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PyPackHub"; Filename: "{app}\PyPackHub.exe"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\PyPackHub"; Filename: "{app}\PyPackHub.exe"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\PyPackHub.exe"; Description: "{cm:LaunchProgram,PyPackHub}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nothing the application creates lives here (its data is per-user), so the
; default uninstall leaves a clean directory behind.
Type: filesandordirs; Name: "{app}"
