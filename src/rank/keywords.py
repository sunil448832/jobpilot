#!/usr/bin/env python3
"""
keywords.py — normalised, synonym-aware keyword matching.

A job description saying "Retrieval-Augmented Generation" and a resume saying
"RAG" are the same requirement, but a plain substring test scores them as a miss.
Every keyword here therefore expands to its aliases before matching:

  * acronym <-> expansion      RAG / retrieval-augmented generation
  * hyphen / space / camel     multi-agent / multi agent / multiagent
  * plural and gerund forms    embedding / embeddings, quantize / quantization
  * vendor <-> category        Qdrant / vector database

Used by rank.py to score a posting against Sunil's real skills. Every term is
TRUE for him — sourced from resume/sections/*.tex and project-memory-backup/.

    python jobs/keywords.py --test
    python jobs/keywords.py --check "we use RAG with a vector store"
"""
import argparse
import re
import os
import sys
from jobpilot.core.paths import CONFIG  # noqa: E402
import sys

# canonical term -> every form a JD might plausibly use for the SAME thing.
ALIASES = {
    # --- retrieval -------------------------------------------------------
    "rag": ["retrieval augmented generation", "retrieval-augmented generation",
            "retrieval augmentation", "grounded generation",
            # mined: "retrieval" alone had lift 4.6 across the RAG corpus
            "retrieval", "context retrieval", "knowledge retrieval",
            "document retrieval", "retrieval pipeline", "retrieval system"],
    "hybrid retrieval": ["hybrid search", "dense and sparse retrieval",
                         "sparse and dense", "dense + sparse", "hybrid ranking"],
    "vector database": ["vector store", "vector db", "vector index", "qdrant",
                        "faiss", "pinecone", "weaviate", "milvus", "embedding store"],
    "qdrant": ["vector database", "vector store"],
    "bm25": ["sparse retrieval", "lexical search", "keyword search", "bm42"],
    "colbert": ["late interaction", "multi vector retrieval"],
    "bge-m3": ["bge", "dense embeddings", "embedding model"],
    "semantic search": ["similarity search", "nearest neighbour search",
                        "nearest neighbor search", "knn search", "embedding search"],
    "reranking": ["re-ranking", "reranker", "cross encoder", "cross-encoder"],
    "beir": ["retrieval benchmark", "ir benchmark"],

    # --- LLM / GenAI -----------------------------------------------------
    "llm": ["large language model", "large language models", "llms",
            "foundation model", "foundation models", "frontier model"],
    "generative ai": ["genai", "gen ai", "generative artificial intelligence"],
    "agentic ai": ["ai agents", "agentic", "agent framework", "autonomous agents",
                   "agent orchestration", "tool calling", "tool use",
                   # mined: loops 4.8, tool 5.0, reasoning 3.3, orchestration 4.0
                   "agentic loops", "agent loops", "agentic workflows",
                   "agent workflows", "reasoning loops", "tool integration"],
    "multi-agent systems": ["multi agent", "multiagent", "agent to agent",
                            "a2a", "agent orchestration", "agent collaboration",
                            "orchestration layer", "agent handoff"],
    "prompt engineering": ["prompting", "prompt design", "few shot"],
    "prompt injection": ["jailbreak", "prompt attacks", "adversarial prompting"],
    "guardrails": ["safety guardrails", "content filtering", "output validation",
                   "responsible ai"],
    "model evaluation": ["evals", "eval harness", "evaluation harness",
                         "llm evaluation", "benchmarking", "llm as judge",
                         "llm-as-a-judge", "offline evaluation",
                         "evaluations", "eval framework", "quality evaluation",
                         "automated grading", "graders"],
    "text2sql": ["text-to-sql", "natural language to sql", "nl2sql", "sql generation"],
    "post-training": ["post training", "posttraining", "alignment", "sft",
                      "supervised fine-tuning", "instruction tuning", "rlhf",
                      "dpo", "ppo", "grpo", "rlaif", "preference optimization",
                      "preference optimisation", "reward model", "reward modeling",
                      "human feedback", "model alignment", "distillation",
                      "knowledge distillation", "continued pretraining"],
    "model compression": ["quantization", "quantisation", "pruning", "sparsification",
                          "distillation", "low precision", "int4", "int8", "fp8",
                          "gptq", "awq", "smoothquant", "compression"],
    "rlhf": ["reinforcement learning from human feedback", "reinforcement learning",
             "preference optimization", "dpo", "ppo", "grpo", "reward model",
             "reward modeling", "post-training", "alignment training",
             "human feedback", "preference tuning"],
    "customer-facing": ["client facing", "customer facing", "forward deployed",
                        "solutions engineering", "technical consulting",
                        "professional services", "customer engagement",
                        "stakeholder management", "trusted advisor",
                        "pre-sales", "post-sales", "deployment engineer"],

    # --- training / serving ---------------------------------------------
    "lora": ["low rank adaptation", "low-rank adaptation", "peft",
             "parameter efficient fine tuning", "adapter tuning"],
    "fine-tuning": ["finetuning", "fine tuning", "sft", "instruction tuning",
                    "supervised fine-tuning"],
    "deepspeed": ["zero-3", "zero3", "deepspeed zero", "fsdp",
                  "distributed training", "model parallel"],
    "distributed training": ["multi gpu", "multi-gpu", "data parallel", "fsdp",
                             "deepspeed"],
    "vllm": ["inference server", "model serving", "llm serving", "tensorrt",
             "triton", "tgi", "text generation inference",
             # mined: gpu 11.8, inference 4.0 across the vLLM corpus
             "gpu inference", "gpu serving", "inference stack", "serving stack"],
    "quantization": ["quantisation", "quantized", "quantised", "fp8", "int8",
                     "4-bit", "4 bit", "8-bit", "gptq", "awq", "bitsandbytes",
                     "low precision", "post-training quantization", "ptq",
                     "quantization aware training", "qat", "model compression",
                     "weight compression", "smoothquant", "turboquant"],
    "llm inference optimization": ["inference optimisation", "latency optimization",
                                   "throughput optimization", "kv cache",
                                   "batching", "serving optimization"],

    # --- multimodal / CV / NLP -------------------------------------------
    "computer vision": ["image understanding", "visual recognition",
                        "object detection", "image classification",
                        "image recognition"],
    "vision language model": ["vlm", "multimodal model", "multimodal",
                              "image-text", "clip", "llava"],
    "nlp": ["natural language processing", "text processing",
            "language understanding"],
    "document ai": ["document understanding", "document parsing", "ocr",
                    "textract", "docling", "pdf extraction", "document extraction"],
    "multilingual": ["translation", "cross-lingual", "mbart", "nllb",
                     "machine translation", "localization", "localisation"],

    # --- platform --------------------------------------------------------
    "python": ["python3"],
    "pytorch": ["torch"],
    "machine learning": ["ml", "statistical learning", "predictive modeling",
                         "predictive modelling"],
    "deep learning": ["neural networks", "neural network"],
    "aws": ["amazon web services", "ec2", "s3", "sagemaker", "bedrock",
            "athena", "ecs"],
    "production ml": ["ml in production", "productionize", "productionise",
                      "mlops", "ml platform", "model deployment",
                      "production systems", "prototype to production"],
    "fastapi": ["rest api", "api development", "microservice"],
    "duckdb": ["olap", "analytical database", "columnar"],
    "sql": ["postgres", "postgresql", "mysql", "athena", "queries"],
    "docker": ["containers", "containerization", "containerisation"],
    "observability": ["monitoring", "tracing", "telemetry", "logging",
                      "cloudwatch", "structlog", "opentelemetry", "alerting"],

    # --- backend (all evidenced in resume/sections or project-memory-backup) --
    "fastapi": ["rest api", "restful", "api development", "microservice",
                "microservices", "uvicorn", "asgi", "backend service",
                "web service", "http api", "async python", "asyncio"],
    "redis": ["cache", "caching", "in-memory store", "key-value store",
              "celery", "task queue", "message queue", "job queue", "broker",
              "background jobs", "async workers"],
    "postgresql": ["postgres", "psycopg", "relational database", "rdbms",
                   "mysql", "sqlite", "mssql", "sql server", "oracle db",
                   "transactional database"],
    "database": ["postgres", "postgresql", "mysql", "sqlite", "mssql", "mongodb",
                 "mongo", "nosql", "rdbms", "data store", "datastore",
                 "snowflake", "redshift", "bigquery", "athena", "duckdb"],
    "data pipeline": ["etl", "elt", "data engineering", "ingestion pipeline",
                      "batch processing", "streaming pipeline", "airflow",
                      "orchestration pipeline", "parquet", "data lake"],
    "pydantic": ["schema validation", "data validation", "type safety",
                 "typed models"],

    # --- infrastructure -----------------------------------------------------
    "docker": ["containers", "containerization", "containerisation",
               "containerized", "dockerfile", "oci image"],
    "kubernetes": ["k8s", "eks", "container orchestration", "helm", "fargate"],
    "terraform": ["infrastructure as code", "iac", "cloudformation", "pulumi",
                  "provisioning", "infrastructure automation"],
    "ci/cd": ["continuous integration", "continuous delivery", "continuous deployment",
              "github actions", "jenkins", "gitlab ci", "build pipeline",
              "deployment pipeline", "automated testing", "pytest", "ruff", "mypy"],
    "cloud infrastructure": ["ec2", "ecs", "ecr", "lambda", "s3", "iam", "sts",
                             "route53", "alb", "load balancer", "autoscaling",
                             "vpc", "serverless", "cloud deployment"],
    "sandboxing": ["bubblewrap", "isolation", "sandbox", "least privilege",
                   "scoped credentials", "egress control", "security hardening"],
}

