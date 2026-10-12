"""Run one JCL job through the existing modernization phases.

The command-line entry point parses and classifies the job, decodes its
steps, generates and validates PySpark, then writes a diagram and migration
specification. Every invocation receives a new run ID so its files and
stored result can be inspected independently of earlier runs.
"""

import argparse
import json
import re
from uuid import uuid4
from pathlib import Path

from agent.memory.session_store import get_run, save_run
from agent.output.cross_step_contract import check_cross_step_contracts
from agent.output.diagram_generator import generate_mermaid_diagram
from agent.output.md_writer import assemble_migration_spec
from agent.parser.jcl_splitter import (
    detect_unsupported_jcl_constructs, load_jcl_from_file, split_jcl_into_steps,
)
from agent.parser.sort_control_validator import validate_sort_sum_layout
from agent.parser.utility_classifier import classify_steps


# Job identity and JSON output helpers.
def get_job_name(jcl_text, source_path):
    """Use the JCL JOB name, falling back to the input filename.

    The JOB card controls the output directory even when the JCL file has a
    different name. The fallback keeps malformed or partial inputs traceable.
    """
    match = re.search(r"^//([A-Za-z0-9@$#]+)\s+JOB\b", jcl_text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).upper() if match else Path(source_path).stem.upper()


def write_json(path, data):
    """Write an artifact in readable UTF-8 JSON, retaining non-ASCII text."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# One-job orchestration. The phase implementations remain in their own modules.
def run_job(jcl_path, cntl_folder="cntl", output_folder="outputs",
            decoder_factory=None, generator=None, validator=None):
    """Parse, decode, generate, validate, and assemble one migration spec.

    Injectable phase callables let the integration contract be tested without
    making external API calls. Production calls the existing phase functions.
    """
    # Create the run identity before touching the input. Even a setup failure
    # must have a distinct result that the caller can retrieve later.
    source = Path(jcl_path)
    run_id = uuid4().hex
    job_name = source.stem.upper()
    # This shape is the backend result contract consumed by the CLI and UI.
    # Start as failed so an early exception can never appear successful.
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
        "manual_steps": [],
        "validation_method": "model_review_and_static_checks",
        "runtime_verified": False,
        "cross_step_validation": {"status": "NOT_RUN", "issues": []},
        "validation_results": [],
        "artifacts": {},
        "code_paths": {},
    }
    database_path = Path(output_folder) / "sessions.db"

    def finish():
        """Write the final JSON result and index it by run ID in SQLite."""
        # Recompute the folder from the resolved JOB name. Before parsing,
        # result["job_name"] still holds the input filename fallback.
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
        # Keep a failed SQLite write inside the result contract. The run's
        # result.json remains usable as a lookup fallback in another checkout.
        try:
            save_run(result, database_path)
        except Exception as error:
            result["errors"].append(f"Could not index run in SQLite: {error}")
            result["status"] = "failed"
            if result["artifacts"].get("result"):
                try:
                    write_json(result_path, result)
                except Exception as write_error:
                    result["errors"].append(
                        f"Could not update run result after SQLite failure: {write_error}"
                    )
        return result

    try:
        # Parse the original JCL first; CNTL members are resolved by the
        # splitter, and the classifier supplies the utility type per step.
        jcl_text = load_jcl_from_file(str(source))
        job_cards = []
        unsupported = detect_unsupported_jcl_constructs(jcl_text, job_cards)
        if len(job_cards) > 1:
            raise ValueError(
                "This input contains multiple JOB cards; pass one JCL job per invocation"
            )
        job_name = job_cards[0].upper() if job_cards else source.stem.upper()
        result["job_name"] = job_name
        original_steps = classify_steps(
            split_jcl_into_steps(jcl_text, cntl_folder=str(cntl_folder))
        )
        if not original_steps:
            raise ValueError(f"No JCL steps found in {source}")
        # Reject a simple, known-invalid DFSORT layout before spending API
        # calls on decoding and generation.
        validate_sort_sum_layout(original_steps)
        unsupported.extend(
            f"{step['step_name']}: {item}"
            for step in original_steps
            for item in step.get("unsupported_constructs", [])
        )
        if unsupported:
            # Unsupported JCL must be visible as incomplete. Continuing with
            # partial control flow or unresolved symbols would produce a
            # plausible but potentially wrong migration specification.
            result["status"] = "incomplete"
            result["warnings"].extend(unsupported)
            result["manual_steps"] = [step["step_name"] for step in original_steps]
            result["step_statuses"] = [{
                "step_name": step["step_name"],
                "utility_type": step.get("utility_type", "UNKNOWN"),
                "decode_status": "not_run",
                "generation_status": "not_run",
                "validation_status": "not_run",
            } for step in original_steps]
            return finish()
        # Import the API-dependent modules only for a real run. Tests inject
        # lightweight phase functions so they can verify the handoffs offline.
        if decoder_factory is None:
            from agent.decoder.step_decoder import DecoderSession
            decoder_factory = DecoderSession
        session = decoder_factory()
        if not session.is_ready():
            raise RuntimeError("Decoder resources are not ready")
        # Positional alignment is required because each decoded result is
        # paired with its original step for code generation and validation.
        if hasattr(session, "decode_step"):
            # Decode independently so a RAG/API exception on one step cannot
            # erase the work and statuses for every other step in the job.
            decoded_steps = []
            for original in original_steps:
                try:
                    decoded_steps.append(session.decode_step(original))
                except Exception as error:
                    decoded_steps.append({
                        "step_name": original["step_name"],
                        "utility_type": original.get("utility_type", "UNKNOWN"),
                        "decode_status": "error",
                        "notes": f"Decoder raised {type(error).__name__}: {error}",
                        "input_files": [],
                        "output_files": [],
                    })
        else:
            # Older injected sessions expose only the aggregate interface.
            decoded_steps = session.decode_all_steps(original_steps)
        if len(decoded_steps) != len(original_steps):
            raise RuntimeError("Decoder did not return one result per JCL step")
        if any(original["step_name"] != decoded.get("step_name")
               for original, decoded in zip(original_steps, decoded_steps)):
            raise RuntimeError("Decoder step names do not match the parsed JCL")
    except Exception as error:
        result["errors"].append(f"Setup or decoding failed: {error}")
        if "original_steps" in locals() and not result["step_statuses"]:
            result["step_statuses"] = [{
                "step_name": step["step_name"],
                "utility_type": step.get("utility_type", "UNKNOWN"),
                "decode_status": "not_run",
                "generation_status": "not_run",
                "validation_status": "not_run",
            } for step in original_steps]
        return finish()

    # Generated code and reports live below this run's unique folder.
    output = Path(output_folder) / job_name / run_id
    code_folder = output / "pyspark"
    result["steps"] = decoded_steps
    validation_results = []
    code_paths = {}
    incomplete = False
    # Process steps in JCL order. Each status records how far that step got,
    # even if a later phase fails or an expected control member is missing.
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
        # An unresolved CNTL member leaves the utility's instructions unknown;
        # recording it as incomplete prevents a misleading successful run.
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
                incomplete = True
                result["manual_steps"].append(name)
                result["warnings"].append(
                    f"{name}: {utility} is documented but has no generated, validated implementation"
                )
                continue
            # PROGRAM and dummy IEBGENER steps are intentionally skipped.
            # Other skips are gaps; API/decode errors are failures.
            message = f"{name}: decode status {decode_status}: {decoded.get('notes', '')}"
            if decode_status == "skipped":
                incomplete = True
                result["warnings"].append(message)
            else:
                result["errors"].append(message)
            continue
        # Reuse the existing generator and validator; their results are
        # attached to this step rather than inferred from old output files.
        try:
            if generator is None:
                from agent.generator.pyspark_generator import generate_pyspark_code
                generator = generate_pyspark_code
            code_path, generation_status = generator(
                decoded, original, session.api_client, output_folder=str(code_folder)
            )
        except Exception as error:
            code_path, generation_status = None, f"error: {error}"
        step_status["generation_status"] = generation_status
        if generation_status != "success" or not code_path:
            result["errors"].append(f"{name}: generation failed ({generation_status})")
            continue
        generated_file = Path(code_path)
        if (not generated_file.is_file() or
                not generated_file.resolve().is_relative_to(code_folder.resolve())):
            step_status["generation_status"] = "invalid_artifact"
            result["errors"].append(
                f"{name}: generator did not create a file in this run's PySpark folder"
            )
            continue
        code_paths[name] = str(code_path)
        try:
            if validator is None:
                from agent.validator.self_correction_loop import run_correction_loop
                validator = run_correction_loop
            validation = validator(decoded, original, str(code_path), session.api_client)
        except Exception as error:
            validation = {
                "step_name": name,
                "validation_status": "ERROR",
                "issues_found": [str(error)],
            }
        validation_results.append(validation)
        step_status["validation_status"] = validation.get("validation_status", "ERROR")
        # REVIEW is an explicit handoff after correction retries. It permits
        # a completed run while preserving the need for a human decision.
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
        elif validation.get("issues_found"):
            result["warnings"].append(
                f"{name}: validator reported {len(validation['issues_found'])} "
                "advisory issue(s); see validation.json"
            )

    # The diagram and spec need both the original DD/control cards and the
    # decoded interpretation. Map validation by name for their per-step text.
    steps_data = [
        {"decoded": decoded, "original": original}
        for original, decoded in zip(original_steps, decoded_steps)
    ]
    validation_map = {item["step_name"]: item for item in validation_results}
    result["validation_results"] = validation_results
    result["code_paths"] = code_paths
    if code_paths:
        try:
            cross_step = check_cross_step_contracts(steps_data, code_paths)
        except Exception as error:
            cross_step = {"status": "ERROR", "issues": [{
                "code": "CHECK_ERROR", "severity": "error", "dataset": "",
                "contract": "cross_step", "steps": [],
                "message": f"Cross-step check failed: {error}",
            }]}
        result["cross_step_validation"] = cross_step
        for issue in cross_step["issues"]:
            message = issue["message"]
            if issue["severity"] == "error":
                result["errors"].append(message)
            else:
                result["warnings"].append(message)
        if cross_step["status"] == "UNVERIFIED":
            incomplete = True
    # Errors outrank incomplete work; REVIEW remains a successful run with
    # review_required=True as agreed by the backend result contract.
    result["status"] = (
        "failed" if result["errors"] else
        "incomplete" if incomplete else "success"
    )
    try:
        # Write every artifact into this run folder. No report from an older
        # invocation can be mistaken for a fresh validation result.
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
        write_json(paths["validation"], {
            "job_name": job_name,
            "steps": validation_results,
            "cross_step_validation": result["cross_step_validation"],
        })
        paths["diagram"].write_text(diagram, encoding="utf-8")
        paths["spec"].write_text(spec, encoding="utf-8")
        result["artifacts"] = {key: str(path) for key, path in paths.items()}
    except Exception as error:
        result["errors"].append(f"Could not write output artifacts: {error}")
        result["status"] = "failed"
    return finish()


# Command-line interface for conversion and stored-result lookup.
def main():
    """Parse CLI arguments, print the structured result, and set exit status."""
    parser = argparse.ArgumentParser(description="Convert a JCL job to a migration specification")
    parser.add_argument("jcl_path", nargs="?")
    parser.add_argument("--cntl-folder", default="cntl")
    parser.add_argument("--output-folder", default="outputs")
    parser.add_argument("--get-run", metavar="RUN_ID")
    args = parser.parse_args()
    # Lookup reads the SQLite index; it does not rerun decoding or generation.
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
    # Batch wrappers can stop or report a problem from the process exit code.
    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
