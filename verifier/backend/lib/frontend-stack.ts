import { CfnOutput, RemovalPolicy, Stack, StackProps, } from 'aws-cdk-lib';
import { CfnCloudFrontOriginAccessIdentity, CfnDistribution, Distribution, OriginAccessIdentity,ViewerProtocolPolicy } from 'aws-cdk-lib/aws-cloudfront';
import { S3Origin } from 'aws-cdk-lib/aws-cloudfront-origins';
import { Bucket, CfnBucket, ObjectOwnership } from 'aws-cdk-lib/aws-s3';
import { BucketDeployment, Source } from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';
import { Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';

export interface ClientStackProps extends StackProps {
  appName: string;
}

export class FrontendStack extends Stack {
  public clientBucket: Bucket;
  public clientDistribution: Distribution;
  public clientDeployment: BucketDeployment;
  serverAccessLoggingBucket: Bucket;


  constructor(scope: Construct, id: string, props?: ClientStackProps) {
    super(scope, id, props);

    this.createClientAssetBucket();
    this.deployClient();
    this.createClientAssetDistribution();


    new CfnOutput(this, "ClientDistributionOutput", {
      value: this.clientDistribution.distributionDomainName
    });

  }

  private deployClient() {
    const deploymentRole = new Role(this, 'ClientDeploymentRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
    });

    this.clientDeployment = new BucketDeployment(this, 'ClientDeployment', {
      sources: [Source.asset('../client/dist')],
      destinationBucket: this.clientBucket,
      role: deploymentRole
    });

  }

  private createClientAssetBucket() {
    this.serverAccessLoggingBucket = new Bucket(this, 'AppClientServerLoggingBucket', {
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      objectOwnership: ObjectOwnership.OBJECT_WRITER
    });
    (this.serverAccessLoggingBucket.node.defaultChild as CfnBucket).overrideLogicalId('AppClientServerLoggingBucket');
    this.clientBucket = new Bucket(this, 'ClientBucket', {
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      serverAccessLogsBucket: this.serverAccessLoggingBucket,
      serverAccessLogsPrefix: 'clientBucketAccessLogs',
      enforceSSL: true,
    });
    (this.clientBucket.node.defaultChild as CfnBucket).overrideLogicalId('ClientBucket');
  }

  private createClientAssetDistribution() {
    const originAccessIdentity = new OriginAccessIdentity(this, 'ClientOriginAccessIdentity');
    (originAccessIdentity.node.defaultChild as CfnCloudFrontOriginAccessIdentity).overrideLogicalId('ClientOriginAccessIdentity');
    this.clientBucket.grantRead(originAccessIdentity);
    this.serverAccessLoggingBucket.grantReadWrite(originAccessIdentity);

    this.clientDistribution = new Distribution(this, 'ClientDistribution', {
      enableLogging: true,
      logBucket: this.serverAccessLoggingBucket,
      logFilePrefix: 'clientDistributionAccessLogs',
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 404,
          responsePagePath: '/index.html'
        }
      ],
      defaultBehavior: {
        origin: new S3Origin(this.clientBucket, { originAccessIdentity }),
        viewerProtocolPolicy: ViewerProtocolPolicy.HTTPS_ONLY,
      },
    });
    (this.clientDistribution.node.defaultChild as CfnDistribution).overrideLogicalId('ClientDistribution');
  }
}
