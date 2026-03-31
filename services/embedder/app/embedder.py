from .models import EmbeddedNode, ParsedNode
from .providers.base import EmbeddingProvider


MAX_BODY_CHARS = 512


def build_embed_text(node: ParsedNode) -> str:
    parts = [node.name, node.signature]
    if node.docstring:
        parts.append(node.docstring)
    if node.body:
        # Include a truncated body snippet for richer semantic content.
        body = node.body[:MAX_BODY_CHARS]
        parts.append(body)
    return " ".join(parts)


async def embed_nodes(
    nodes: list[ParsedNode],
    neo4j_cache: dict[str, str],
    provider: EmbeddingProvider,
    batch_size: int = 64,
    embedding_dim: int = 0,
    file_path: str = "",
    language: str = "",
) -> list[EmbeddedNode]:
    """Embed nodes, skipping any whose node_hash already matches Neo4j.

    neo4j_cache: {stable_id: node_hash} for known nodes.
    embedding_dim: if > 0, validate that every returned vector has this length.
    """
    to_embed = [
        n for n in nodes
        if neo4j_cache.get(n.stable_id) != n.node_hash
    ]

    if not to_embed:
        return []

    embed_texts = [build_embed_text(n) for n in to_embed]

    vectors: list[list[float]] = []
    for i in range(0, len(embed_texts), batch_size):
        batch = embed_texts[i : i + batch_size]
        batch_vectors = await provider.embed(batch)
        if embedding_dim > 0:
            for vec in batch_vectors:
                if len(vec) != embedding_dim:
                    raise ValueError(
                        f"Expected vector dimension {embedding_dim}, got {len(vec)}"
                    )
        vectors.extend(batch_vectors)

    return [
        EmbeddedNode(
            stable_id=node.stable_id,
            vector=vector,
            embed_text=embed_texts[idx],
            node_hash=node.node_hash,
            name=node.name,
            type=node.type,
            file_path=file_path,
            language=language,
        )
        for idx, (node, vector) in enumerate(zip(to_embed, vectors))
    ]
