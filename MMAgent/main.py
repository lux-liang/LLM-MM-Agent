"""Command line entry point for the research pipeline."""

from __future__ import annotations

import argparse
import os
import time

from llm.llm import LLM
from utils.computational_solving import computational_solving
from utils.mathematical_modeling import mathematical_modeling
from utils.problem_analysis import problem_analysis
from utils.solution_reporting import generate_paper
from utils.utils import get_info, write_json_file


def run(
    key,
    problem_path,
    config,
    name,
    dataset_path,
    output_dir,
    *,
    api_base=None,
    reasoning_effort=None,
    generate_report=False,
):
    """Run the four-stage pipeline and return the generated solution."""
    llm = LLM(
        config["model_name"],
        key,
        api_base=api_base,
        reasoning_effort=reasoning_effort,
    )

    print("********************* Stage 1: Problem Analysis start *********************")
    problem, order, with_code, coordinator, task_descriptions, solution = problem_analysis(
        llm, problem_path, config, dataset_path, output_dir
    )
    print("********************* Stage 1: Problem Analysis finish *********************")

    print(
        "********************* Stage 2 & 3: Mathematical Modeling & "
        "Computational Solving start *********************"
    )
    for task_id in order:
        print(f"********************* Solving Task {task_id} *********************")
        (
            task_description,
            task_analysis,
            task_modeling_formulas,
            task_modeling_method,
            dependent_file_prompt,
        ) = mathematical_modeling(
            task_id,
            problem,
            task_descriptions,
            llm,
            config,
            coordinator,
            with_code,
        )
        solution = computational_solving(
            llm,
            coordinator,
            with_code,
            problem,
            task_id,
            task_description,
            task_analysis,
            task_modeling_formulas,
            task_modeling_method,
            dependent_file_prompt,
            config,
            solution,
            name,
            output_dir,
        )
    print(
        "********************* Stage 2 & 3: Mathematical Modeling & "
        "Computational Solving finish *********************"
    )

    if generate_report:
        print("********************* Stage 4: Solution Reporting start *********************")
        generate_paper(llm, output_dir, name)
        print("********************* Stage 4: Solution Reporting finish *********************")

    print(solution)
    usage = llm.get_total_usage()
    print("Usage:", usage)
    write_json_file(f"{output_dir}/usage/{name}.json", usage)
    return solution


def parse_arguments():
    parser = argparse.ArgumentParser(description="MM-Agent mathematical modeling pipeline")
    parser.add_argument(
        "--model_name",
        type=str,
        default=os.getenv("MMAGENT_MODEL_NAME", "gpt-5.6-sol"),
        help="Model id or alias: gpt-5.6-sol/gpt5.6sol or deepseek-v4-pro/deepseekv4pro",
    )
    parser.add_argument("--method_name", type=str, default="MM-Agent")
    parser.add_argument("--task", type=str, default="2024_C")
    parser.add_argument(
        "--key",
        type=str,
        default=os.getenv("MMAGENT_API_KEY") or None,
        help="Explicit key override; provider-specific environment variables are preferred",
    )
    parser.add_argument(
        "--api_base",
        type=str,
        default=os.getenv("MMAGENT_BASE_URL"),
        help="Optional OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--reasoning_effort",
        type=str,
        default=os.getenv("MMAGENT_REASONING_EFFORT", "high"),
        choices=["none", "low", "medium", "high", "xhigh", "max"],
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Run the optional Solution Reporting stage",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    problem_path, config, dataset_dir, output_dir = get_info(args)
    start = time.time()
    run(
        key=args.key,
        problem_path=problem_path,
        config=config,
        name=args.task,
        dataset_path=dataset_dir,
        output_dir=output_dir,
        api_base=args.api_base,
        reasoning_effort=args.reasoning_effort,
        generate_report=args.report,
    )
    elapsed = time.time() - start
    with open(output_dir + "/usage/runtime.txt", "w", encoding="utf-8") as f:
        f.write(f"{elapsed:.2f}s")
