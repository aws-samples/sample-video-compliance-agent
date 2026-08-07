import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as logs from 'aws-cdk-lib/aws-logs';
import { DynamoEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';

export interface BackendStackProps extends StackProps {
  appName: string;
}

export class BackendStack extends cdk.Stack {
  private appName: string;
  private generatorBucket: s3.IBucket;
  private outputBucket: s3.Bucket;
  private table: dynamodb.Table;
  private dependencyLayer: lambda.LayerVersion;
  private complianceCheckerLayer: lambda.LayerVersion;
  private stateMachine: sfn.StateMachine;

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props);

    this.appName = props.appName;
    
    const generatorBucketName = this.node.tryGetContext('generatorMetadataBucket');
    if (!generatorBucketName) {
      throw new Error('generatorMetadataBucket context parameter is required');
    }
    
    this.generatorBucket = s3.Bucket.fromBucketName(this, 'GeneratorBucket', generatorBucketName);
    
    this.createInfrastructure();
    this.createLambdaLayers();
    const lambdas = this.createLambdaFunctions();
    this.createStateMachine(lambdas);
    this.createTriggerLambda(lambdas);
  }

  private createInfrastructure() {
    this.outputBucket = new s3.Bucket(this, 'OutputBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Grant Bedrock Data Automation service access to the output bucket
    this.outputBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowBedrockDataAutomationAccess',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('bedrock.amazonaws.com')],
        actions: ['s3:*'],
        resources: [
          this.outputBucket.bucketArn,
          `${this.outputBucket.bucketArn}/*`,
        ],
        conditions: {
          StringEquals: {
            'aws:SourceAccount': cdk.Stack.of(this).account,
          },
        },
      })
    );

    this.table = new dynamodb.Table(this, 'Table', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
  }

  private createLambdaLayers() {
    this.dependencyLayer = new lambda.LayerVersion(this, 'DependencyLayer', {
      code: lambda.Code.fromAsset('lambda/layers/dependencies', {
        bundling: {
          image: cdk.DockerImage.fromRegistry('public.ecr.aws/sam/build-python3.11'),
          platform: 'linux/amd64',
          command: ['bash', '-c', 'pip install -r requirements.txt -t /asset-output/python --platform manylinux2014_x86_64 --only-binary=:all:'],
        },
      }),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_11],
    });

    this.complianceCheckerLayer = new lambda.LayerVersion(this, 'ComplianceCheckerLayer', {
      code: lambda.Code.fromAsset('lambda/layers/compliance_checker_deps', {
        bundling: {
          image: cdk.DockerImage.fromRegistry('public.ecr.aws/sam/build-python3.11'),
          platform: 'linux/amd64',
          command: ['bash', '-c', 'pip install -r requirements.txt -t /asset-output/python --platform manylinux2014_x86_64 --only-binary=:all:'],
        },
      }),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_11],
    });
  }

  private createLambdaFunctions() {
    const metadataProcessor = this.createLambda('MetadataProcessor', 'metadata-processor', 'function.lambda_handler', {
      TABLE_NAME: this.table.tableName,
      INPUT_BUCKET_NAME: this.generatorBucket.bucketName,
      OUTPUT_BUCKET_NAME: this.outputBucket.bucketName,
    }, [this.dependencyLayer]);

    const complianceFeeder = this.createLambda('ComplianceFeeder', 'compliance_feeder', 'function.lambda_handler', {
      TABLE_NAME: this.table.tableName,
      BUCKET_NAME: this.generatorBucket.bucketName,
    }, [this.dependencyLayer]);

    const processorFeeder = this.createLambda('ProcessorFeeder', 'processor_feeder', 'function.lambda_handler', {
      TABLE_NAME: this.table.tableName,
      BUCKET_NAME: this.generatorBucket.bucketName,
    }, [this.dependencyLayer]);

    const reportCompiler = this.createLambda('ReportCompiler', 'report_complier', 'function.lambda_handler', {
      TABLE_NAME: this.table.tableName,
      BUCKET_NAME: this.outputBucket.bucketName,
      GENERATOR_BUCKET_NAME: this.generatorBucket.bucketName,
    }, [this.dependencyLayer]);

    const listVideos = this.createLambda('ListVideos', 'list_video_step', 'handler.lambda_handler', {
      BUCKET_NAME: this.outputBucket.bucketName,
    }, []);

    const complianceChecker = this.createLambda('ComplianceChecker', 'compliance-checker', 'compliance_checker.lambda_handler', {
      TABLE_NAME: this.table.tableName,
      BUCKET_NAME: this.generatorBucket.bucketName,
      // MODEL_ID: 'us.amazon.nova-2-lite-v1:0',  // Optional: overrides the Python default if set
    }, [this.complianceCheckerLayer], 15);

    complianceChecker.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:*'],
      resources: ['*'],
    }));

    [complianceFeeder, processorFeeder].forEach(fn => {
      this.generatorBucket.grantRead(fn);
    });

    [metadataProcessor, complianceChecker, reportCompiler].forEach(fn => {
      this.generatorBucket.grantReadWrite(fn);
    });

    [metadataProcessor, reportCompiler, complianceChecker].forEach(fn => {
      this.outputBucket.grantReadWrite(fn);
    });

    [metadataProcessor, complianceFeeder, processorFeeder, reportCompiler, complianceChecker].forEach(fn => {
      this.table.grantReadWriteData(fn);
    });

    return { metadataProcessor, complianceFeeder, processorFeeder, reportCompiler, listVideos, complianceChecker };
  }

  private createLambda(
    id: string,
    assetPath: string,
    handler: string,
    environment: Record<string, string>,
    layers: lambda.LayerVersion[],
    timeoutMinutes: number = 10
  ): lambda.Function {
    return new lambda.Function(this, id, {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler,
      code: lambda.Code.fromAsset(`lambda/${assetPath}`),
      layers,
      timeout: cdk.Duration.minutes(timeoutMinutes),
      memorySize: 2048,
      environment,
    });
  }

  private createStateMachine(lambdas: any) {
    const processMetaMap = new sfn.Map(this, 'ProcessMetaMap', {
      maxConcurrency: 20,
      itemsPath: '$.processfeederResult.shot_index',
      resultPath: '$.shotsResults',
      itemSelector: {
        'shot_index.$': '$$.Map.Item.Value',
        'videoId.$': '$.videoDetails.id',
      },
    });

    processMetaMap.itemProcessor(new tasks.LambdaInvoke(this, 'ProcessMetadataShot', {
      lambdaFunction: lambdas.metadataProcessor,
      payloadResponseOnly: true,
      resultPath: '$.result',
    }));

    const processShots = new sfn.Map(this, 'ProcessShotsMap', {
      maxConcurrency: 10,
      itemsPath: '$.feederResult.s3Uris',
      resultPath: '$.shotsResults',
      itemSelector: {
        's3Uri.$': '$$.Map.Item.Value',
        'videoId.$': '$.videoDetails.id',
      },
    });

    const shotComplianceTask = new tasks.LambdaInvoke(this, 'ShotCompliance', {
      lambdaFunction: lambdas.complianceChecker,
      payloadResponseOnly: true,
      resultPath: '$.result',
    });

    shotComplianceTask.addRetry({
      errors: ['States.TaskFailed'],
      interval: cdk.Duration.seconds(30),
      maxAttempts: 5,
      backoffRate: 2,
    });

    processShots.itemProcessor(shotComplianceTask);

    const processingChain = new tasks.LambdaInvoke(this, 'ProcessFeederTask', {
      lambdaFunction: lambdas.processorFeeder,
      payloadResponseOnly: true,
      resultPath: '$.processfeederResult',
    })
      .next(processMetaMap)
      .next(new tasks.LambdaInvoke(this, 'ComplianceFeederTask', {
        lambdaFunction: lambdas.complianceFeeder,
        payloadResponseOnly: true,
        resultPath: '$.feederResult',
      }))
      .next(processShots)
      .next(new tasks.LambdaInvoke(this, 'CompileReportTask', {
        lambdaFunction: lambdas.reportCompiler,
        payloadResponseOnly: true,
        resultPath: '$.compileReportResult',
      }));

    const processVideosMap = new sfn.Map(this, 'ProcessVideosMap', {
      maxConcurrency: 10,
      itemsPath: '$.videosList.videos',
      resultPath: '$.processingResults',
      parameters: {
        'videoDetails.$': '$$.Map.Item.Value',
        'AWS_STEP_FUNCTIONS_STARTED_BY_EXECUTION_ID.$': '$$.Execution.Id',
      },
    });

    processVideosMap.itemProcessor(processingChain);

    const definition = sfn.Chain
      .start(new tasks.LambdaInvoke(this, 'ListVideosTask', {
        lambdaFunction: lambdas.listVideos,
        payloadResponseOnly: true,
        resultPath: '$.videosList',
      }))
      .next(processVideosMap);

    this.stateMachine = new sfn.StateMachine(this, 'VideoProcessingStateMachine', {
      definition,
      timeout: cdk.Duration.minutes(120),
      tracingEnabled: true,
      logs: {
        destination: new logs.LogGroup(this, 'StateMachineLogs'),
        level: sfn.LogLevel.ALL,
      },
    });

    this.table.grantReadWriteData(this.stateMachine);
    this.outputBucket.grantReadWrite(this.stateMachine);
  }

  private createTriggerLambda(lambdas: any) {
    const triggerLambda = new lambda.Function(this, 'TriggerVerifier', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'function.lambda_handler',
      code: lambda.Code.fromAsset('lambda/trigger-verifier'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        STATE_MACHINE_ARN: this.stateMachine.stateMachineArn,
        BUCKET_NAME: this.generatorBucket.bucketName,
      },
    });

    this.stateMachine.grantStartExecution(triggerLambda);

    const streamArn = this.node.tryGetContext('generatorStatusTableStreamArn');
    if (streamArn) {
      const generatorTable = dynamodb.Table.fromTableAttributes(this, 'GeneratorStatusTable', {
        tableStreamArn: streamArn,
        tableName: 'VideoProcessingStatusTable'
      });
      
      triggerLambda.addEventSource(new DynamoEventSource(generatorTable, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        retryAttempts: 3
      }));
    }
  }
}
