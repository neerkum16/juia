"""Run the existing JUIA phases for one JCL job."""

import argparse
import json
import re
from uuid import uuid4
from pathlib import Path

from agent.memory.session_store import get_run, save_run
from agent.output.diagram_generator import generate_mermaid_diagram
from agent.output.md_writer import assemble_migration_spec
from agent.parser.jcl_splitter import load_jcl_from_file, split_jcl_into_steps
from agent.parser.utility_classifier import classify_steps


def get_job_name(jcl_text, source_path):
    """Use the JCL JOB name, falling back to the input filename."""
    match = re.search(r"^//([A-Za-z0-9@$#]+)\s+JOB\b", jcl_text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).upper() if match else Path(source_path).stem.upper()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def run_job(jcl_path, cntl_folder="cntl", output_folder="outputs",
            decoder_factory=None, generator=None, validator=None):
    """Parse, decode, generate, validate, and assemble one migration spec.

    Injectable phase callables let the integration contract be tested without
    making external API calls. Production calls the existing phase functions.
    """
    source = Path(jcl_path)
    run_id = uuid4().hex
    job_name = source.stem.upper()
    result = {
        "run_id": run_id,
        "job_name": job_name,
        "status": "failed",
        "steps": [],
        "step_statuses": [],
        "errors": [],
        "warnings": [],
        "review_steps": [],
        "review_required": False,
        "validation_results": [],
        "artifacts": {},
        "code_paths": {},
    }
    database_path = Path(output_folder) / "sessions.db"

    def finish():
        run_folder = Path(output_folder) / result["job_name"] / run_id
        result_path = run_folder / "result.json"
        try:
            run_folder.mkdir(parents=True, exist_ok=True)
            result["artifacts"]["result"] = str(result_path)
            write_json(result_path, result)
        except Exception as error:
            result["artifacts"].pop("result", None)
            result["errors"].append(f"Could not write run result: {error}")
            result["status"] = "failed"
        save_run(result, database_path)
        return result

    try:
        jcl_text = load_jcl_from_file(str(source))
        job_name = get_job_name(jcl_text, source)
        result["job_name"] = job_name
        original_steps = classify_steps(
            split_jcl_into_steps(jcl_text, cntl_folder=str(cntl_folder))
        )
        if not original_steps:
            raise ValueError(f"No JCL steps found in {source}")
        if decoder_factory is None:
            from agent.decoder.step_decoder import DecoderSession
            decoder_factory = DecoderSession
        if generator is None:
            from agent.generator.pyspark_generator import generate_pyspark_code
            generator = generate_pyspark_code
        if validator is None:
            from agent.validator.self_correction_loop import run_correction_loop
            validator = run_correction_loop
        session = decoder_factory()
        if not session.is_ready():
            raise RuntimeError("Decoder resources are not ready")
        decoded_steps = session.decode_all_steps(original_steps)
        if len(decoded_steps) != len(original_steps):
            raise RuntimeError("Decoder did not return one result per JCL step")
        if any(original["step_name"] != decoded.get("step_name")
               for original, decoded in zip(original_steps, decoded_steps)):
            raise RuntimeError("Decoder step names do not match the parsed JCL")
    except Exception as error:
        result["errors"].append(f"Setup or decoding failed: {error}")
        return finish()

    output = Path(output_folder) / job_name / run_id
    code_folder = output / "pyspark"
    result["steps"] = decoded_steps
    validation_results = []
    code_paths = {}
    incomplete = False
    for original, decoded in zip(original_steps, decoded_steps):
        name = original["step_name"]
        utility = original.get("utility_type", "UNKNOWN")
        decode_status = decoded.get("decode_status", "unknown")
        step_status = {
            "step_name": name,
            "utility_type": utility,
            "decode_status": decode_status,
            "generation_status": "not_run",
            "validation_status": "not_run",
        }
        result["step_statuses"].append(step_status)
        if original.get("sysin_source") == "external_unresolved":
            incomplete = True
            result["warnings"].append(
                f"{name}: external SYSIN is unresolved ({original.get('sysin_dsn', '')})"
            )
            continue
        if decode_status != "success":
            if decode_status == "skipped" and (
                utility == "PROGRAM" or
                (utility == "IEBGENER" and original.get("sysin_source") == "dummy")
            ):
                continue
            message = f"{name}: decode status {decode_status}: {decoded.get('notes', '')}"
            if decode_status == "skipped":
                incomplete = True
                result["warnings"].append(message)
            else:
                result["errors"].append(message)
            continue
        try:
            code_path, generation_status = generator(
                decoded, original, session.api_client, output_folder=str(code_folder)
            )
        except Exception as error:
            code_path, generation_status = None, f"error: {error}"
        step_status["generation_status"] = generation_status
        if generation_status != "success" or not code_path:
            result["errors"].append(f"{name}: generation failed ({generation_status})")
            continue
        code_paths[name] = str(code_path)
        try:
            validation = validator(decoded, original, str(code_path), session.api_client)
        except Exception as error:
            validation = {
                "step_name": name,
                "validation_status": "ERROR",
                "issues_found": [str(error)],
            }
        validation_results.append(validation)
        step_status["validation_status"] = validation.get("validation_status", "ERROR")
        if step_status["validation_status"] == "REVIEW":
            result["review_steps"].append(name)
            result["review_required"] = True
            result["warnings"].append(
                f"{name}: human review required after correction retries"
            )
        elif step_status["validation_status"] != "PASS":
            result["errors"].append(
                f"{name}: validation {step_status['validation_status']}"
            )

    steps_data = [
        {"decoded": decoded, "original": original}
        for original, decoded in zip(original_steps, decoded_steps)
    ]
    validation_map = {item["step_name"]: item for item in validation_results}
    result["validation_results"] = validation_results
    result["code_paths"] = code_paths
    result["status"] = (
        "failed" if result["errors"] else
        "incomplete" if incomplete else "success"
    )
    try:
        diagram = generate_mermaid_diagram(job_name, steps_data)
        spec = assemble_migration_spec(
            job_name, source.name, steps_data, validation_map, diagram, str(code_folder)
        )
        output.mkdir(parents=True, exist_ok=True)
        paths = {
            "decoded": output / "decoded.json",
            "validation": output / "validation.json",
            "diagram": output / "lineage.mmd",
            "spec": output / "migration_spec.md",
        }
        write_json(paths["decoded"], {"job_name": job_name, "steps": decoded_steps})
        write_json(paths["validation"], {"job_name": job_name, "steps": validation_results})
        paths["diagram"].write_text(diagram, encoding="utf-8")
        paths["spec"].write_text(spec, encoding="utf-8")
        result["artifacts"] = {key: str(path) for key, path in paths.items()}
    except Exception as error:
        result["errors"].append(f"Could not write output artifacts: {error}")
        result["status"] = "failed"
    return finish()


def main():
    parser = argparse.ArgumentParser(description="Convert a JCL job to a migration specification")
    parser.add_argument("jcl_path", nargs="?")
    parser.add_argument("--cntl-folder", default="cntl")
    parser.add_argument("--output-folder", default="outputs")
    parser.add_argument("--get-run", metavar="RUN_ID")
    args = parser.parse_args()
    if args.get_run:
        result = get_run(args.get_run, Path(args.output_folder) / "sessions.db")
        if result is None:
            parser.error(f"Run ID not found: {args.get_run}")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not args.jcl_path:
        parser.error("jcl_path is required unless --get-run is used")
    result = run_job(args.jcl_path, args.cntl_folder, args.output_folder)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
