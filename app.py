#!/usr/bin/env python3

from aws_cdk import core

from cdk_private_s3_hosting.cdk_private_s3_hosting_stack import CdkPrivateS3HostingStack


app = core.App()
CdkPrivateS3HostingStack(app, "cdk-private-s3-hosting", env={'region': 'eu-west-1'})

app.synth()
