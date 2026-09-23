<#
.SYNOPSIS
    Deploy the SQL MCP Server to Azure Container Apps.

.DESCRIPTION
    Creates a resource group, a container registry, a Container Apps
    environment, and the container app itself - then wires the app to your
    database using a managed identity, so no connection secret is ever stored.

    The script is idempotent. If a resource already exists it is reused, so you
    can re-run after a failure without cleaning up by hand.

    IT WILL REFUSE TO DEPLOY a config with authentication disabled unless you
    explicitly pass -AllowUnauthenticated. See the warning in the output.

.EXAMPLE
    ./deploy.ps1 -ResourceGroup rg-sql-mcp -Location eastus `
                 -SqlServer myserver.database.fabric.microsoft.com `
                 -SqlDatabase deposits

.EXAMPLE
    # A throwaway demo, knowingly public. Do not do this with real data.
    ./deploy.ps1 -ResourceGroup rg-sql-mcp -Location eastus `
                 -SqlServer ... -SqlDatabase ... -AllowUnauthenticated
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ResourceGroup,
    [Parameter(Mandatory)] [string] $SqlServer,
    [Parameter(Mandatory)] [string] $SqlDatabase,

    [string] $Location   = "eastus",
    [string] $AppName    = "sql-mcp-server",
    [string] $ConfigPath = "$PSScriptRoot/../dab/dab-config.json",

    # Deploy even though the config allows anonymous access. Only for a
    # throwaway demo against synthetic data.
    [switch] $AllowUnauthenticated
)

$ErrorActionPreference = "Stop"

function Step {
    param([string] $Message)
    Write-Host ""
    Write-Host "──> $Message" -ForegroundColor Cyan
}

function Ok {
    param([string] $Message)
    Write-Host "    $Message" -ForegroundColor DarkGray
}

# ── 0. Safety check ──────────────────────────────────────────────────────────
# This is the most consequential thing this script does. A public ingress in
# front of an unauthenticated config is an open database.

Step "Checking the configuration"

if (-not (Test-Path $ConfigPath)) {
    throw "Config not found at $ConfigPath"
}

$config   = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$provider = $config.runtime.host.authentication.provider
Ok "authentication provider: $provider"

if ($provider -eq "Unauthenticated") {
    Write-Host ""
    Write-Host "  ┌────────────────────────────────────────────────────────────┐" -ForegroundColor Yellow
    Write-Host "  │  WARNING: authentication is disabled                       │" -ForegroundColor Yellow
    Write-Host "  └────────────────────────────────────────────────────────────┘" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  This container will have a PUBLIC URL. With" -NoNewline
    Write-Host " Unauthenticated" -ForegroundColor Yellow -NoNewline
    Write-Host ", anyone"
    Write-Host "  who discovers that URL can read every row you have published."
    Write-Host ""
    Write-Host "  Fix it before deploying: set an Entra JWT provider and replace"
    Write-Host "  the 'anonymous' role on each entity. See docs/04-deploy-to-azure.md."
    Write-Host ""

    if (-not $AllowUnauthenticated) {
        Write-Host "  Refusing to deploy. Pass -AllowUnauthenticated to override" -ForegroundColor Red
        Write-Host "  (only sensible for a throwaway demo over synthetic data)." -ForegroundColor Red
        exit 1
    }

    Write-Host "  -AllowUnauthenticated was passed. Continuing anyway." -ForegroundColor Yellow
    Start-Sleep -Seconds 3
}
else {
    Ok "authentication is configured - good"
}

# ── 1. Subscription ──────────────────────────────────────────────────────────

Step "Checking your Azure sign-in"
$account = az account show --output json 2>$null | ConvertFrom-Json
if (-not $account) {
    throw "Not signed in. Run: az login"
}
Ok "subscription: $($account.name)"
Ok "identity    : $($account.user.name)"

# The containerapp commands live in an extension that is not installed by
# default. Installing it here saves a confusing failure four steps later.
Step "Checking the Azure CLI containerapp extension"
$ext = az extension list --query "[?name=='containerapp'].name" --output tsv 2>$null
if ($ext) {
    Ok "already installed"
} else {
    Ok "installing (one-off, takes a moment)"
    az extension add --name containerapp --only-show-errors --output none 2>$null
    Ok "installed"
}

# Container Apps needs these resource providers registered on the subscription.
# Registration is idempotent and usually instant if already done.
foreach ($rp in @("Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.ContainerRegistry")) {
    $state = az provider show --namespace $rp --query registrationState --output tsv 2>$null
    if ($state -ne "Registered") {
        Ok "registering $rp (this can take a minute)"
        az provider register --namespace $rp --wait --output none 2>$null
    }
}
Ok "resource providers registered"

# ── 2. Resource group ────────────────────────────────────────────────────────

Step "Resource group: $ResourceGroup"
if ((az group exists --name $ResourceGroup) -eq "true") {
    Ok "already exists, reusing"
} else {
    az group create --name $ResourceGroup --location $Location --output none
    Ok "created in $Location"
}

# ── 3. Container registry ────────────────────────────────────────────────────
# The name must be globally unique and alphanumeric only, so it is derived from
# the resource group with a stable hash rather than a random number. Re-running
# the script therefore finds the same registry instead of creating a new one.

$hash    = [Math]::Abs($ResourceGroup.GetHashCode()) % 100000
$AcrName = ("acr" + ($AppName -replace '[^a-zA-Z0-9]','') + $hash).ToLower()
if ($AcrName.Length -gt 50) { $AcrName = $AcrName.Substring(0, 50) }

Step "Container registry: $AcrName"
$acr = az acr show --name $AcrName --resource-group $ResourceGroup --output json 2>$null | ConvertFrom-Json
if ($acr) {
    Ok "already exists, reusing"
} else {
    az acr create --name $AcrName --resource-group $ResourceGroup `
        --sku Basic --location $Location --output none
    Ok "created"
}

