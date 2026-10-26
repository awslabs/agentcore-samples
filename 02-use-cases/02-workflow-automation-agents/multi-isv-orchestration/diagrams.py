"""
Regenerate the multi-ISV orchestration architecture diagram as PNG.

Requirements:
    brew install graphviz
    pip install diagrams

Usage:
    python3 diagrams.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here in sys.path:
    sys.path.remove(_here)

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.security import Cognito, SecretsManager
from diagrams.custom import Custom
from diagrams.onprem.client import User

GRAPH = {
    "bgcolor": "white",
    "pad": "0.5",
    "fontsize": "13",
    "fontname": "Helvetica",
    "splines": "curved",
}
NODE = {"fontsize": "11", "fontname": "Helvetica"}
EDGE = {"fontsize": "9", "fontname": "Helvetica"}

_ICONS = os.path.join(_here, "icons")
ICON_RUNTIME = os.path.join(_ICONS, "agentcore-runtime.png")

_C_CLIENT = dict(bgcolor="#E0F2FE", style="rounded", pencolor="#0EA5E9", penwidth="2")
_C_GATEWAY = dict(bgcolor="#FAF5FF", style="rounded", pencolor="#A855F7", penwidth="3")
_C_TARGETS = dict(bgcolor="#FAF5FF", style="rounded", pencolor="#A855F7", penwidth="2", margin="28")
_C_IDENTITY = dict(bgcolor="#F0FDF4", style="rounded", pencolor="#16A34A", penwidth="2.5")
_C_OUTBOUND = dict(bgcolor="#FEF3C7", style="rounded", pencolor="#D97706", penwidth="2")
_C_ISV = dict(bgcolor="#FFF1F2", style="rounded", pencolor="#E11D48", penwidth="2")


def multi_isv_architecture():
    with Diagram(
        "",
        filename=os.path.join(_here, "images", "multi-isv-architecture"),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr={**GRAPH, "ranksep": "2.2", "nodesep": "1.2", "size": "26,18"},
        node_attr=NODE,
        edge_attr=EDGE,
    ):
        with Cluster("MCP Client / Agent", graph_attr=_C_CLIENT):
            agent = User("Strands Agent\nor MCP Client")

        with Cluster("Inbound Auth", graph_attr=_C_IDENTITY):
            cognito = Cognito("Amazon Cognito\nclient_credentials")

        with Cluster("Amazon Bedrock AgentCore Gateway", graph_attr=_C_GATEWAY):
            gw = Custom("MCP Gateway\nJSON-RPC 2.0\nJWT Authorizer", ICON_RUNTIME)

        with Cluster("Gateway Targets", graph_attr=_C_TARGETS):
            sf_target = Custom("Salesforce Target\nOpenAPI Schema\n43 tools", ICON_RUNTIME)
            sap_target = Custom("SAP Target\nMCP Server\n9 tools", ICON_RUNTIME)

        with Cluster("Outbound Auth", graph_attr=_C_OUTBOUND):
            sf_cred = SecretsManager("CustomOauth2\nSF Connected App")
            sap_cred = SecretsManager("CustomOauth2\nSAP Cognito Pool")

        with Cluster("ISV Platforms", graph_attr=_C_ISV):
            sf_platform = Custom("Salesforce Lightning\nREST API v62.0", ICON_RUNTIME)
            sap_platform = Custom("AWS for SAP\nMCP Server · OData V2", ICON_RUNTIME)

        agent >> Edge(style="dashed", color="#16A34A") >> cognito
        agent >> Edge(label="tools/list · tools/call", dir="both") >> gw
        gw >> Edge(dir="both") >> sf_target
        gw >> Edge(dir="both") >> sap_target
        sf_target >> Edge(style="dashed", color="#D97706") >> sf_cred
        sap_target >> Edge(style="dashed", color="#D97706") >> sap_cred
        sf_cred >> Edge(dir="both") >> sf_platform
        sap_cred >> Edge(dir="both") >> sap_platform


if __name__ == "__main__":
    os.makedirs(os.path.join(_here, "images"), exist_ok=True)
    print("Generating multi-isv-architecture.png ...")
    multi_isv_architecture()
    print("Done. Diagram saved to images/")
