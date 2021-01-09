import json
import pytest

from aws_cdk import core
from cdk-private-s3-hosting.cdk_private_s3_hosting_stack import CdkPrivateS3HostingStack


def get_template():
    app = core.App()
    CdkPrivateS3HostingStack(app, "cdk-private-s3-hosting")
    return json.dumps(app.synth().get_stack("cdk-private-s3-hosting").template)


def test_sqs_queue_created():
    assert("AWS::SQS::Queue" in get_template())


def test_sns_topic_created():
    assert("AWS::SNS::Topic" in get_template())
