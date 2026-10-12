#!/usr/bin/env bash
# Convert every .jcl file in a folder, one job at a time.
#
# Run from any directory; paths passed without a leading slash are resolved
# relative to this repository. Activate the project virtual environment first
# so the selected Python executable has the agent's dependencies installed.
#
# Examples:
#   bash scripts/run_all_jcl.sh
#   bash scripts/run_all_jcl.sh --jcl-folder jcl --cntl-folder cntl
#   bash scripts/run_all_jcl.sh --jcl-folder /data/jobs --output-folder outputs
#
# Each agent.main invocation creates its own outputs/<job>/<run_id>/ folder.
# This wrapper records console output separately and continues after a failed
# or incomplete job. It exits nonzero if any job did not succeed.

set -u
set -o pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
jcl_arg=""
cntl_arg="cntl"
output_arg="outputs"
log_arg=""
python_cmd="python"

usage() {
    cat <<'USAGE'
Usage: bash scripts/run_all_jcl.sh [options]

Options:
  --jcl-folder PATH     Folder to scan recursively for .jcl files.
                        Defaults to jcl/.
  --cntl-folder PATH    Local CNTL member folder (default: cntl/).
  --output-folder PATH  Per-run artifacts and sessions.db (default: outputs/).
  --log-folder PATH     Batch logs; default: outputs/batch_logs/<batch-id>/.
  --python COMMAND      Python executable (default: python from the active venv).
  --help                Show this help.

Relative paths are resolved from the repository root.
USAGE
}

# Parse options explicitly so misspellings fail instead of silently processing
# a different set of files. A missing value is reported before any job runs.
while (($# > 0)); do
    case "$1" in
        --jcl-folder|--cntl-folder|--output-folder|--log-folder|--python)
            option="$1"
            if (($# < 2)) || [[ -z "$2" ]]; then
                printf 'Missing value for %s\n' "$option" >&2
                exit 2
            fi
            case "$option" in
                --jcl-folder) jcl_arg="$2" ;;
                --cntl-folder) cntl_arg="$2" ;;
                --output-folder) output_arg="$2" ;;
                --log-folder) log_arg="$2" ;;
                --python) python_cmd="$2" ;;
            esac
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# This sets jcl_arg to "jcl" only if it is unset or empty,
# otherwise it preserves the existing value.
if [[ -z "$jcl_arg" ]]; then
    jcl_arg="jcl"
fi

resolve_path() {
    if [[ "$1" = /* ]]; then
        printf '%s' "$1"
    else
        printf '%s/%s' "$project_root" "$1"
    fi
}

jcl_dir="$(resolve_path "$jcl_arg")"
cntl_dir="$(resolve_path "$cntl_arg")"
output_dir="$(resolve_path "$output_arg")"
batch_id="$(date -u +'%Y%m%dT%H%M%SZ')-$$"
if [[ -n "$log_arg" ]]; then
    log_dir="$(resolve_path "$log_arg")"
else
    log_dir="$output_dir/batch_logs/$batch_id"
fi

# Validate shared prerequisites once. Individual JCL failures are handled
# inside the loop; a missing input folder or Python executable is a batch
# setup error and should stop immediately.
if [[ ! -d "$jcl_dir" ]]; then
    printf 'JCL folder not found: %s\n' "$jcl_dir" >&2
    exit 2
fi
if [[ ! -d "$cntl_dir" ]]; then
    printf 'CNTL folder not found: %s\n' "$cntl_dir" >&2
    exit 2
fi
if ! command -v "$python_cmd" >/dev/null 2>&1; then
    printf 'Python executable not found: %s\n' "$python_cmd" >&2
    exit 2
fi

# Null delimiters preserve spaces in file names. Sorting makes the order
# repeatable. The array keeps counters in this shell rather than a pipeline
# subshell, so the final success/failure totals remain accurate.
mapfile -d '' -t jcl_files < <(
    find "$jcl_dir" -type f -iname '*.jcl' -print0 | sort -z
)
if ((${#jcl_files[@]} == 0)); then
    printf 'No .jcl files found in: %s\n' "$jcl_dir" >&2
    exit 2
fi
if ! mkdir -p -- "$log_dir"; then
    printf 'Could not create log folder: %s\n' "$log_dir" >&2
    exit 2
fi

# The tab-separated manifest maps each input JCL to its log and process exit
# code. The numbered log names remain unique even when different directories
# contain JCL files with the same base name.
summary="$log_dir/summary.tsv"
printf 'sequence\tjcl\texit_code\tlog\n' > "$summary"
total=0
succeeded=0
failed=0

cd -- "$project_root" || exit 2
for jcl in "${jcl_files[@]}"; do
    total=$((total + 1))
    printf -v sequence '%04d' "$total"
    log_file="$log_dir/$sequence.log"
    printf '[%d/%d] %s\n' "$total" "${#jcl_files[@]}" "$jcl"

    # A failed or incomplete run returns nonzero. Capture it and continue so
    # one bad JCL or missing CNTL member does not prevent the other jobs from
    # receiving their own run IDs and result files.
    if "$python_cmd" -m agent.main "$jcl" \
        --cntl-folder "$cntl_dir" \
        --output-folder "$output_dir" > "$log_file" 2>&1; then
        exit_code=0
        succeeded=$((succeeded + 1))
        printf '  SUCCESS  log: %s\n' "$log_file"
    else
        exit_code=$?
        failed=$((failed + 1))
        printf '  NON-SUCCESS (exit %d)  log: %s\n' "$exit_code" "$log_file"
    fi
    printf '%s\t%s\t%s\t%s\n' \
        "$sequence" "$jcl" "$exit_code" "$log_file" >> "$summary"
done

printf '\nCompleted: %d total, %d successful, %d non-successful\n' \
    "$total" "$succeeded" "$failed"
printf 'Batch manifest: %s\n' "$summary"
if ((failed > 0)); then
    exit 1
fi
