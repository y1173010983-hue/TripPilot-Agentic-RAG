<#
One-command TripPilot deployment from Windows to a Linux cloud instance.
The script prompts locally for secrets and uploads them only as the remote .env.
#>
[CmdletBinding()]
param(
    [string] $CloudHost,
    [int] $CloudPort,
    [string] $CloudUser = "root",
    [string] $RemoteRoot = "/root/autodl-tmp/trippilot-app",
    [string] $LlmBaseUrl = "https://api.siliconflow.cn/v1",
    [string] $LlmModel = "deepseek-ai/DeepSeek-V3.2"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($CloudHost) -or $CloudPort -lt 1) {
    $sshLine = Read-Host "Paste the SSH command shown by the cloud provider"
    if ($sshLine -notmatch '^\s*ssh\s+-p\s+(\d+)\s+([^@\s]+)@([^\s]+)\s*$') {
        throw "SSH command format is invalid. Expected: ssh -p PORT USER@HOST"
    }
    $CloudPort = [int]$Matches[1]
    # Some copied UI strings escape '@' as '\@'. PowerShell treats that slash
    # literally, so remove it before constructing the SSH target.
    $CloudUser = ($Matches[2] -replace '\\', '')
    $CloudHost = $Matches[3]
}

function Convert-SecureStringToPlainText([Security.SecureString] $Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function New-DeploymentSecret([int] $ByteCount = 32) {
    $bytes = New-Object byte[] $ByteCount
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) }
    finally { $generator.Dispose() }
    return ([Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'B').Replace('=', 'C'))
}

function Get-LocalDotEnvValue([string] $Path, [string] $Name) {
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    $prefix = "$Name="
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line.StartsWith($prefix, [StringComparison]::Ordinal)) {
            return $line.Substring($prefix.Length).Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) "trippilot-cloud-$timestamp"
$archivePath = Join-Path $temporaryRoot "trippilot.tar.gz"
$envPath = Join-Path $temporaryRoot ".env"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

$localEnvPath = Join-Path $projectRoot ".env"
$llmKey = Get-LocalDotEnvValue $localEnvPath "TRIP_LLM_API_KEY"
$amapKey = Get-LocalDotEnvValue $localEnvPath "TRIP_AMAP_KEY"
if ([string]::IsNullOrWhiteSpace($llmKey)) {
    $llmKey = Convert-SecureStringToPlainText (Read-Host "SiliconFlow API Key" -AsSecureString)
}
if ([string]::IsNullOrWhiteSpace($amapKey)) {
    $amapKey = Convert-SecureStringToPlainText (Read-Host "AMap Web service Key (press Enter to skip)" -AsSecureString)
}
$adminPassword = Get-LocalDotEnvValue $localEnvPath "TRIP_BOOTSTRAP_ADMIN_PASSWORD"
if ([string]::IsNullOrWhiteSpace($adminPassword)) {
    $adminPassword = Convert-SecureStringToPlainText (Read-Host "TripPilot admin password (at least 10 characters)" -AsSecureString)
}
if ($adminPassword.Length -lt 10) { throw "Administrator password must contain at least 10 characters." }
if ([string]::IsNullOrWhiteSpace($llmKey)) { throw "A SiliconFlow API Key is required for online answers." }

$postgresPassword = New-DeploymentSecret
$redisPassword = New-DeploymentSecret
$minioPassword = New-DeploymentSecret
$milvusPassword = New-DeploymentSecret
$appSecret = New-DeploymentSecret 48

$envContent = @"
TRIP_ENVIRONMENT=production
TRIP_SINGLE_NODE_MODE=true
TRIP_SECRET_KEY=$appSecret
TRIP_BOOTSTRAP_ADMIN_USERNAME=admin
TRIP_BOOTSTRAP_ADMIN_PASSWORD=$adminPassword
TRIP_DATABASE_URL=sqlite:///./data/trippilot.db
TRIP_STORAGE_BACKEND=local
TRIP_QUEUE_BACKEND=database
TRIP_REDIS_URL=redis://:$redisPassword@127.0.0.1:6379/0
TRIP_VECTOR_BACKEND=bm25
TRIP_LLM_BASE_URL=$LlmBaseUrl
TRIP_LLM_API_KEY=$llmKey
TRIP_LLM_MODEL=$LlmModel
TRIP_AMAP_KEY=$amapKey
TRIP_POSTGRES_USER=trippilot
TRIP_POSTGRES_PASSWORD=$postgresPassword
TRIP_POSTGRES_DB=trippilot
TRIP_REDIS_PASSWORD=$redisPassword
TRIP_MINIO_ROOT_USER=trippilot-minio
TRIP_MINIO_ROOT_PASSWORD=$minioPassword
TRIP_MILVUS_ROOT_PASSWORD=$milvusPassword
TRIP_MINIO_ACCESS_KEY=trippilot-minio
TRIP_MINIO_SECRET_KEY=$minioPassword
TRIP_MINIO_ENDPOINT=127.0.0.1:9000
TRIP_MILVUS_URI=http://127.0.0.1:19530
"@
[IO.File]::WriteAllText($envPath, $envContent, [Text.UTF8Encoding]::new($false))

Push-Location $projectRoot
try {
    & tar -czf $archivePath `
        --exclude=.git --exclude=.venv --exclude=.venv-gpu --exclude=frontend/node_modules `
        --exclude=data --exclude=logs --exclude=volumes --exclude=.env `
        backend frontend migrations deploy tests pyproject.toml uv.lock alembic.ini
    if ($LASTEXITCODE -ne 0) { throw "Project archive creation failed." }
}
finally { Pop-Location }

try {
    & ssh -p $CloudPort "$CloudUser@$CloudHost" "mkdir -p '$RemoteRoot'"
    & scp -P $CloudPort $archivePath "$CloudUser@${CloudHost}:$RemoteRoot/trippilot.tar.gz"
    & scp -P $CloudPort $envPath "$CloudUser@${CloudHost}:$RemoteRoot/.env"
    & ssh -p $CloudPort "$CloudUser@$CloudHost" "cd '$RemoteRoot' && tar -xzf trippilot.tar.gz && chmod +x deploy/cloud_bootstrap.sh && bash deploy/cloud_bootstrap.sh '$RemoteRoot'"
    if ($LASTEXITCODE -ne 0) { throw "Cloud deployment failed. Read $RemoteRoot/logs/api.log and worker.log on the server." }
    Write-Host "Deployment succeeded. Map the cloud provider's HTTP/TCP port 8000, then open http://<public-host>:<mapped-port>/" -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}