# ── 4. Build the image ───────────────────────────────────────────────────────
# az acr build runs the build in Azure, so Docker does not need to be installed
# locally. The build context is deploy/, and the config is copied in beside the
# Dockerfile first so the COPY instruction can find it.

Step "Building the container image"
$buildDir = Join-Path $PSScriptRoot "_build"
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
Copy-Item $ConfigPath (Join-Path $buildDir "dab-config.json") -Force
Copy-Item (Join-Path $PSScriptRoot "Dockerfile") $buildDir -Force

$tag = Get-Date -Format "yyyyMMddHHmm"
az acr build --registry $AcrName --image "sql-mcp:$tag" --image "sql-mcp:latest" `
    $buildDir --output none
Remove-Item $buildDir -Recurse -Force
Ok "pushed sql-mcp:$tag"

$loginServer = az acr show --name $AcrName --query loginServer --output tsv

# ── 5. Container Apps environment ────────────────────────────────────────────

$EnvName = "$AppName-env"
Step "Container Apps environment: $EnvName"
# Named $acaEnv rather than $env - the latter collides with PowerShell's
# environment-variable drive and makes for confusing reading.
$acaEnv = az containerapp env show --name $EnvName --resource-group $ResourceGroup --output json 2>$null | ConvertFrom-Json
if ($acaEnv) {
    Ok "already exists, reusing"
} else {
    Ok "creating (this takes a few minutes)"
    az containerapp env create --name $EnvName --resource-group $ResourceGroup `
        --location $Location --output none
    Ok "created"
}

# ── 6. The container app ─────────────────────────────────────────────────────
# Note what is NOT here: no password, no connection secret, no registry
# credential.
#
# On authentication mode: "Active Directory Managed Identity" is used rather
# than "Active Directory Default". The Default chain is convenient locally, but
# inside a container it does not reliably resolve a system-assigned identity and
# fails with a bare "Login failed ... State:240". Being explicit removes the
# ambiguity - this container has exactly one identity and this names it.

