; Inno Setup script for the installed version of AppLiveCaption (the portable version is the zip).
; build.ps1 -Installer builds the app, then runs:
;
;     ISCC /DAppVersion=1.1.0 installer\AppLiveCaption.iss
;
; and writes dist\AppLiveCaption-<version>-Setup.exe. Setup asks whether to install for the current
; user only (no admin; %LOCALAPPDATA%\Programs) or for all users (Program Files). Either way it puts
; AppLiveCaption.installed next to the exe, so the app keeps its data in the user's profile instead of
; next to the exe (app/paths.py): settings and history in %APPDATA%\AppLiveCaption, logs, models and
; GPU support in %LOCALAPPDATA%\AppLiveCaption. Uninstalling asks which of them to delete; a silent
; uninstall keeps them all unless /DELETEDATA=settings,models,gpu (or all) says otherwise.

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
// The app's data (app/paths.py) isn't part of the install, so uninstalling keeps it unless the user
// ticks what to delete. (No line in this section may start with "#": the preprocessor would take it
// for a directive.)

// Settings and history (they roam with the user's profile).
function RoamingDir: String;
begin
  Result := ExpandConstant('{userappdata}\{#AppName}');
end;

// Logs, models and GPU support (big, and tied to this PC).
function LocalDir: String;
begin
  Result := ExpandConstant('{localappdata}\{#AppName}');
end;

function FolderSize(const Dir: String): Int64;
var
  Rec: TFindRec;
begin
  Result := 0;
  if FindFirst(AddBackslash(Dir) + '*', Rec) then
    try
      repeat
        if (Rec.Name <> '.') and (Rec.Name <> '..') then
          if Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
            Result := Result + FolderSize(AddBackslash(Dir) + Rec.Name)
          else
            Result := Result + Int64(Rec.SizeHigh) * 4294967296 + Rec.SizeLow;
      until not FindNext(Rec);
    finally
      FindClose(Rec);
    end;
end;

function SizeText(Bytes: Int64): String;
var
  Tenths: Int64;
begin
  if Bytes >= 1073741824 then
  begin
    Tenths := Bytes * 10 div 1073741824;
    Result := IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10) + ' GB';
  end
  else if Bytes >= 1048576 then
    Result := IntToStr(Bytes div 1048576) + ' MB'
  else
    Result := 'less than 1 MB';
end;

// /DELETEDATA=settings,models,gpu or all: what a (silent) uninstall deletes without asking.
function DeleteDataParam(const What: String): Boolean;
var
  Value: String;
begin
  Value := ',' + Lowercase(ExpandConstant('{param:DELETEDATA|}')) + ',';
  Result := (Pos(',all,', Value) > 0) or (Pos(',' + What + ',', Value) > 0);
end;

function AddCheckBox(Form: TSetupForm; const Caption: String; Checked: Boolean; var Top: Integer): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(Form);
  Result.Parent := Form;
  Result.Caption := Caption;
  Result.Checked := Checked;
  Result.Left := ScaleX(16);
  Result.Top := Top;
  Result.Width := Form.ClientWidth - ScaleX(32);
  Result.Height := ScaleY(20);
  Top := Top + Result.Height + ScaleY(6);
end;

function AddText(Form: TSetupForm; const Caption: String; var Top: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(Form);
  Result.AutoSize := False;
  Result.WordWrap := True;
  Result.Left := ScaleX(16);
  Result.Top := Top;
  Result.Width := Form.ClientWidth - ScaleX(32);
  Result.Caption := Caption;
  Result.Parent := Form;
  Result.AdjustHeight;
  Top := Top + Result.Height + ScaleY(12);
end;

// Ask which data to delete; every box starts unticked (keeping is the default).
procedure AskWhatToDelete(HasSettings, HasModels, HasGpu: Boolean; var DelSettings, DelModels, DelGpu: Boolean);
var
  Form: TSetupForm;
  SettingsBox, ModelsBox, GpuBox: TNewCheckBox;
  OkButton: TNewButton;
  Top: Integer;
begin
  Form := CreateCustomForm(ScaleX(340), ScaleY(200), False, True);
  try
    Form.Caption := '{#AppName} Uninstall';
    Top := ScaleY(16);
    AddText(Form, 'AppLiveCaption is removed. Delete your data too?', Top);
    SettingsBox := nil;
    ModelsBox := nil;
    GpuBox := nil;
    if HasSettings then
      SettingsBox := AddCheckBox(Form, 'Settings and history', DelSettings, Top);
    if HasModels then
      ModelsBox := AddCheckBox(Form, 'Downloaded models (' + SizeText(FolderSize(LocalDir + '\models')) + ')', DelModels, Top);
    if HasGpu then
      GpuBox := AddCheckBox(Form, 'GPU support (' + SizeText(FolderSize(LocalDir + '\cuda')) + ')', DelGpu, Top);
    Top := Top + ScaleY(4);
    AddText(Form, 'Anything unticked is kept.', Top).Font.Color := clGrayText;

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := 'OK';
    OkButton.Width := Form.CalculateButtonWidth([OkButton.Caption]);
    OkButton.Height := ScaleY(23);
    OkButton.Left := Form.ClientWidth - OkButton.Width - ScaleX(16);
    OkButton.Top := Top;
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;
    OkButton.Cancel := True;
    Form.ClientHeight := Top + OkButton.Height + ScaleY(16);
    Form.ActiveControl := OkButton;
    Form.FlipAndCenterIfNeeded(True, UninstallProgressForm, False);

    Form.ShowModal();
    DelSettings := (SettingsBox <> nil) and SettingsBox.Checked;
    DelModels := (ModelsBox <> nil) and ModelsBox.Checked;
    DelGpu := (GpuBox <> nil) and GpuBox.Checked;
  finally
    Form.Free();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  HasSettings, HasModels, HasGpu, DelSettings, DelModels, DelGpu: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  HasSettings := DirExists(RoamingDir) or DirExists(LocalDir + '\logs');
  HasModels := DirExists(LocalDir + '\models');
  HasGpu := DirExists(LocalDir + '\cuda');
  if not (HasSettings or HasModels or HasGpu) then
    Exit;
  DelSettings := DeleteDataParam('settings');
  DelModels := DeleteDataParam('models');
  DelGpu := DeleteDataParam('gpu');
  if not UninstallSilent then
    AskWhatToDelete(HasSettings, HasModels, HasGpu, DelSettings, DelModels, DelGpu);

  if DelSettings then
  begin
    DelTree(RoamingDir, True, True, True);
    DelTree(LocalDir + '\logs', True, True, True);
  end;
  if DelModels then
    DelTree(LocalDir + '\models', True, True, True);
  if DelGpu then
    DelTree(LocalDir + '\cuda', True, True, True);
  RemoveDir(LocalDir);  // only if nothing is left in it
end;
