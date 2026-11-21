; Inno Setup 6 script (6.3 or later): the ctgedit installer.
;
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; packaging\build.ps1 runs it after the PyInstaller build (it passes AppVersion
; and IconFile). Input: dist\ctgedit\ (the one-folder build, no Python needed on
; the target PC). Output: dist\ctgedit-<version>-setup.exe.
;
; Installs for the current user by default (no administrator rights; the
; wizard offers "all users" too), adds a Start menu shortcut, offers .ctg in
; Explorer's "Open with" list and -- task, on by default -- opens .ctg pages
; with ctgedit on double-click. A second double-click hands the page to the
; running editor (ctgedit.ui.single_instance). Upgrades close a running
; ctgedit first (AppMutex, created by ctgedit.app).
; ctgedit's own updater (ctgedit.ui.dialog.updates) runs it with /SILENT and
; /RELAUNCH=1, and it starts the new ctgedit when done.

#define AppName "ctgedit"
#define AppExe "ctgedit.exe"
; the taskbar identity ctgedit.app sets (SetCurrentProcessExplicitAppUserModelID):
; a pinned shortcut then groups with the running editor
#define AppUserModel "ctgedit.editor"
; paths relative to this script, whatever the current folder is
#define SrcDir AddBackslash(SourcePath) + "..\dist\ctgedit"
#ifndef AppVersion
  #define AppVersion GetStringFileInfo(SrcDir + "\" + AppExe, "ProductVersion")
#endif
#ifndef IconFile
  #define IconFile AddBackslash(SourcePath) + "..\build\pyinstaller\ctgedit\generated\ctgedit.ico"
#endif

[Setup]
AppId={{8C1F5C2E-3D4A-4B9E-9F57-6E2B1C0A7D41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ctgedit
AppComments=Editor for AVEVA Plant SCADA (Citect) graphics pages
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
LicenseFile=..\LICENSE
OutputBaseFilename=ctgedit-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
AppMutex=ctgeditAppMutex
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "assoc"; Description: "Open .ctg graphics pages with ctgedit when they are double-clicked"; GroupDescription: "File association:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; an upgrade replaces the whole bundle: no module of the previous version lingers
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Edit AVEVA Plant SCADA graphics pages"; AppUserModelID: "{#AppUserModel}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "{#AppUserModel}"

[Registry]
; the page file type ctgedit offers (always: Explorer's "Open with" list)
Root: HKA; Subkey: "Software\Classes\ctgedit.page"; ValueType: string; ValueName: ""; ValueData: "Citect graphics page"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ctgedit.page\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"
Root: HKA; Subkey: "Software\Classes\ctgedit.page\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.ctg\OpenWithProgids"; ValueType: string; ValueName: "ctgedit.page"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".ctg"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
; double-click opens ctgedit (task "assoc"; AVEVA registers no program for .ctg)
Root: HKA; Subkey: "Software\Classes\.ctg"; ValueType: string; ValueName: ""; ValueData: "ctgedit.page"; Flags: uninsdeletevalue; Tasks: assoc

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start ctgedit"; Flags: nowait postinstall skipifsilent
; an update ctgedit started (Help > Check for Updates: /SILENT /RELAUNCH=1) opens it again
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;
