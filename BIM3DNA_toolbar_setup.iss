#define MyAppName "BIM3DNA Toolbar"
#define MyAppVersion "0.1.0"
#define MyPublisher "BIM3DNA"
#define MyPublisherURL "https://bim3dna.nl/"
#define MyExtensionName "BIM3DNA.extension"
#define MyOutputBaseFilename "BIM3DNA_Toolbar_Installer"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyPublisherURL}
AppSupportURL={#MyPublisherURL}
DefaultDirName={userappdata}\pyRevit\Extensions\{#MyExtensionName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=yes
SetupLogging=yes

[Files]
; Main BIM3DNA pyRevit extension
Source: "BIM3DNA.extension\*"; DestDir: "{userappdata}\pyRevit\Extensions\BIM3DNA.extension"; Flags: ignoreversion recursesubdirs createallsubdirs

; AI runtime folders kept as siblings to the extension, matching current dev layout
Source: "Model_Service\*"; DestDir: "{userappdata}\pyRevit\Extensions\Model_Service"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "Openai_Server\*"; DestDir: "{userappdata}\pyRevit\Extensions\Openai_Server"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Optional bundled Ollama installer
Source: "OllamaSetup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

; --------------------------------------------------------------------------
; OPTIONAL: only use these if these folders still exist OUTSIDE the extension
; and are required at runtime on target machines.
; If you move them inside BIM3DNA.extension, remove these entries.
; --------------------------------------------------------------------------
; Source: "Model_Service\*"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\Model_Service"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Source: "openai_Server\*"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\openai_Server"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Source: "Openai_Server\*"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\Openai_Server"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Source: "Model_Service\*.json"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\Model_Service"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}"

[Code]
var
  OllamaPage: TInputOptionWizardPage;
  LocalInstallerPage: TInputQueryWizardPage;
  ModelPage: TInputOptionWizardPage;
  BrowseButton: TNewButton;

function OllamaExePath(): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe');
end;

function BundledInstallerPath(): string;
begin
  Result := ExpandConstant('{tmp}\OllamaSetup.exe');
end;

function HasBundledInstaller(): Boolean;
begin
  Result := FileExists(BundledInstallerPath());
end;

function IsOllamaInstalled(): Boolean;
begin
  Result := FileExists(OllamaExePath());
end;

function IsOllamaCliReady(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;

  if not IsOllamaInstalled() then
    exit;

  Result := Exec(
    OllamaExePath(),
    'list',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function StartOllamaAndWait(): Boolean;
var
  ResultCode: Integer;
  I: Integer;
begin
  Result := False;

  if not IsOllamaInstalled() then
    exit;

  if IsOllamaCliReady() then
  begin
    Result := True;
    exit;
  end;

  Exec(OllamaExePath(), 'serve', '', SW_HIDE, ewNoWait, ResultCode);

  for I := 1 to 12 do
  begin
    Sleep(2000);
    if IsOllamaCliReady() then
    begin
      Result := True;
      exit;
    end;
  end;
end;

function PullOllamaModel(const ModelName: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    OllamaExePath(),
    'pull ' + ModelName,
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function SelectedModelName(): string;
begin
  Result := '';

  { Keep this aligned with the default model configured in the BIM3DNA AI tool }
  if ModelPage.Values[0] then
    Result := 'phi3:mini'
  else if ModelPage.Values[1] then
    Result := 'phi3'
  else if ModelPage.Values[2] then
    Result := 'gemma3:1b'
  else if ModelPage.Values[3] then
    Result := 'llama3.2:1b'
  else if ModelPage.Values[4] then
    Result := 'llama3.2:3b'
  else
    Result := '';
end;

procedure BrowseLocalInstallerClick(Sender: TObject);
var
  FileName: string;
begin
  FileName := Trim(LocalInstallerPage.Values[0]);
  if FileName = '' then
    FileName := ExpandConstant('{userdesktop}\OllamaSetup.exe');

  if GetOpenFileName(
      'Select local Ollama installer',
      FileName,
      '',
      'Executable files (*.exe)|*.exe',
      'exe') then
  begin
    LocalInstallerPage.Values[0] := FileName;
  end;
end;

function InstallBundledOllama(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;

  if not HasBundledInstaller() then
  begin
    MsgBox(
      'No bundled Ollama installer was found inside this setup package.' + #13#10 + #13#10 +
      'Place OllamaSetup.exe next to this .iss file before compiling, or choose another installation option.',
      mbError,
      MB_OK
    );
    exit;
  end;

  Result := Exec(
    BundledInstallerPath(),
    '',
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function InstallLocalOllama(): Boolean;
var
  ResultCode: Integer;
  InstallerPath: string;
begin
  Result := False;
  InstallerPath := Trim(LocalInstallerPage.Values[0]);

  if (InstallerPath = '') or (not FileExists(InstallerPath)) then
  begin
    MsgBox(
      'The selected Ollama installer is empty or invalid.' + #13#10 + #13#10 +
      'Please browse to a valid OllamaSetup.exe file or choose another option.',
      mbError,
      MB_OK
    );
    exit;
  end;

  Result := Exec(
    InstallerPath,
    '',
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function InstallOfficialOllamaNow(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    ExpandConstant('{cmd}'),
    '/C powershell -ExecutionPolicy Bypass -Command "irm https://ollama.com/install.ps1 | iex"',
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

procedure OpenOfficialOllamaDownload();
var
  ResultCode: Integer;
begin
  ShellExec(
    'open',
    'https://ollama.com/download/windows',
    '',
    '',
    SW_SHOWNORMAL,
    ewNoWait,
    ResultCode
  );
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;

  if (PageID = OllamaPage.ID) and IsOllamaInstalled() then
  begin
    Result := True;
    exit;
  end;

  if (PageID = LocalInstallerPage.ID) then
  begin
    if IsOllamaInstalled() then
    begin
      Result := True;
      exit;
    end;

    if not OllamaPage.Values[1] then
    begin
      Result := True;
      exit;
    end;
  end;

  if (PageID = ModelPage.ID) then
  begin
    if IsOllamaInstalled() then
    begin
      Result := False;
      exit;
    end;

    if OllamaPage.Values[0] or OllamaPage.Values[1] or OllamaPage.Values[2] then
      Result := False
    else
      Result := True;
  end;
end;

procedure InitializeWizard;
begin
  OllamaPage :=
    CreateInputOptionPage(
      wpSelectDir,
      'Ollama Setup',
      'Local AI runtime dependency',
      'The BIM3DNA toolbar includes AI-assisted features that can use Ollama for local models.' + #13#10 + #13#10 +
      'Choose how you want to handle Ollama setup on this machine.' + #13#10 +
      'The toolbar itself will still be installed regardless.',
      True,
      False
    );

  OllamaPage.Add('Use bundled Ollama installer (recommended if included)');
  OllamaPage.Add('Browse to local Ollama installer');
  OllamaPage.Add('Install from official source now');
  OllamaPage.Add('Open official download page');
  OllamaPage.Add('Skip Ollama setup for now');

  if HasBundledInstaller() then
    OllamaPage.Values[0] := True
  else
    OllamaPage.Values[2] := True;

  LocalInstallerPage :=
    CreateInputQueryPage(
      OllamaPage.ID,
      'Local Ollama Installer',
      'Choose a local Ollama installer',
      'Provide a path to an existing OllamaSetup.exe file on this machine or a shared location.'
    );

  LocalInstallerPage.Add('Local Ollama installer path:', False);
  LocalInstallerPage.Values[0] := ExpandConstant('{userdesktop}\OllamaSetup.exe');

  BrowseButton := TNewButton.Create(LocalInstallerPage.Surface);
  BrowseButton.Parent := LocalInstallerPage.Surface;
  BrowseButton.Left := LocalInstallerPage.Edits[0].Left + LocalInstallerPage.Edits[0].Width + ScaleX(8);
  BrowseButton.Top := LocalInstallerPage.Edits[0].Top - ScaleY(1);
  BrowseButton.Width := ScaleX(75);
  BrowseButton.Height := LocalInstallerPage.Edits[0].Height + ScaleY(2);
  BrowseButton.Caption := 'Browse...';
  BrowseButton.OnClick := @BrowseLocalInstallerClick;

  ModelPage :=
    CreateInputOptionPage(
      LocalInstallerPage.ID,
      'Ollama Model Setup',
      'Choose an initial supported model',
      'Pick one pinned starter model for team testing.' + #13#10 + #13#10 +
      'Keep this aligned with the AI Workbench default model in the toolbar.',
      True,
      False
    );

  ModelPage.Add('phi3:mini (recommended for most PCs)');
  ModelPage.Add('phi3');
  ModelPage.Add('gemma3:1b');
  ModelPage.Add('llama3.2:1b');
  ModelPage.Add('llama3.2:3b');
  ModelPage.Add('Skip model download for now');
  ModelPage.Values[0] := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  InstallOk: Boolean;
  ModelName: string;
begin
  if CurStep <> ssPostInstall then
    exit;

  if not IsOllamaInstalled() then
  begin
    InstallOk := False;

    if OllamaPage.Values[0] then
      InstallOk := InstallBundledOllama()
    else if OllamaPage.Values[1] then
      InstallOk := InstallLocalOllama()
    else if OllamaPage.Values[2] then
      InstallOk := InstallOfficialOllamaNow()
    else if OllamaPage.Values[3] then
    begin
      OpenOfficialOllamaDownload();
      MsgBox(
        'The BIM3DNA toolbar was installed.' + #13#10 + #13#10 +
        'The official Ollama download page has been opened.' + #13#10 +
        'After installing Ollama, run this installer again if you want automated model setup.',
        mbInformation,
        MB_OK
      );
      exit;
    end
    else
    begin
      MsgBox(
        'The BIM3DNA toolbar was installed, but Ollama setup was skipped.' + #13#10 + #13#10 +
        'The AI features will not function until Ollama is installed.',
        mbInformation,
        MB_OK
      );
      exit;
    end;

    if not InstallOk then
    begin
      MsgBox(
        'The BIM3DNA toolbar was installed, but Ollama installation did not complete successfully.' + #13#10 + #13#10 +
        'You can run the installer again later or install Ollama manually.',
        mbInformation,
        MB_OK
      );
      exit;
    end;
  end;

  if not StartOllamaAndWait() then
  begin
    MsgBox(
      'Ollama appears to be installed, but it is not ready yet.' + #13#10 + #13#10 +
      'Please start Ollama manually and wait a moment before using the AI features.',
      mbInformation,
      MB_OK
    );
    exit;
  end;

  ModelName := SelectedModelName();
  if ModelName <> '' then
  begin
    if PullOllamaModel(ModelName) then
    begin
      MsgBox(
        'Ollama is ready and the selected model "' + ModelName + '" was installed successfully.' + #13#10 + #13#10 +
        'Make sure the BIM3DNA AI configuration uses the same model name.',
        mbInformation,
        MB_OK
      );
    end
    else
    begin
      MsgBox(
        'Ollama is installed, but the model download failed.' + #13#10 + #13#10 +
        'You can run this manually later:' + #13#10 +
        'ollama pull ' + ModelName,
        mbError,
        MB_OK
      );
    end;
  end
  else
  begin
    MsgBox(
      'Ollama is installed and ready. Model download was skipped.',
      mbInformation,
      MB_OK
    );
  end;

  MsgBox(
    'Installation finished.' + #13#10 + #13#10 +
    'If Revit is already open, reload pyRevit or restart Revit to load the new BIM3DNA toolbar.',
    mbInformation,
    MB_OK
  );
end;