$connectionString = "Server=$SqlServer,1433;Database=$SqlDatabase;" +
                    "Authentication=Active Directory Managed Identity;" +
                    "Encrypt=Yes;TrustServerCertificate=No;"

Step "Container app: $AppName"
$app = az containerapp show --name $AppName --resource-group $ResourceGroup --output json 2>$null | ConvertFrom-Json

if ($app) {
    Ok "already exists, updating to the new image"
    az containerapp update --name $AppName --resource-group $ResourceGroup `
        --image "$loginServer/sql-mcp:$tag" `
        --set-env-vars "MSSQL_CONNECTION_STRING=$connectionString" `
        --output none
} else {
    az containerapp create --name $AppName --resource-group $ResourceGroup `
        --environment $EnvName `
        --image "$loginServer/sql-mcp:$tag" `
        --registry-server $loginServer `
        --registry-identity system `
        --system-assigned `
        --target-port 5000 `
        --ingress external `
        --min-replicas 1 --max-replicas 3 `
        --cpu 0.5 --memory 1.0Gi `
        --env-vars "MSSQL_CONNECTION_STRING=$connectionString" `
        --output none
    Ok "created with a system-assigned managed identity"
}

# The app pulls from ACR using its own identity rather than a registry password,
# so grant it AcrPull. Safe to re-run.
$principalId = az containerapp identity show --name $AppName --resource-group $ResourceGroup --query principalId --output tsv
$acrId       = az acr show --name $AcrName --resource-group $ResourceGroup --query id --output tsv
az role assignment create --assignee $principalId --role AcrPull --scope $acrId --output none 2>$null
Ok "granted AcrPull to the app identity"

$fqdn = az containerapp show --name $AppName --resource-group $ResourceGroup `
    --query "properties.configuration.ingress.fqdn" --output tsv

# ── Done ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host " Deployed" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  MCP endpoint   https://$fqdn/mcp"
Write-Host "  Health         https://$fqdn/health"
Write-Host "  Identity       $AppName  (principal $principalId)"
Write-Host ""
Write-Host "ONE STEP REMAINS. The container cannot read your database yet." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Part 1 - the SQL grant" -ForegroundColor Cyan
Write-Host "    Open deploy/grant-managed-identity.sql, replace"
Write-Host "    <MANAGED-IDENTITY-NAME> with:  $AppName"
Write-Host "    Run it against $SqlDatabase as a database admin."
Write-Host ""
Write-Host "  Part 2 - the Fabric item permission  (Fabric SQL Database only)" -ForegroundColor Cyan
Write-Host "    Fabric has a permission layer above SQL. Without this the"
Write-Host "    container authenticates and is then refused with"
Write-Host "    'Verify the user has the Read item permission'."
Write-Host ""
Write-Host "    Fabric portal -> your workspace -> Manage access -> Add people"
Write-Host "    or groups -> search '$AppName' -> Member"
Write-Host ""
Write-Host "    Then restart so it retries:"
Write-Host "      az containerapp revision restart --name $AppName \"
Write-Host "        --resource-group $ResourceGroup --revision <active-revision>"
Write-Host ""
Write-Host "    Skip part 2 for Azure SQL, SQL MI or SQL Server." -ForegroundColor DarkGray
Write-Host ""
Write-Host "Then confirm the tool surface:" -ForegroundColor Cyan
Write-Host "  python scripts/inspect_server.py --url https://$fqdn/mcp"
Write-Host ""
Write-Host "Watch the logs:"
Write-Host "  az containerapp logs show --name $AppName --resource-group $ResourceGroup --follow"
Write-Host ""
Write-Host "Remove everything:"
Write-Host "  az group delete --name $ResourceGroup --yes"
Write-Host ""
