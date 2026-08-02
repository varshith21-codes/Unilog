import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';

/**
 * Storage foundation for the AXIOM pipeline.
 *
 * Three ideas drive the choices here, all from blueprint Part 6:
 *
 * 1. **Raw bytes are kept forever, versioned and immutable.** Provenance is worthless if
 *    you cannot reopen the exact document a citation points at. Retaining the original
 *    bytes is what makes an Enrichment Certificate auditable two years later.
 * 2. **Idempotency is a storage concern, not an application concern.** The jobs table keys
 *    on (artifactHash, schemaVersion, promptVersion) so a retry cannot double-process and
 *    a prompt change *can* legitimately reprocess.
 * 3. **A poison document must not stall a supplier.** Every queue has a dead-letter queue
 *    so one malformed 900-page PDF is isolated rather than blocking the batch behind it.
 */
export interface StorageStackProps extends cdk.StackProps {
  /** Short environment name, e.g. 'dev'. Used in resource names. */
  readonly envName: string;
}

export class StorageStack extends cdk.Stack {
  public readonly landingBucket: s3.Bucket;
  public readonly artifactBucket: s3.Bucket;
  public readonly jobsTable: dynamodb.Table;
  public readonly ingestQueue: sqs.Queue;
  public readonly ingestDlq: sqs.Queue;

  constructor(scope: Construct, id: string, props: StorageStackProps) {
    super(scope, id, props);

    const { envName } = props;

    // Landing zone for source artifacts: supplier files, datasheets, crawled pages.
    //
    // Versioned because a supplier reissuing a datasheet under the same filename must not
    // destroy the version a published citation refers to. This is the single most important
    // storage decision in the system.
    //
    // NOTE — no `eventBridgeEnabled` yet, deliberately. Turning it on makes CDK synthesize a
    // `Custom::S3BucketNotifications` resource backed by a Lambda and its own IAM role, and
    // right now **nothing consumes those events**: the pipeline is driven by
    // `scripts/run_pipeline.py`, not by uploads. So it would buy an IAM role and a function
    // in exchange for firing events into the void. It goes back on in Tier 2, together with
    // the ingest Lambda that reads `ingestQueue` — at which point this stack has real assets
    // and needs a proper `cdk bootstrap` regardless. See docs/AWS-SETUP.md.
    this.landingBucket = new s3.Bucket(this, 'LandingBucket', {
      bucketName: `axiom-${envName}-landing-${this.account}`,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        {
          // Source documents are read heavily during enrichment and rarely afterwards.
          id: 'tier-cold-sources',
          transitions: [
            {
              storageClass: s3.StorageClass.INTELLIGENT_TIERING,
              transitionAfter: cdk.Duration.days(30),
            },
          ],
          // Old versions are kept, just cheaply. Never expired: see the note above.
          noncurrentVersionTransitions: [
            {
              storageClass: s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
              transitionAfter: cdk.Duration.days(90),
            },
          ],
        },
        { id: 'abort-stale-multipart', abortIncompleteMultipartUploadAfter: cdk.Duration.days(7) },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Derived artifacts: page renders for the evidence viewer, thumbnails, channel exports,
    // certificates. Regenerable, so this bucket is not versioned and may expire old output.
    this.artifactBucket = new s3.Bucket(this, 'ArtifactBucket', {
      bucketName: `axiom-${envName}-artifacts-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        { id: 'expire-old-exports', prefix: 'exports/', expiration: cdk.Duration.days(90) },
        { id: 'abort-stale-multipart', abortIncompleteMultipartUploadAfter: cdk.Duration.days(7) },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Pipeline job state and the idempotency ledger.
    //
    // Partition key is the idempotency key: hashing the artifact together with the schema
    // and prompt versions means a retry is a no-op, while a genuine prompt change produces
    // a different key and is allowed to reprocess. That distinction is what makes the
    // pipeline safely retryable without blocking improvement.
    this.jobsTable = new dynamodb.Table(this, 'JobsTable', {
      tableName: `axiom-${envName}-jobs`,
      partitionKey: { name: 'idempotencyKey', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'stage', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
      timeToLiveAttribute: 'expiresAt',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Query pipeline state by SKU, which is what the review workspace needs.
    this.jobsTable.addGlobalSecondaryIndex({
      indexName: 'bySku',
      partitionKey: { name: 'sku', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'updatedAt', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Find everything currently queued for human review, ordered by priority.
    this.jobsTable.addGlobalSecondaryIndex({
      indexName: 'byStatus',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'priority', type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.ingestDlq = new sqs.Queue(this, 'IngestDlq', {
      queueName: `axiom-${envName}-ingest-dlq`,
      enforceSSL: true,
      // Long retention: a dead letter is a bug report, and 14 days is time to read it.
      retentionPeriod: cdk.Duration.days(14),
    });

    this.ingestQueue = new sqs.Queue(this, 'IngestQueue', {
      queueName: `axiom-${envName}-ingest`,
      enforceSSL: true,
      // Generous: document parsing on a large PDF is slow, and a redelivery mid-parse
      // wastes the work already done.
      visibilityTimeout: cdk.Duration.minutes(15),
      retentionPeriod: cdk.Duration.days(4),
      deadLetterQueue: { queue: this.ingestDlq, maxReceiveCount: 3 },
    });

    new cdk.CfnOutput(this, 'LandingBucketName', { value: this.landingBucket.bucketName });
    new cdk.CfnOutput(this, 'ArtifactBucketName', { value: this.artifactBucket.bucketName });
    new cdk.CfnOutput(this, 'JobsTableName', { value: this.jobsTable.tableName });
    new cdk.CfnOutput(this, 'IngestQueueUrl', { value: this.ingestQueue.queueUrl });
  }
}
