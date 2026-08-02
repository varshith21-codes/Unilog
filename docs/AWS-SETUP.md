# AWS setup for AXIOM

Target account: **860510875713** · Region: **us-east-2 (Ohio)** · Profile: **`axiom`**

AWS CLI v2.36.14 is already installed at `C:\Program Files\Amazon\AWSCLIV2\aws.exe`.

> **Region matters more than you would expect.** This account has **zero** Bedrock
> tokens-per-day quota in `us-east-1` and generous quota in `us-east-2`. Every model call in
> `us-east-1` fails with `ThrottlingException: Too many tokens per day` even on a first
> request. Do not "fix" that by retrying — change region. See
> [Bedrock model access](#step-5--enable-bedrock-model-access) below.

---

## Why not `aws login`?

`aws login` uses a browser round-trip: the CLI opens a local listener on a random port
(`127.0.0.1:<port>/oauth/callback`), sends you to the AWS sign-in page, and waits for the
browser to redirect back. Two failure modes make it unreliable for a long build session:

1. **The pending authorization expires after a few minutes.** Once it does, the local
   listener shuts down and the browser tab spins forever, because there is nothing left to
   receive the callback. Reopening the same URL cannot work — the port is closed.
2. **The session is short-lived.** Even on success it expires and interrupts work
   mid-deploy.

For a multi-day build, use a named profile backed by IAM access keys instead.

---

## Recommended: IAM user with a named profile

### Step 1 — Create the IAM user (console, one time)

1. Sign in to the console at <https://console.aws.amazon.com/> for account `860510875713`.
2. Go to **IAM → Users → Create user**. Name it `axiom-dev`.
3. Do **not** grant console access — this user is for programmatic use only.
4. On permissions, choose **Attach policies directly** and attach `PowerUserAccess`.
   - `PowerUserAccess` covers Bedrock, S3, DynamoDB, SQS, Step Functions, Lambda,
     CloudFormation and the rest of the build surface.
   - It deliberately grants **no `iam:*` permissions** at all. That is the whole point of it,
     and it is also why `cdk bootstrap` does not work with this user — see
     [CDK deployment without bootstrap](#cdk-deployment-without-bootstrap) below, which is the
     path this project actually uses.
   - Do **not** add `IAMFullAccess` unless you have read that section and decided you need it.
     `iam:*` on a long-lived access key sitting in `~/.aws/credentials` is equivalent to
     administrator, because that key can mint itself an admin role and assume it.
5. Create the user, then open it → **Security credentials → Create access key**.
6. Choose **Command Line Interface (CLI)**, acknowledge the warning, and create.
7. Leave the "Retrieve access keys" page open for the next step.

### Step 2 — Configure the profile (you run this, in your own terminal)

Run this yourself and paste the values at the prompts. **Do not paste access keys into
chat** — they only need to reach your local credentials file.

```powershell
aws configure --profile axiom
```

Answer:

```
AWS Access Key ID     : <paste from the console>
AWS Secret Access Key : <paste from the console>
Default region name   : us-east-2
Default output format : json
```

### Step 3 — Make it the default for this project

```powershell
# current terminal only
$env:AWS_PROFILE = "axiom"

# persist for future terminals
setx AWS_PROFILE axiom
```

### Step 4 — Verify

```powershell
aws sts get-caller-identity --profile axiom
```

Expected shape:

```json
{
  "UserId": "AIDA...",
  "Account": "860510875713",
  "Arn": "arn:aws:iam::860510875713:user/axiom-dev"
}
```

---

## Step 5 — Enable Bedrock model access

Model access is per-account and per-region, and it is **not** granted by default.

### Just run the preflight

```powershell
$env:AWS_PROFILE = "axiom"
.\.venv\Scripts\python.exe scripts\preflight_bedrock.py --region us-east-2 --write
```

The script attempts a real `Converse` call against every candidate in every cascade tier,
tries both the bare model ID and the geo-prefixed inference-profile form (`us.<id>`), and
writes the winners to `packages/axiom/config/models.yaml`. **Listing models proves
nothing** — `list-foundation-models` returns the whole catalogue regardless of entitlement,
which is exactly how you waste an afternoon. Only an invocation is evidence.

Current resolved state in `us-east-2`:

| Tier | Model | Probe latency |
|---|---|---|
| micro / volume | `zai.glm-4.7-flash` | ~0.5 s |
| mid | `zai.glm-4.7` | ~0.5 s |
| frontier | `zai.glm-5` | ~0.5 s |
| vision | `qwen.qwen3-vl-235b-a22b` | ~0.6 s |
| embedding | `amazon.titan-embed-text-v2:0` (dim 1024) | ~0.45 s |

### Why the open-weight models rather than Claude

Anthropic models on Bedrock are billed through an **AWS Marketplace subscription**, which
requires a valid payment instrument on the account. On this account they fail with:

```
AccessDeniedException: Model access is denied due to INVALID_PAYMENT_INSTRUMENT:
A valid payment instrument must be provided. Your AWS Marketplace subscription for
this model cannot be completed...
```

The Chinese-lab open-weight families — Z.ai GLM, MiniMax, Qwen, DeepSeek, Moonshot Kimi —
are served directly by Bedrock with no Marketplace subscription, and they have very large
daily token quotas in `us-east-2` (GLM-4.7, GLM-4.7 Flash, MiniMax M2 and M2.5 each show
5.4 B tokens/day). That makes them the pragmatic primary path.

To switch to Anthropic later, attach a payment method in **Billing → Payment preferences**
and re-run the preflight. The candidate lists already include Anthropic as a fallback, so
the cascade upgrades itself with no code change.

### Manual console route, if you prefer

1. Console → **Amazon Bedrock**, region **us-east-2** → **Model access**.
2. **Modify model access**, enable the GLM / MiniMax / Qwen / DeepSeek families plus
   **Titan Text Embeddings V2**, and submit.

> Never hard-code model IDs from memory — the catalogue changes, and IDs differ per region.
> `packages/axiom/config/models.yaml` is generated, and the header says so.

---

## Security notes

**The existing `~/.aws/config` points at the account root user**
(`login_session = arn:aws:iam::860510875713:root`). Avoid building against root:

- Root permissions cannot be scoped down. A leaked root key compromises the entire account,
  including billing and account closure.
- AWS explicitly discourages root access keys.
- Root sessions from `aws login` expire and will interrupt long-running deploys.

Use the `axiom-dev` IAM user above. Also worth doing once, in the console:

- Enable MFA on the root user, then stop using it.
- Set a **Billing budget with an alert** before running any Bedrock workload. A misconfigured
  batch job is the most common way to get a surprise bill.

### Least privilege, later

`PowerUserAccess` is a pragmatic build-phase choice, not a production one. Before any real
deployment, replace it with a scoped policy limited to the services actually in use (Bedrock,
S3, DynamoDB, SQS, Step Functions, Lambda, CloudFormation, Logs) and constrained to this
project's resource ARNs.

### Key hygiene

- Access keys live in `%USERPROFILE%\.aws\credentials`. That file is outside the repo and
  must stay that way.
- `.gitignore` already excludes `.env*` and `*.pem`. Never commit credentials.
- Rotate or delete the `axiom-dev` key when the hackathon ends.

---

## CDK deployment without bootstrap

### What goes wrong, and why

`npx cdk bootstrap` against this account fails, repeatedly and confusingly:

```
CDKToolkit | CREATE_FAILED | AWS::IAM::Role | FilePublishingRole
  User: arn:aws:iam::860510875713:user/axiom-dev is not authorized to perform:
  iam:GetRole on resource: role cdk-hnb659fds-file-publishing-role-...
```

Bootstrap's job is to create four IAM roles, an S3 staging bucket, an ECR repository and an SSM
version parameter. `PowerUserAccess` grants every service **except** IAM, so all four roles
fail. Worse, the rollback then also fails — deleting a role needs `iam:DeleteRole`, which is
equally denied — leaving `CDKToolkit` in `ROLLBACK_FAILED`, a terminal state that blocks every
later attempt.

If you hit this, clear the wreckage before trying anything else:

```powershell
$env:AWS_PROFILE = "axiom"

# ROLLBACK_FAILED must first be pushed to DELETE_FAILED
aws cloudformation delete-stack --stack-name CDKToolkit --region us-east-2

# then delete again, skipping the roles CloudFormation cannot verify or remove
aws cloudformation delete-stack --stack-name CDKToolkit --region us-east-2 `
  --retain-resources CloudFormationExecutionRole FilePublishingRole `
                     ImagePublishingRole LookupRole StagingBucket

aws cloudformation describe-stacks --stack-name CDKToolkit --region us-east-2
# expect: Stack with id CDKToolkit does not exist
```

The retained roles are safe to skip because they were never successfully created — the
`iam:GetRole` denial happened during creation, so there is nothing orphaned. Confirm with
`aws s3api list-buckets` that no `cdk-hnb659fds-*` bucket was left behind.

### The approach this project uses

The storage stack declares S3, DynamoDB and SQS resources and **no IAM resources**, so it does
not need a privileged deployment role at all. `infra/bin/axiom.ts` therefore uses
`CliCredentialsStackSynthesizer`, which deploys with the CLI's own credentials instead of
assuming bootstrap roles.

One thing is still required: the CDK CLI always publishes the synthesized template to S3 rather
than inlining it, even for a small template. So it needs a staging bucket — just a bucket, with
none of the roles. `scripts/bootstrap_lite.ps1` creates exactly that, and is idempotent:

```powershell
./scripts/bootstrap_lite.ps1

cd infra
$env:AWS_PROFILE = "axiom"
$env:CDK_DEFAULT_ACCOUNT = "860510875713"
$env:CDK_DEFAULT_REGION = "us-east-2"
npx cdk deploy --all
```

`CDK_DEFAULT_ACCOUNT` and `CDK_DEFAULT_REGION` are not optional here — the synthesizer builds
the staging bucket name from them.

### Deployed resources

| Resource | Name | Notes |
|---|---|---|
| Landing bucket | `axiom-dev-landing-860510875713` | Versioned. A reissued datasheet must not destroy the version a citation points at. |
| Artifact bucket | `axiom-dev-artifacts-860510875713` | Derived output, regenerable, exports expire at 90 days. |
| Jobs table | `axiom-dev-jobs` | On-demand, PITR on, GSIs `bySku` and `byStatus`. |
| Ingest queue | `axiom-dev-ingest` | 15 min visibility, DLQ after 3 receives. |
| Ingest DLQ | `axiom-dev-ingest-dlq` | 14 day retention — a dead letter is a bug report. |

All buckets and the table are `RemovalPolicy.RETAIN`, so `cdk destroy` leaves the data. Delete
them by hand if you really mean it.

### When this stops being enough

Tier 2 adds Lambda. At that point the stack declares its own IAM roles and produces real code
assets, and a proper bootstrap becomes necessary. That needs IAM write access on the deploying
principal — but it can be scoped to the CDK role name pattern rather than granted as
`IAMFullAccess`. Attach this as a customer-managed policy, as root, and detach it once
bootstrap has run:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CdkBootstrapRolesOnly",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies"
      ],
      "Resource": "arn:aws:iam::860510875713:role/cdk-hnb659fds-*"
    },
    {
      "Sid": "PassExecRoleToCloudFormation",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::860510875713:role/cdk-hnb659fds-cfn-exec-role-*",
      "Condition": {
        "StringEquals": { "iam:PassedToService": "cloudformation.amazonaws.com" }
      }
    }
  ]
}
```

This is narrower than `IAMFullAccess` in the way that matters: it cannot create a role outside
the `cdk-hnb659fds-*` namespace, so the key cannot mint itself an administrator. Note the
remaining caveat — the bootstrap `cfn-exec-role` is created with `AdministratorAccess` by
default, and anything CloudFormation deploys runs as that role. Use
`--cloudformation-execution-policies` to narrow it if that matters for your account.

---

## Cost guardrails to set up front

```powershell
# confirm which region you are billing into
aws configure get region --profile axiom
```

In the console:

1. **Billing → Budgets → Create budget.** A monthly cost budget with an alert at 50% / 80% /
   100% of whatever you are willing to spend.
2 . **Bedrock → Model invocation logging.** Send invocation logs to CloudWatch so token spend
   is attributable per pipeline stage. This is what feeds the cost-per-SKU meter described in
   blueprint Part 9.4.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ThrottlingException: Too many tokens per day` on the very first call | the region has **zero** quota for that model, not a rate limit | change region. Check with `aws service-quotas list-service-quotas --service-code bedrock --region <r> --query "Quotas[?Value>\`0\`]"` — if that is empty, the region is unusable |
| `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT` | Marketplace-billed model (Anthropic) with no valid payment method on the account | use the open-weight tiers, or add a payment method in Billing → Payment preferences |
| `ValidationException` naming an inference profile | model is only reachable cross-region | prefix the ID with `us.` — the preflight script tries this automatically |
| `Your session has expired. Please reauthenticate using 'aws login'` | the cached root login token expired | switch to the `axiom` profile above |
| Browser tab spins forever on the sign-in URL | the CLI's local callback listener already timed out and closed | use access keys instead; the devtools sign-in flow is fragile |
| `AccessDeniedException` on `bedrock-runtime` | model access not enabled in this region | Step 5 above |
| `UnrecognizedClientException` | wrong or inactive access key | recreate the key, re-run `aws configure --profile axiom` |
| `aws` not recognised in a new terminal | PATH not refreshed | open a fresh terminal, or use the full path `C:\Program Files\Amazon\AWSCLIV2\aws.exe` |
| CDK deploy fails on role creation | missing `IAMFullAccess` | attach it, or run `cdk bootstrap` with a more privileged principal |
