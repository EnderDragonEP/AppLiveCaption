; Inno Setup script for the installed version of AppLiveCaption (the portable version is the zip).
; build.ps1 -Installer builds the app, then runs:
;
;     ISCC /DAppVersion=1.1.0 installer\AppLiveCaption.iss
;
; and writes dist\AppLiveCaption-<version>-Setup.exe. Setup asks whether to install for the current
; user only (no admin; %LOCALAPPDATA%\Programs) or for all users (Program Files). Either way it puts
; AppLiveCaption.installed next to the exe, so the app keeps its data in %APPDATA%\AppLiveCaption
; (app/paths.py) instead of next to the exe. Uninstalling asks whether to delete that data.

#ifndef AppVersion
  #error Pass the app's version: ISCC /DAppVersion=x.y.z installer\AppLiveCaption.iss (build.ps1 -Installer does).
#endif

#define AppName "AppLiveCaption"
#define AppExe "AppLiveCaption.exe"
#define RepoUrl "https://github.com/EnderDragonEP/AppLiveCaption"
; PyInstaller's output. Only the exe and _internal are packed: the rest of that folder can be the
; settings, history and models of a built app that was run there.
#define Dist "..\dist\AppLiveCaption"

[Setup]
; Never change AppId: Windows finds an existing install (to update or uninstall it) by it.
AppId={{D784A470-4B86-466C-B9EE-956509C23047}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Ender Yang
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Current user by default, without admin rights; Setup offers "all users" (admin) too.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Held by the running app (app/main.py), so Setup and the uninstaller ask to close it first.
AppMutex=AppLiveCaption.Running,Global\AppLiveCaption.Running
OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile=..\app\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the libraries completely; files of the old version left in between can break it.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Dist}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Dist}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "AppLiveCaption.installed"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function DataDir: String;
begin
  Result := ExpandConstant('{userappdata}\{#AppName}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // The app's data (app/paths.py) isn't part of the install. Ask before deleting it; keeping it is
  // the default, and the answer in a silent uninstall.
  if (CurUninstallStep = usPostUninstall) and DirExists(DataDir) then
    // (No line may start with "#": the preprocessor would take it for a directive.)
    if SuppressibleMsgBox(
         'Do you also want to delete your AppLiveCaption settings, history, downloaded models and GPU support?' + #13#10#13#10 +
         DataDir + #13#10#13#10 +
         'Choose No to keep them for when you install AppLiveCaption again.',
         mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      DelTree(DataDir, True, True, True);
end;