_WORD = re.compile(r"[a-z0-9][a-z0-9+.#-]*")


def normalize(text):
    """Lowercase, collapse punctuation to spaces, keep +/#/. inside tokens.

    JDs are stored HTML-escaped, so "&lt;br&gt;" survives naive cleaning and shows
    up as the tokens "lt", "br", "gt". Unescape and strip tags first.
    """
    import html as _html
    t = _html.unescape(_html.unescape(text or ""))
    t = re.sub(r"<[^>]{0,200}>", " ", t)
    t = re.sub(r"&[a-z]{2,8};|&#\d{2,5};", " ", t)
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)
    t = t.lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[‐-―]", "-", t)          # unicode dashes -> hyphen
    t = re.sub(r"[^a-z0-9+.#\-\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def forms(term):
    """Every surface form of one term: itself, its aliases, and spacing/plural
    variants of each. Matching any one of these counts as a hit."""
    out = set()
    for base in [term] + ALIASES.get(term.lower(), []):
        b = normalize(base)
        if not b:
            continue
        variants = {b, b.replace("-", " "), b.replace("-", ""),
                    b.replace(" ", "-"), b.replace(" ", "")}
        for v in list(variants):
            if v.endswith("y"):
                variants.add(v[:-1] + "ies")
            if not v.endswith("s"):
                variants.add(v + "s")
            else:
                variants.add(v[:-1])
            # quantize/quantization, optimize/optimization
            if v.endswith("ization"):
                variants.add(v[:-7] + "ize")
                variants.add(v[:-7] + "isation")
            if v.endswith("ing") and len(v) > 6:
                variants.add(v[:-3])
        out |= {v for v in variants if len(v) > 1}
    return out


def hit(term, blob_norm):
    """Does this term (or any alias/variant) appear? Word-boundary anchored so
    'ml' does not match 'html' and 'cv' does not match 'cvs'."""
    for f in forms(term):
        if re.search(r"(?<![a-z0-9])" + re.escape(f) + r"(?![a-z0-9])", blob_norm):
            return True
    return False


def hits(terms, text):
    """Which of these terms appear in the text."""
    blob = normalize(text)
    return [t for t in terms if hit(t, blob)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--check")
    a = ap.parse_args()

    if a.check:
        import yaml, os
        t = yaml.safe_load(open(os.path.join(CONFIG, "targets.yaml")))
        allk = t["keywords"]["strong"] + t["keywords"]["strong_recent"]
        got = hits(allk, a.check)
        print(f"  {len(got)}/{len(allk)} matched: {', '.join(got) or '(none)'}")
        return

    cases = [
        ("RAG", "we build Retrieval-Augmented Generation pipelines"),
        ("RAG", "grounded generation over a corpus"),
        ("LoRA", "experience with parameter efficient fine tuning"),
        ("DeepSpeed", "distributed training with FSDP across nodes"),
        ("Qdrant", "you will work with a vector store"),
        ("hybrid retrieval", "dense and sparse retrieval combined"),
        ("Multi-Agent Systems", "agent-to-agent orchestration"),
        ("Quantization", "FP8 and 4-bit inference"),
        ("Model Evaluation", "building eval harnesses and LLM-as-a-judge"),
        ("Document AI", "parsing PDFs with Textract and Docling"),
        ("Multilingual", "NLLB machine translation across languages"),
        ("Generative AI", "GenAI applications at scale"),
        ("Machine Learning", "the html of the page"),          # must NOT match
        ("Computer Vision", "we sell CVs to recruiters"),      # must NOT match
    ]
    print("  term                     matched  text")
    for term, text in cases:
        ok = hit(term, normalize(text))
        print(f"  {term:<24} {'YES ' if ok else 'no  '}     {text[:52]}")


if __name__ == "__main__":
    main()
