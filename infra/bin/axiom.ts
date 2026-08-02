#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { StorageStack } from '../lib/storage-stack';

const app = new cdk.App();

const envName = app.node.tryGetContext('envName') ?? 'dev';

// Region note: this account has zero Bedrock token quota in us-east-1, so us-east-2 is not
// a preference here, it is a requirement. See docs/AWS-SETUP.md.
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'us-east-2',
};

new StorageStack(app, `Axiom-${envName}-Storage`, {
  env,
  envName,
  description: 'AXIOM storage foundation: source artifacts, derived output, job state',
  tags: {
    Project: 'axiom',
    Environment: envName,
    ManagedBy: 'cdk',
  },

  // Deploy with the CLI's own credentials instead of assuming bootstrap roles.
  //
  // The default synthesizer requires `cdk bootstrap`, which creates four IAM roles. Creating
  // them needs `iam:CreateRole` and friends, and the deploying principal here holds
  // `PowerUserAccess` — which grants everything *except* `iam:*`. So bootstrap cannot
  // succeed without escalating that principal to near-administrator.
  //
  // This stack does not need any of it. It declares S3, DynamoDB and SQS resources and
  // **no assets** — no Lambda bundles, no container images — so there is nothing to publish
  // to a staging bucket and no need for a privileged deployment role. Escalating privileges
  // to create roles that would then go unused is the wrong trade.
  //
  // When compute lands (Tier 2 adds Lambda), this stack will start producing assets and will
  // need a real bootstrap. At that point the deploying principal needs IAM write access,
  // scoped to `cdk-hnb659fds-*` — see docs/AWS-SETUP.md, which carries the exact policy.
  //
  // The staging bucket is still required even with no application assets, because the CLI
  // always publishes the synthesized template to S3 rather than inlining it. That bucket is
  // a plain bucket created by `scripts/bootstrap_lite.ps1` — no roles, no SSM version
  // parameter, nothing that needs `iam:*`.
  synthesizer: new cdk.CliCredentialsStackSynthesizer({
    fileAssetsBucketName: `axiom-cdk-assets-${process.env.CDK_DEFAULT_ACCOUNT}-${
      process.env.CDK_DEFAULT_REGION ?? 'us-east-2'
    }`,
  }),
});

app.synth();
