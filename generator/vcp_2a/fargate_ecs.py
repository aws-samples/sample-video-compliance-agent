#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_ecr_assets as ecr_assets,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_lambda as lambda_,
    aws_s3_notifications as s3n,
    Stack, CfnOutput, RemovalPolicy, Duration
)
from constructs import Construct

class VCPFargateStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Note: We must keep at least one public subnet for the NAT Gateway
        # but all our workloads will run in private subnets only
        vpc = ec2.Vpc(
            self, "VCPTaskVpc",
            max_azs=2,
            ip_addresses=ec2.IpAddresses.cidr("10.200.0.0/16"),
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",  
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=28
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24   
                )
            ]
        )

        # Create security group for ECS tasks
        ecs_task_sg = ec2.SecurityGroup(
            self, "ECSTaskSecurityGroup",
            vpc=vpc,
            description="Security group for ECS Fargate tasks",
            allow_all_outbound=True  # Allow all outbound traffic
        )

        # Create security group for VPC endpoints
        vpc_endpoint_sg = ec2.SecurityGroup(
            self, "VPCEndpointSecurityGroup",
            vpc=vpc,
            description="Security group for VPC endpoints"
        )

        # Allow inbound HTTPS from ECS tasks to VPC endpoints
        vpc_endpoint_sg.add_ingress_rule(
            peer=ecs_task_sg,
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from ECS tasks"
        )

        # ECR VPC Endpoints
        ecr_api_endpoint = ec2.InterfaceVpcEndpoint(
            self, "EcrApiEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.ECR,
            private_dns_enabled=True,
            security_groups=[vpc_endpoint_sg],
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        )

        ecr_dkr_endpoint = ec2.InterfaceVpcEndpoint(
            self, "EcrDkrEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            private_dns_enabled=True,
            security_groups=[vpc_endpoint_sg],
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        )
        
        # Add S3 Gateway Endpoint
        s3_endpoint = ec2.GatewayVpcEndpoint(
            self, "S3Endpoint",
            vpc=vpc,
            service=ec2.GatewayVpcEndpointAwsService.S3
        )

        # Add CloudWatch Logs VPC Endpoint (required for ECS container logging)
        logs_endpoint = ec2.InterfaceVpcEndpoint(
            self, "CloudWatchLogsEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            private_dns_enabled=True,
            security_groups=[vpc_endpoint_sg],
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        )

        # Add Bedrock Runtime VPC Endpoint (required for Bedrock API calls)
        bedrock_runtime_endpoint = ec2.InterfaceVpcEndpoint(
            self, "BedrockRuntimeEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            private_dns_enabled=True,
            security_groups=[vpc_endpoint_sg],
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        )

        # Create S3 bucket for metadata storage
        metadata_bucket = s3.Bucket(
            self, "MetadataBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # Grant Bedrock Data Automation service access to the S3 bucket
        metadata_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBedrockDataAutomationAccess",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("bedrock.amazonaws.com")],
                actions=["s3:*"],
                resources=[
                    metadata_bucket.bucket_arn,
                    f"{metadata_bucket.bucket_arn}/*"
                ],
                conditions={
                    "StringEquals": {
                        "aws:SourceAccount": self.account
                    }
                }
            )
        )

        # Create DynamoDB table to track video processing status
        video_status_table = dynamodb.Table(
            self, "VideoProcessingStatusTable",
            partition_key=dynamodb.Attribute(name="video_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            removal_policy=RemovalPolicy.DESTROY
        )

        # Create an ECS Cluster - Make sure to select only private subnets
        cluster = ecs.Cluster(
            self, "VCPTaskCluster",
            vpc=vpc,
            container_insights=True
        )

        # Create IAM roles
        execution_role = iam.Role(
            self, "VCPTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")
            ]
        )

        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["*"],
                resources=["*"]
            )
        )

        task_role = iam.Role(
            self, "VCPTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        
        # Grant permissions to task role
        video_status_table.grant_read_write_data(task_role)
        metadata_bucket.grant_read_write(task_role)
        metadata_bucket.grant_read_write(execution_role)

        # Grant Bedrock Data Automation permissions to task role
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["*"],
                resources=["*"]
            )
        )

        # Create a log group
        log_group = logs.LogGroup(
            self, "VCPTaskLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )

        # Build Docker images from local code for all three services
        video_processor_image = ecr_assets.DockerImageAsset(
            self, "VideoProcessorImage",
            directory="vcp_2a/images/video_processor",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        video_analyzer_image = ecr_assets.DockerImageAsset(
            self, "VideoAnalyzerImage",
            directory="vcp_2a/images/video_analyzer",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        image_analyzer_image = ecr_assets.DockerImageAsset(
            self, "ImageAnalyzerImage",
            directory="vcp_2a/images/image_analyzer",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        metadata_processor_image = ecr_assets.DockerImageAsset(
            self, "MetadataProcessorImage",
            directory="vcp_2a/images/metadata_processor",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        frame_selector_image = ecr_assets.DockerImageAsset(
            self, "FrameSelectorImage",
            directory="vcp_2a/images/frame_selector",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        # Create a Fargate task definition
        task_definition = ecs.FargateTaskDefinition(
            self, "VCPTaskDefinition",
            memory_limit_mib=16*1024,
            cpu=4*1024,
            execution_role=execution_role,
            task_role=task_role
        )

        # Add containers to the task definition in the specified order:
        # 1. video_processor, 2. frame_selector, 3. video_analyzer, 4. image_analyzer, 5. metadata_processor

        # 1. Video Processor Container
        video_processor_container = task_definition.add_container(
            "VideoProcessorContainer",
            image=ecs.ContainerImage.from_docker_image_asset(video_processor_image),
            essential=False,  # batch job that exits; required for SUCCESS dependency condition
            memory_limit_mib=8192,  # 8GB for ffmpeg video processing and frame extraction
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vcp-video-processor",
                log_group=log_group
            ),
            environment={
                "BUCKET_NAME": metadata_bucket.bucket_name,
                "VIDEO_S3_KEY": "value"
            }
        )

        # 2. Frame Selector Container
        frame_selector_container = task_definition.add_container(
            "FrameSelectorContainer",
            image=ecs.ContainerImage.from_docker_image_asset(frame_selector_image),
            essential=False,  # batch job that exits; required for SUCCESS dependency condition
            memory_limit_mib=2048,  # 2GB for pHash frame selection
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vcp-frame-selector",
                log_group=log_group
            ),
            environment={
                "BUCKET_NAME": metadata_bucket.bucket_name,
                "VIDEO_ID": "value",
                "PHASH_THRESHOLD": "0.70"
            }
        )

        # 3. Video Analyzer Container
        video_analyzer_container = task_definition.add_container(
            "VideoAnalyzerContainer",
            image=ecs.ContainerImage.from_docker_image_asset(video_analyzer_image),
            essential=False,  # batch job that exits alongside image_analyzer
            memory_limit_mib=2048,  # 2GB for Bedrock Data Automation
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vcp-video-analyzer",
                log_group=log_group
            ),
            environment={
                "BUCKET_NAME": metadata_bucket.bucket_name,
                "VIDEO_S3_KEY": "value"
            }
        )

        # 4. Image Analyzer Container
        image_analyzer_container = task_definition.add_container(
            "ImageAnalyzerContainer",
            image=ecs.ContainerImage.from_docker_image_asset(image_analyzer_image),
            essential=False,  # batch job that exits; required for SUCCESS dependency condition
            memory_limit_mib=2048,  # 2GB for Bedrock image analysis
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vcp-image-analyzer",
                log_group=log_group
            ),
            environment={
                "BUCKET_NAME": metadata_bucket.bucket_name,
                "VIDEO_S3_KEY": "value",
                "STATUS_TABLE_NAME": video_status_table.table_name
            }
        )

        # 5. Metadata Processor Container
        metadata_processor_container = task_definition.add_container(
            "MetadataProcessorContainer",
            image=ecs.ContainerImage.from_docker_image_asset(metadata_processor_image),
            essential=True,  # controlling container - task succeeds/fails with this one
            memory_limit_mib=2048,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vcp-metadata-processor",
                log_group=log_group
            ),
            environment={
                "INPUT_BUCKET_NAME": metadata_bucket.bucket_name,
                "VIDEO_ID": "value"
            }
        )

        # frame_selector starts after video_processor exits successfully
        frame_selector_container.add_container_dependencies(
            ecs.ContainerDependency(
                container=video_processor_container,
                condition=ecs.ContainerDependencyCondition.SUCCESS
            )
        )

        # video_analyzer and image_analyzer only start after frame_selector exits successfully
        video_analyzer_container.add_container_dependencies(
            ecs.ContainerDependency(
                container=frame_selector_container,
                condition=ecs.ContainerDependencyCondition.SUCCESS
            )
        )
        image_analyzer_container.add_container_dependencies(
            ecs.ContainerDependency(
                container=frame_selector_container,
                condition=ecs.ContainerDependencyCondition.SUCCESS
            )
        )

        # metadata_processor only starts after both video_analyzer and image_analyzer exit successfully
        metadata_processor_container.add_container_dependencies(
            ecs.ContainerDependency(
                container=video_analyzer_container,
                condition=ecs.ContainerDependencyCondition.SUCCESS
            )
        )
        metadata_processor_container.add_container_dependencies(
            ecs.ContainerDependency(
                container=image_analyzer_container,
                condition=ecs.ContainerDependencyCondition.SUCCESS
            )
        )

        CfnOutput(
            self, "VCPTaskDefinitionArn",
            value=task_definition.task_definition_arn
        )
        
        CfnOutput(
            self, "VPC-ID",
            value=vpc.vpc_id
        )
        
        CfnOutput(
            self, "VideoStatusTableName",
            value=video_status_table.table_name
        )
        
        CfnOutput(
            self, "VideoStatusTableStreamArn",
            value=video_status_table.table_stream_arn
        )
        
        # Lambda to trigger ECS task on S3 upload
        import os
        lambda_path = os.path.join(os.path.dirname(__file__), "..", "lambda", "trigger-generator")
        
        trigger_lambda = lambda_.Function(
            self, "TriggerGenerator",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="function.lambda_handler",
            code=lambda_.Code.from_asset(lambda_path),
            timeout=Duration.minutes(2),
            environment={
                "CLUSTER_NAME": cluster.cluster_name,
                "TASK_DEFINITION": task_definition.task_definition_arn,
                "SUBNETS": ",".join([subnet.subnet_id for subnet in vpc.private_subnets]),
                "SECURITY_GROUPS": ecs_task_sg.security_group_id,
                "BUCKET_NAME": metadata_bucket.bucket_name,
                "TABLE_NAME": video_status_table.table_name
            }
        )
        
        trigger_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:RunTask"],
            resources=[task_definition.task_definition_arn]
        ))
        
        trigger_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[execution_role.role_arn, task_role.role_arn]
        ))
        
        # S3 event notification
        metadata_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(trigger_lambda),
            s3.NotificationKeyFilter(prefix="input/", suffix=".mp4")
        )
        
        CfnOutput(
            self, "MetadataBucketName",
            value=metadata_bucket.bucket_name
        )