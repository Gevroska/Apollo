param([Parameter(Mandatory)][string]$Installer)
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $Installer)).Replace("`r`n", "`n")
$testRoot = Join-Path $env:RUNNER_TEMP ('apollo-service-' + [guid]::NewGuid().ToString('N'))
$originalLocalAppData = $env:LOCALAPPDATA
$originalTestScript = $env:APOLLO_QUOTING_SCRIPT

function Get-RawImagePath([string]$RegistryPath) {
  $key = Get-Item -LiteralPath $RegistryPath
  try {
    if ($key.GetValueKind('ImagePath') -ne [Microsoft.Win32.RegistryValueKind]::ExpandString) {
      throw 'SCM ImagePath has an unexpected registry type'
    }
    # The default registry getter expands %NAME% and cannot verify stored bytes.
    return $key.GetValue('ImagePath', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  } finally {
    $key.Dispose()
  }
}

try {
  foreach ($directory in @('Apollo', 'Apollo Service Path', 'Apollo (CI) & Path', 'Apollo !literal! 100%', 'Apollo !literal! %PATH%')) {
    # A literal environment-token directory tests batch/registry preservation
    # only: SCM expands the token, so that directory is not a startup target.
    $storageOnly = $directory -ceq 'Apollo !literal! %PATH%'
    foreach ($newline in @("`n", "`r`n")) {
      $serviceName = 'ApolloQuoteTest_' + [guid]::NewGuid().ToString('N')
      $fixture = [IO.Path]::GetFullPath((Join-Path $testRoot ($serviceName + '/' + $directory)))
      $scripts = New-Item -ItemType Directory -Path (Join-Path $fixture 'scripts') -Force
      $script = Join-Path $scripts.FullName 'install-service.bat'
      $binary = [IO.Path]::GetFullPath((Join-Path $fixture 'tools/sunshinesvc.exe'))
      $expected = '"' + $binary.Replace('/', '\') + '"'
      $registry = "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
      $env:LOCALAPPDATA = Join-Path $fixture 'Profile !data! (CI) & Settings'
      $settings = New-Item -ItemType Directory -Path (Join-Path $env:LOCALAPPDATA 'SudoMaker/Apollo') -Force
      $savedStartType = Join-Path $settings.FullName 'service_start_type.txt'

      # Execute the complete packaged script. Only isolate the service name and
      # suppress process start/stop and legacy-service deletion side effects.
      $fixtureSource = $source
      foreach ($line in @('net stop sunshinesvc', 'sc delete sunshinesvc', '    net stop %SERVICE_NAME%', 'net start %SERVICE_NAME%')) {
        if (-not $fixtureSource.Contains($line + "`n")) { throw "Missing service isolation boundary: $line" }
        $fixtureSource = $fixtureSource.Replace($line + "`n", 'rem Isolated by service-path regression test' + "`n")
      }
      if (-not $fixtureSource.Contains('set SERVICE_NAME=ApolloService')) { throw 'Missing service-name fixture boundary' }
      $fixtureSource = $fixtureSource.Replace('set SERVICE_NAME=ApolloService', "set SERVICE_NAME=$serviceName")
      [IO.File]::WriteAllText($script, $fixtureSource.Replace("`n", $newline), [Text.Encoding]::ASCII)
      $env:APOLLO_QUOTING_SCRIPT = $script
      try {
        foreach ($mode in @('create', 'config')) {
          if ($mode -eq 'config') {
            & sc.exe config $serviceName binPath= $binary
            if ($LASTEXITCODE -ne 0) { throw 'Could not prepare unquoted upgrade fixture' }
            if ((Get-RawImagePath $registry) -cne $binary) { throw 'Upgrade fixture differs from the unquoted raw path' }
          }
          [IO.File]::WriteAllText($savedStartType, '3', [Text.Encoding]::ASCII)
          # Delayed expansion of an environment value preserves literal %, !,
          # spaces and metacharacters while invoking the script under test.
          & $env:ComSpec /d /v:on /c '"!APOLLO_QUOTING_SCRIPT!"'
          if ($LASTEXITCODE -ne 0) { throw "Service $mode script failed" }
          if ((Get-RawImagePath $registry) -cne $expected) { throw "$mode persisted an incorrect raw ImagePath" }
          $properties = Get-ItemProperty -LiteralPath $registry
          $expanded = [Environment]::ExpandEnvironmentVariables($expected)
          if ($properties.ImagePath -cne $expanded) { throw "$mode returned an incorrect expanded ImagePath" }
          if ($properties.Start -ne 3 -or $properties.DisplayName -cne 'Apollo Service' -or $properties.ObjectName -cne 'LocalSystem') {
            throw "$mode changed the service start type, display name or account"
          }
          if (Test-Path -LiteralPath $savedStartType) { throw 'Saved start type was not consumed from the exact profile path' }
          $configuration = & sc.exe qc $serviceName
          if ($LASTEXITCODE -ne 0) { throw 'sc qc failed' }
          $pathLines = @($configuration | Select-String '^\s*BINARY_PATH_NAME\s*:\s*(.*)$')
          if ($pathLines.Count -ne 1) { throw 'sc qc did not return one binary path' }
          $scmPath = $pathLines[0].Matches[0].Groups[1].Value
          if ($storageOnly) {
            # QueryServiceConfig also expands tokens, using SCM's environment
            # rather than this runner process's potentially different PATH.
            $token = $expected.IndexOf('%PATH%', [StringComparison]::Ordinal)
            $prefix = $expected.Substring(0, $token)
            $suffix = $expected.Substring($token + '%PATH%'.Length)
            if ($expanded -ceq $expected -or $scmPath -ceq $expected -or
                -not $scmPath.StartsWith($prefix, [StringComparison]::Ordinal) -or
                -not $scmPath.EndsWith($suffix, [StringComparison]::Ordinal) -or
                $scmPath.Length -le $prefix.Length + $suffix.Length) {
              throw 'SCM environment expansion did not preserve the quoted path boundaries'
            }
            Write-Host "$mode verified raw storage and SCM expansion only (not literal-path startup): $directory"
          } else {
            if ($expanded -cne $expected -or $scmPath -cne $expected) { throw 'SCM did not retain the exact quoted literal path' }
            Write-Host "$mode verified complete service script and literal path: $directory"
          }
        }
      } finally {
        if (Test-Path -LiteralPath $registry) {
          & sc.exe delete $serviceName
          if ($LASTEXITCODE -ne 0) { throw 'Temporary service cleanup failed' }
        }
      }
    }
  }
} finally {
  $env:LOCALAPPDATA = $originalLocalAppData
  $env:APOLLO_QUOTING_SCRIPT = $originalTestScript
  # Validate the exact generated root before recursive fixture cleanup.
  $resolved = [IO.Path]::GetFullPath($testRoot)
  $parent = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
  if (-not $resolved.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase)) { throw 'Service fixture escaped RUNNER_TEMP' }
  if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
