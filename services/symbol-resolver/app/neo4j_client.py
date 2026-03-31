# services/symbol-resolver/app/neo4j_client.py
import os
from neo4j import GraphDatabase


def make_driver():
    url = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(url, auth=(user, password))
