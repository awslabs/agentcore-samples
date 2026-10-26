"""Event-Driven Claims Agent — high-level architecture (AWS icons via mingrammer diagrams).

Source of truth for the app-root architecture.png. Regenerate with:
    uv run --with diagrams python3 docs/diagrams/architecture.py
(run from the event-driven-claims-agent/ directory)
"""

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import DynamodbTable
from diagrams.aws.engagement import SimpleEmailServiceSes
from diagrams.aws.general import Client, User
from diagrams.aws.integration import Eventbridge, SimpleNotificationServiceSnsTopic
from diagrams.aws.management import Cloudwatch
from diagrams.aws.ml import Bedrock
from diagrams.aws.security import Cognito
from diagrams.aws.storage import SimpleStorageServiceS3Bucket
from diagrams.custom import Custom

ICONS = os.path.join(os.path.dirname(__file__), "icons")
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(APP_ROOT, "architecture")


def ac(label, icon):
    return Custom(label, os.path.join(ICONS, icon), **NODE)


NODE = {
    "width": "1.05",
    "height": "1.35",
    "fixedsize": "true",
    "imagescale": "true",
    "imagepos": "tc",
    "labelloc": "b",
    "shape": "box",
    "style": "rounded,filled",
    "fillcolor": "white",
    "color": "#cccccc",
    "penwidth": "1.0",
    "fontsize": "8",
    "margin": "0.06,0.04",
}

graph_attr = {
    "fontsize": "15",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "ortho",
    "bgcolor": "transparent",
    "nodesep": "0.30",
    "ranksep": "1.5",
    "size": "13.33,7.5",
    "dpi": "200",
}

edge = Edge(color="#555555")
dashed = Edge(color="#8e44ad", style="dashed")
dotted = Edge(color="#888888", style="dotted")

with Diagram(
    "Event-Driven Claims Agent — Architecture",
    filename=OUT,
    show=False,
    direction="LR",
    outformat=["png"],
    graph_attr=graph_attr,
    node_attr={"fontname": "Helvetica"},
):
    with Cluster("Ingress", graph_attr={"bgcolor": "#fff3e0"}):
        claimant = User("Claimant", **NODE)
        inbox = SimpleStorageServiceS3Bucket("Claims Inbox\n(S3)", **NODE)
        eb = Eventbridge("EventBridge\nRule", **NODE)
        trigger = Lambda("Trigger\nLambda", **NODE)
        caller = Client("Direct API\nCaller", **NODE)

    with Cluster("Amazon Bedrock AgentCore", graph_attr={"bgcolor": "#e8f4fd"}):
        runtime = ac("Runtime\n(Dual-Agent)", "AgentCoreRuntime.png")
        gateway = ac("Gateway\n(MCP)", "AgentCoreGateway.png")
        policy = ac("Policy Engine\n(Cedar)", "AgentCore.png")
        memory = ac("Memory\n(Sem.+Summ.)", "AgentCoreMemory.png")
        identity = ac("Identity\n(Token Vault)", "AgentCoreIdentity.png")
        online_eval = ac("Online Eval\n(LLM Judge)", "AgentCore.png")
        bedrock = Bedrock("Bedrock\nSonnet + Haiku", **NODE)

    with Cluster("Auth", graph_attr={"bgcolor": "#fce4ec"}):
        cognito = Cognito("Cognito\n(M2M JWT)", **NODE)

    with Cluster("Tools & Data", graph_attr={"bgcolor": "#f0f9e8"}):
        tools = Lambda("6 Tool\nLambdas", **NODE)
        ddb = DynamodbTable("DynamoDB\nPolicies/Claims/Reviews", **NODE)
        sns = SimpleNotificationServiceSnsTopic("Human Review\n(SNS)", **NODE)
        ses_out = SimpleEmailServiceSes("Notifications\n(SES)", **NODE)

    with Cluster("Observability", graph_attr={"bgcolor": "#f3e5f5"}):
        cw = Cloudwatch("CloudWatch\n+ X-Ray")

    # Ingress flow
    claimant >> Edge(label="email / upload", **edge.attrs) >> inbox
    inbox >> edge >> eb >> Edge(**edge.attrs) >> trigger
    trigger >> Edge(label="SigV4", **edge.attrs) >> runtime
    caller >> Edge(label="SigV4", **edge.attrs) >> runtime

    # Runtime interactions
    runtime >> Edge(label="inference", **edge.attrs) >> bedrock
    runtime >> Edge(label="MCP / JWT", **edge.attrs) >> gateway
    runtime >> Edge(label="token", **dashed.attrs) >> identity
    identity >> dashed >> cognito
    cognito >> Edge(label="OIDC", **dashed.attrs) >> gateway
    runtime >> Edge(label="enrich / record", **dashed.attrs) >> memory
    runtime >> dotted >> cw
    runtime >> dotted >> online_eval

    # Gateway → tools → data
    gateway >> Edge(label="Cedar", **dashed.attrs) >> policy
    gateway >> edge >> tools
    tools >> edge >> ddb
    tools >> edge >> sns
    tools >> edge >> ses_out
