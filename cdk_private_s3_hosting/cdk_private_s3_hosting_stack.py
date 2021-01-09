from aws_cdk import (
    aws_iam as iam,
    core,
    aws_s3 as s3,
    aws_s3_deployment as s3dep,
    aws_apigateway as apigw,
)

from aws_cdk.aws_ec2 import BastionHostLinux, InstanceType, AmazonLinuxImage, \
    SubnetSelection, SecurityGroup, SubnetType, InterfaceVpcEndpoint, \
    Vpc, Subnet, Peer, Port, CfnVPCPeeringConnection, CfnRoute, \
    IInterfaceVpcEndpointService, InterfaceVpcEndpointAwsService

class VpcPeeringHelper(core.Construct):

    def __init__(self, scope: core.Construct, id: str, client_vpc, peer_vpc, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        vpc_peering = CfnVPCPeeringConnection (self, id,
          vpc_id=client_vpc.vpc_id,
          peer_vpc_id=peer_vpc.vpc_id
          )
        route = 1
        for (vpc1, vpc2) in [(client_vpc, peer_vpc), (peer_vpc, client_vpc)]:
          for subnet in vpc1.private_subnets:
            CfnRoute(self, 'Route-%s-%d' % (id, route),
              route_table_id= subnet.route_table.route_table_id,
              destination_cidr_block= vpc2.vpc_cidr_block,
              vpc_peering_connection_id= vpc_peering.ref )
            route += 1

class CdkPrivateS3HostingStack(core.Stack):

    def __init__(self, scope: core.Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create two VPCs - one to host our private website, the other to act as a client
        website_vpc = Vpc(self, "WEBSITEVPC",
          cidr="10.0.0.0/16",
          )
        client_vpc = Vpc(self, "ClientVPC",
          cidr="10.1.0.0/16",
        )
        
        # Create a bastion host in the client API which will act like our client workstation
        bastion = BastionHostLinux(
          self, "WEBClient",
          vpc=client_vpc,
          instance_name='my-bastion',
          instance_type=InstanceType('t3.micro'),
          machine_image=AmazonLinuxImage(),
          subnet_selection=SubnetSelection(subnet_type=SubnetType.PRIVATE),
          security_group=SecurityGroup(
            scope=self,
            id='bastion-sg',
            security_group_name='bastion-sg',
            description='Security group for the bastion, no inbound open because we should access'
                        ' to the bastion via AWS SSM',
            vpc=client_vpc,
            allow_all_outbound=True
          )
        )

        # Set up a VPC peering connection between client and API VPCs, and adjust
        # the routing table to allow connections back and forth
        VpcPeeringHelper(self, 'Peering', website_vpc, client_vpc)

        # Create VPC endpoints for API gateway        
        vpc_endpoint = InterfaceVpcEndpoint(self, 'APIGWVpcEndpoint',
          vpc=website_vpc,
          service=InterfaceVpcEndpointAwsService.APIGATEWAY,
          private_dns_enabled=True,
        )
        vpc_endpoint.connections.allow_from(bastion, Port.tcp(443))
        endpoint_id = vpc_endpoint.vpc_endpoint_id

        api_policy = iam.PolicyDocument(
            statements= [
              iam.PolicyStatement(
                principals= [iam.AnyPrincipal()],
                actions= ['execute-api:Invoke'],
                resources= ['execute-api:/*'],
                effect= iam.Effect.DENY,
                conditions= {
                  "StringNotEquals": {
                    "aws:SourceVpce": endpoint_id
                  }
                }
              ),
              iam.PolicyStatement(
                principals= [iam.AnyPrincipal()],
                actions= ['execute-api:Invoke'],
                resources= ['execute-api:/*'],
                effect= iam.Effect.ALLOW
              )
            ]
          )

        # Create an s3 bucket to hold the content
        content_bucket = s3.Bucket(self, "ContentBucket",
            removal_policy=core.RemovalPolicy.DESTROY)

        # Upload our static content to the bucket
        s3dep.BucketDeployment(self, "DeployWithInvalidation",
            sources=[s3dep.Source.asset('website')],
            destination_bucket=content_bucket)
        
        # Create a private API GW in the API VPC
        api = apigw.RestApi(self, 'PrivateS3Api', 
          endpoint_configuration=apigw.EndpointConfiguration(
            types = [apigw.EndpointType.PRIVATE],
                     vpc_endpoints = [vpc_endpoint]),
          policy= api_policy
        )

        # Create a role to allow API GW to access our S3 bucket contents
        role = iam.Role(self, "Role",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"))
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            resources=[
              content_bucket.bucket_arn,
              content_bucket.bucket_arn+'/*'],
            actions=["s3:Get*"]))

        # Create a proxy resource that captures all non-root resource requests
        resource = api.root.add_resource("{proxy+}")
        # Create an integration with S3
        resource_integration = apigw.Integration(
            type=apigw.IntegrationType.AWS,
            integration_http_method='GET',
            options=apigw.IntegrationOptions(
                request_parameters={ # map the proxy parameter so we can pass the request path
                  "integration.request.path.proxy": "method.request.path.proxy"
                },
                integration_responses=[
                    apigw.IntegrationResponse(
                        status_code='200',
                        response_parameters={ # map the content type of the S3 object back to the HTTP response
                            "method.response.header.Content-Type": "integration.response.header.Content-Type"
                            }
                        )
                    
                    ],
                credentials_role=role
                ),
            # reference the bucket content we want to retrieve
            uri='arn:aws:apigateway:eu-west-1:s3:path/%s/{proxy}' %
                (content_bucket.bucket_name))
        # handle the GET request and map it to our new integration
        resource.add_method("GET", resource_integration,
          method_responses=[
            apigw.MethodResponse(
              status_code='200',
              response_parameters={
                "method.response.header.Content-Type": False
              }
            )
          ],
          request_parameters={
            "method.request.path.proxy": True
            }
          )
        # Handle requests to the root of our site
        # Create another integration with S3 - this time with no proxy parameter
        resource_integration = apigw.Integration(
            type=apigw.IntegrationType.AWS,
            integration_http_method='GET',
            options=apigw.IntegrationOptions(
                integration_responses=[
                    apigw.IntegrationResponse(
                        status_code='200',
                        response_parameters={ # map the content type of the S3 object back to the HTTP response
                            "method.response.header.Content-Type": "integration.response.header.Content-Type"
                            }
                        )
                    
                    ],
                credentials_role=role
                ),
            # reference the bucket content we want to retrieve
            uri='arn:aws:apigateway:eu-west-1:s3:path/%s/index.html' %
                (content_bucket.bucket_name))
        # handle the GET request and map it to our new integration
        api.root.add_method("GET", resource_integration,
          method_responses=[
            apigw.MethodResponse(
              status_code='200',
              response_parameters={
                "method.response.header.Content-Type": False
              }
            )
          ]
          )
        