<#
.SYNOPSIS
    Create the S3 staging bucket the CDK CLI needs, without a full `cdk bootstrap`.

.DESCRIPTION
    `cdk bootstrap` creates four IAM roles and an SSM version parameter alongside the staging
    bucket. Creating those roles needs `iam:CreateRole`, `iam:AttachRolePolicy` and friends.

    The deploying principal for this project (`axiom-dev`) holds `PowerUserAccess`, which
    grants everything *except* `iam:*`. So a normal bootstrap fails — and granting the IAM
    permissions to fix it would make a long-lived laptop access key effectively an
    administrator, because `iam:*` lets that key mint itself an admin role.

    The storage stack does not need any of that. It declares S3, DynamoDB and SQS resources
    and no IAM resources, so it can be deployed with the CLI's own credentials via
    `CliCredentialsStackSynthesizer` (see infra/bin/axiom.ts). The one thing still required is
    a bucket to publish the synthesized template to, because the CDK CLI always uploads the
    template rather than inlining it. This script creates exactly that bucket and nothing else.

    Idempotent: re-running against an existing bucket is a no-op.

.NOTES
    This is a deliberate stopgap, not a permanent answer. When Tier 2 adds Lambda, the stack
    will declare IAM roles of its own and will need a real bootstrap. The scoped IAM policy
    for that is in docs/AWS-SETUP.md.

.EXAMPLE
    ./scripts/bootstrap_lite.ps1
    ./scripts/bootstrap_lite.ps1 -Region us-west-2 -Profile other
#>
[CmdletBinding()]
param(
    [string]$Profile = 'axiom',
    [string]$Region = 'us-east-2',
    [string]$AccountId
)

$ErrorActionPreference = 'Stop'
$env:AWS_PROFILE = $Profile
$env:AWS_PAGER = ''

if (-not $AccountId) {
    $AccountId = (aws sts get-caller-identity --query Account --output text).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $AccountId) {
        throw "could not resolve the account id; is the '$Profile' profile configured?"
    }
}

$bucket = "axiom-cdk-assets-$AccountId-$Region"
Write-Host "staging bucket: $bucket" -ForegroundColor Cyan

# --- create, unless it already exists ---------------------------------------------------
aws s3api head-bucket --bucket $bucket 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  already exists, leaving it alone" -ForegroundColor Yellow
}
else {
    # us-east-1 is the one region that rejects an explicit LocationConstraint.
    if ($Region -eq 'us-east-1') {
        aws s3api create-bucket --bucket $bucket --region $Region | Out-Null
    }
    else {
        aws s3api create-bucket --bucket $bucket --region $Region `
            --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw "failed to create $bucket" }
    Write-Host "  created" -ForegroundColor Green
}

# --- lock it down ------------------------------------------------------------------------
# Applied every run, not just on create, so a bucket that drifted gets corrected.

aws s3api put-public-access-block --bucket $bucket --public-access-block-configuration `
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to block public access on $bucket" }
Write-Host "  public access blocked" -ForegroundColor Green

# JSON goes via a temp file rather than an inline argument. PowerShell strips the double quotes
# when it hands a string to a native command, so `--x '{"a":1}'` arrives as `{a:1}` and the CLI
# rejects it. `file://` sidesteps the quoting rules entirely.
function Set-BucketJson {
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][string]$Json,
        [Parameter(Mandatory)][scriptblock]$Invoke
    )
    $file = New-TemporaryFile
    try {
        # ASCII, because the CLI chokes on a UTF-8 BOM in a --cli-input/file:// payload.
        [System.IO.File]::WriteAllText($file.FullName, $Json, [System.Text.Encoding]::ASCII)
        & $Invoke "file://$($file.FullName -replace '\\', '/')" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to set $Description on $bucket" }
        Write-Host "  $Description" -ForegroundColor Green
    }
    finally {
        Remove-Item $file.FullName -Force -ErrorAction SilentlyContinue
    }
}

Set-BucketJson -Description 'encryption enabled (AES256)' `
    -Json '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' `
    -Invoke { param($payload)
        aws s3api put-bucket-encryption --bucket $bucket `
            --server-side-encryption-configuration $payload
    }

# Templates are disposable build output; there is no reason to pay to keep old ones.
Set-BucketJson -Description 'lifecycle set (templates expire after 30 days)' `
    -Json '{"Rules":[{"ID":"expire-old-templates","Status":"Enabled","Filter":{"Prefix":""},"Expiration":{"Days":30},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}' `
    -Invoke { param($payload)
        aws s3api put-bucket-lifecycle-configuration --bucket $bucket `
            --lifecycle-configuration $payload
    }

Write-Host ""
Write-Host "ready. deploy with:" -ForegroundColor Cyan
Write-Host "  cd infra" -ForegroundColor White
Write-Host "  `$env:AWS_PROFILE = '$Profile'" -ForegroundColor White
Write-Host "  `$env:CDK_DEFAULT_ACCOUNT = '$AccountId'" -ForegroundColor White
Write-Host "  `$env:CDK_DEFAULT_REGION = '$Region'" -ForegroundColor White
Write-Host "  npx cdk deploy --all" -ForegroundColor White
