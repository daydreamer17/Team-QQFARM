from pathlib import Path


def test_docker_image_includes_policy_and_evaluation_assets() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY data/policies ./data/policies" in dockerfile
    assert "COPY data/examples/policy_rag ./data/examples/policy_rag" in dockerfile
    assert (
        "COPY evaluation/reference/policy_rag ./evaluation/reference/policy_rag"
        in dockerfile
    )
