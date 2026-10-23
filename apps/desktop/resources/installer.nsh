!include "LogicLib.nsh"
!include "FileFunc.nsh"
!define POLICY_SCRIPT "${__FILEDIR__}\installer-policy.ps1"

!ifdef BUILD_UNINSTALLER
  !define POLICY_PREFIX "un."
!else
  !define POLICY_PREFIX ""
!endif

Var policyResult
Var policyOutput
!ifndef BUILD_UNINSTALLER
Var policyVersion
Var policyRegistration
!endif
Var policyParameters
Var policyOption
Var policyProcessId

!macro customHeader
Function ${POLICY_PREFIX}InitializePolicy
  ${GetParameters} $policyParameters
  ClearErrors
  ${GetOptions} $policyParameters "--delete-app-data" $policyOption
  ${IfNot} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Профиль и рабочие дела сохраняются при удалении. Параметр --delete-app-data не поддерживается." /SD IDOK
    SetErrorLevel 20
    Quit
  ${EndIf}
  ClearErrors
  InitPluginsDir
  File /oname=$PLUGINSDIR\installer-policy.ps1 "${POLICY_SCRIPT}"
  Push $0
  System::Call 'kernel32::GetCurrentProcessId() i .r0'
  StrCpy $policyProcessId $0
  Pop $0
FunctionEnd

!ifndef BUILD_UNINSTALLER
Function CheckInstalledVersion
  Call InitializePolicy
  ReadRegStr $policyVersion HKCU "${UNINSTALL_REGISTRY_KEY}" "DisplayVersion"
  ReadRegStr $policyRegistration HKCU "${UNINSTALL_REGISTRY_KEY}" "UninstallString"
  ${If} $policyRegistration == ""
  ${AndIf} $policyVersion == ""
    ReadRegStr $policyRegistration HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
    ${If} $policyRegistration == ""
      ClearErrors
      Return
    ${EndIf}
  ${EndIf}
  nsExec::ExecToStack /TIMEOUT=15000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-policy.ps1" -Mode Version -InstalledVersion "$policyVersion" -IncomingVersion "${VERSION}"'
  Pop $policyResult
  Pop $policyOutput
  ${If} $policyResult != 0
    MessageBox MB_OK|MB_ICONSTOP "Установлена версия $policyVersion. Этот Setup содержит ${VERSION}. Установка более старой или неизвестной версии запрещена. Программа и данные не изменены." /SD IDOK
    SetErrorLevel 30
    Quit
  ${EndIf}
  ClearErrors
FunctionEnd
!endif

Function ${POLICY_PREFIX}WaitForApplicationExit
  Call ${POLICY_PREFIX}InitializePolicy
  policy_retry:
  nsExec::ExecToStack /TIMEOUT=15000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-policy.ps1" -Mode Processes -InstallationDirectory "$INSTDIR" -InstallerProcessId $policyProcessId'
  Pop $policyResult
  Pop $policyOutput
  ${If} $policyResult == 0
    ClearErrors
    Return
  ${EndIf}
  ${If} ${Silent}
    SetErrorLevel 20
    Quit
  ${EndIf}
  ${If} $policyResult == 10
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Сохраните работу и самостоятельно закройте все окна Impeller Reliability. Дождитесь завершения worker, затем выберите «Повторить». Установщик не завершает процессы." /SD IDCANCEL IDRETRY policy_retry
  ${Else}
    MessageBox MB_RETRYCANCEL|MB_ICONSTOP "Не удалось достоверно проверить завершение процессов приложения. Файлы не заменены. Закройте приложение и выберите «Повторить» или отмените установку." /SD IDCANCEL IDRETRY policy_retry
  ${EndIf}
  SetErrorLevel 20
  Quit
FunctionEnd

!macroend

!macro customInit
  Call CheckInstalledVersion
!macroend

!macro customCheckAppRunning
  !ifndef BUILD_UNINSTALLER
    Call CheckInstalledVersion
  !endif
  Call ${POLICY_PREFIX}WaitForApplicationExit
  ; The stock ZIP extractor quits on errors without setting an error level.
  ; Only the completed install/uninstall path may reset this to success.
  SetErrorLevel 20
!macroend

!macro customUnInit
  Call un.InitializePolicy
!macroend

!macro customUnInstall
  Call un.WaitForApplicationExit
!macroend

!macro customUnInstallCheck
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Не удалось запустить удаление прежней версии. Установка остановлена." /SD IDOK
    SetErrorLevel 20
    Quit
  ${EndIf}
  ${If} $R0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Прежняя версия не удалена (код $R0). Закройте приложение и повторите запуск Setup." /SD IDOK
    SetErrorLevel 20
    Quit
  ${EndIf}
!macroend
